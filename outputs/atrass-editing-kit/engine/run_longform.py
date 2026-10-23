#!/usr/bin/env python3
"""Run a source-bound longform project through render, review and handoff.

Accepts an agent's explicit project, edit plan, or prepared job + decisions.
It does not infer semantics or certify style from a successful encode.
Every completed stage is hash-checked on resume; partial attempts are preserved.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from review_movie import require, save, sha, verify_sidecar

ENGINE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def job_lock(directory):
    """OS releases the lock on exit/crash; no stale writer is forcibly unlocked."""
    path = Path(directory)/'.execution.lock'
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('Another process is already running this job') from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('Another process is already running this job') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def binding(path):
    p = Path(path).resolve(strict=True)
    require(p.is_file(), 'Input is not a file: '+str(p))
    return {'path': str(p), 'sha256': sha(p), 'bytes': p.stat().st_size}


def verify_bindings(rows):
    for row in rows:
        p = Path(row['path'])
        require(p.is_file() and p.stat().st_size == row['bytes'] and sha(p) == row['sha256'],
                'Bound input/output changed: '+str(p))


def project_bindings(path, raw_sha):
    path = Path(path).resolve(strict=True)
    p = read(path)
    require(p.get('schema') == 'atrass-render-v1', 'Compiled render project required')
    require(p.get('variant') in ('A', 'B'), 'Explicit A or B style required')
    rows = {str(path): binding(path)}
    actual_raw = []
    for group in ('media', 'fonts'):
        for key, item in p.get(group, {}).items():
            role = item.get('role', '')
            require(role not in ('reference', 'reference_video', 'finished_reference'), 'Finished reference media cannot enter output')
            resource = (path.parent/item['path']).resolve(strict=True)
            row = rows.setdefault(str(resource), binding(resource))
            if item.get('sha256') is not None:
                require(item['sha256'] == row['sha256'], 'Resource declared SHA mismatch: '+key)
            if role == 'source_footage' and row['sha256'] == raw_sha:
                actual_raw.append(key)
            for field, digest_field in [('provenance','provenance_sha256'), ('source_asset_path','source_asset_sha256')]:
                if digest_field in item:
                    source = (path.parent/item[field]).resolve(strict=True)
                    r = rows.setdefault(str(source), binding(source))
                    require(item[digest_field] == r['sha256'], 'Asset provenance SHA mismatch: '+key)
    require(actual_raw, 'Project is not bound to the supplied raw footage')
    used = {item['source'] for item in p['clips']} | {item['source'] for item in p.get('audio', [])}
    require(set(actual_raw) & used, 'Supplied raw footage is declared but unused')
    return list(rows.values())


def stage(root, name, inputs, action):
    verify_bindings(inputs)
    folder = root/name
    folder.mkdir(exist_ok=True)
    receipts = sorted(folder.glob('attempt-*/complete.json'))
    if receipts:
        require(len(receipts) == 1, 'Ambiguous completed attempts: '+name)
        receipt = read(receipts[0])
        require(receipt['inputs'] == inputs, 'Stage inputs differ from completed work: '+name)
        verify_bindings(receipt['outputs'])
        return receipt['result']
    attempt = folder/f'attempt-{len(list(folder.glob("attempt-*")))+1:03d}'
    attempt.mkdir()
    save(attempt/'started.json', {'at_utc': now(), 'pid': os.getpid(), 'inputs': inputs})
    print(json.dumps({'stage': name, 'state': 'running', 'attempt': str(attempt)}, ensure_ascii=False), flush=True)
    started = time.monotonic()
    try:
        result, files = action(attempt)
        verify_bindings(inputs)
        outputs = [binding(p) for p in sorted(set(map(Path, files)))]
        save(attempt/'complete.json', {'at_utc': now(), 'elapsed_seconds': time.monotonic()-started,
             'inputs': inputs, 'outputs': outputs, 'result': result, 'release_ready': False})
        print(json.dumps({'stage': name, 'state': 'complete', 'elapsed_seconds': round(time.monotonic()-started, 2)}, ensure_ascii=False), flush=True)
        return result
    except Exception as exc:
        save(attempt/'failed.json', {'at_utc': now(), 'error': str(exc), 'type': type(exc).__name__, 'release_ready': False})
        raise


def completed_outputs(root, name):
    """Use the closed stage inventory, excluding later caches or review files."""
    receipts = list((root/name).glob('attempt-*/complete.json'))
    require(len(receipts) == 1, 'One completed stage required: '+name)
    rows = read(receipts[0])['outputs']
    verify_bindings(rows)
    return rows


def command(args, attempt, timeout=14400):
    with (attempt/'stdout.txt').open('x') as out, (attempt/'stderr.txt').open('x') as err:
        run = subprocess.run(list(map(str, args)), stdout=out, stderr=err, timeout=timeout)
    require(run.returncode == 0, 'Step failed; see '+str(attempt/'stderr.txt'))


def run(root):
    config = read(root/'job-config.json')
    require(config.get('schema') == 'atrass-longform-run-v1', 'Unsupported job config')
    verify_bindings(config['inputs'])
    raw_sha = config['raw']['sha256']

    def compile_project(attempt):
        if config['mode'] == 'recipe':
            from compose_source_style import compose
            composed = compose(config['input']['path'], raw_sha, root/'style-composition')
            p = Path(composed['project'])
        elif config['mode'] == 'project':
            p = Path(config['input']['path'])
        else:
            if config['mode'] == 'job':
                from assemble_edit_plan import assemble
                plan = attempt/'edit-plan.json'
                assemble(config['input']['path'], config['decisions']['path'], plan)
            else:
                plan = Path(config['input']['path'])
            from compile_plan import compile_plan
            p = attempt/'project.json'
            compile_plan(plan, p)
        resources = project_bindings(p, raw_sha)
        save(attempt/'project-inputs.json', {'bindings': resources})
        return {'project': str(p), 'bindings': resources}, [Path(row['path']) for row in resources]+[attempt/'project-inputs.json']

    compiled = stage(root, '01-project', config['inputs'], compile_project)
    project = Path(compiled['project'])
    frozen_inputs = compiled['bindings'] + config['inputs']

    def delivery(attempt):
        from export_delivery import export, verify
        from relink_by_identity import relink_by_identity
        folder = attempt/'runtime'
        export(project, folder)
        sources = {}
        for item in read(project)['media'].values():
            if item.get('role') == 'source_footage' or item.get('type') != 'image':
                path = (project.parent/item['path']).resolve(strict=True)
                sources[item['sha256']] = str(path)
        linked = folder/'project-linked.json'
        relink_by_identity(folder/'project.json', linked, sources)
        verify(folder)
        return {'runtime': str(folder), 'project': str(linked)}, [p for p in folder.rglob('*') if p.is_file()]

    portable = stage(root, '02-runtime', frozen_inputs, delivery)
    # Rendering imports this runtime and creates __pycache__. Those new files
    # must not change the inventory of the already completed delivery stage.
    runtime_inputs = completed_outputs(root, '02-runtime')

    def render(attempt):
        if config.get('adopt'):
            movie = Path(config['adopt']['path'])
            sidecar = Path(config['adopt_sidecar']['path'])
            rendered_project = project
        else:
            rendered_project = Path(portable['project'])
            movie = attempt/'longform.mp4'
            command([sys.executable, Path(portable['runtime'])/'engine/render.py', rendered_project,
                     '--output', movie, '--draft', '--ffmpeg', config['ffmpeg']['path']], attempt)
            sidecar = movie.with_suffix('.render.json')
        verify_sidecar(rendered_project, movie, sidecar)
        save(attempt/'render-result.json', {'movie': str(movie), 'sidecar': str(sidecar), 'project': str(rendered_project),
             'adopted_closed_output': bool(config.get('adopt')), 'release_ready': False})
        return read(attempt/'render-result.json'), [movie, sidecar, attempt/'render-result.json']

    rendered = stage(root, '03-render', frozen_inputs+runtime_inputs, render)

    def inspect(attempt):
        from review_movie import review
        folder = attempt/'review'
        result = review(rendered['project'], rendered['movie'], rendered['sidecar'], folder, config['ffmpeg']['path'])
        return {'directory': str(folder), **result}, [p for p in folder.rglob('*') if p.is_file()]

    inspected = stage(root, '04-review', frozen_inputs+runtime_inputs+[binding(rendered['movie']), binding(rendered['sidecar'])], inspect)
    result = {'schema': 'atrass-longform-run-result-v1', 'status': 'rendered_and_review_material_prepared',
              'movie': rendered['movie'], 'movie_sha256': sha(rendered['movie']), 'review': inspected,
              'runtime': portable['runtime'], 'raw_sha256': raw_sha, 'release_ready': False,
              'next': 'Agent must inspect actual output, compare style/reference, review speech and sound, record decisions and fix concrete defects. Successful rendering is not longform completion. Shorts remain gated by references/work-queue.json.',
              'full_temporal_review': False, 'direct_listening_seconds': 0, 'company_execution_verified': False}
    final = root/'result.json'
    if final.exists():
        require(read(final) == result, 'Final result differs from completed receipts')
    else:
        save(final, result)
    return result


def initialize(args):
    root = Path(args.output_dir).resolve()
    require(not root.exists(), 'Choose a new job directory, or use resume')
    raw = binding(args.raw)
    source = binding(args.project or args.plan or args.job or args.recipe)
    mode = 'project' if args.project else 'plan' if args.plan else 'job' if args.job else 'recipe'
    require(bool(args.decisions) == (mode == 'job'), '--decisions is required only with --job')
    require(not args.adopt_output or mode == 'project', 'Adoption requires the exact original --project')
    ffmpeg = shutil.which(args.ffmpeg) or args.ffmpeg
    config = {'schema': 'atrass-longform-run-v1', 'created_at_utc': now(), 'mode': mode,
              'raw': raw, 'input': source, 'ffmpeg': binding(ffmpeg), 'release_ready': False}
    inputs = [raw, source, config['ffmpeg']]
    # A resumed job cannot silently switch its executing implementation.
    inputs.extend(binding(p) for p in sorted(ENGINE.glob('*.py')))
    if args.decisions:
        config['decisions'] = binding(args.decisions); inputs.append(config['decisions'])
        job = read(source['path'])
        require(job['raw']['sha256'] == raw['sha256'], 'Prepared job belongs to another raw source')
        inputs.append(binding(Path(source['path']).parent/job['files']['transcript']))
    if mode == 'project':
        inputs.extend(project_bindings(source['path'], raw['sha256']))
    if mode == 'recipe':
        from compose_source_style import inspect_recipe
        _, resources = inspect_recipe(source['path'], raw['sha256'])
        inputs.extend(resources)
    if args.adopt_output:
        config['adopt'] = binding(args.adopt_output)
        config['adopt_sidecar'] = binding(Path(args.adopt_output).with_suffix('.render.json'))
        verify_sidecar(source['path'], config['adopt']['path'], config['adopt_sidecar']['path'])
        inputs.extend([config['adopt'], config['adopt_sidecar']])
    config['inputs'] = list({row['path']: row for row in inputs}.values())
    root.mkdir(parents=True)
    save(root/'job-config.json', config)
    return root


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='action', required=True)
    start = sub.add_parser('start')
    start.add_argument('--raw', required=True)
    group = start.add_mutually_exclusive_group(required=True)
    for mode in ('project', 'plan', 'job', 'recipe'):
        group.add_argument('--'+mode)
    start.add_argument('--decisions')
    start.add_argument('--ffmpeg', default='ffmpeg')
    start.add_argument('--output-dir', required=True)
    start.add_argument('--adopt-output', help='Adopt a closed full render with its matching .render.json sidecar')
    for name in ('resume', 'status'):
        sub.add_parser(name).add_argument('directory')
    a = ap.parse_args()
    try:
        root = initialize(a) if a.action == 'start' else Path(a.directory).resolve(strict=True)
        if a.action == 'status':
            result = read(root/'result.json') if (root/'result.json').exists() else {'status': 'incomplete',
                     'completed_stages': [str(p.relative_to(root)) for p in sorted(root.glob('*/attempt-*/complete.json'))], 'release_ready': False}
        else:
            with job_lock(root):
                result = run(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
