#!/usr/bin/env python3
"""Route raw bytes to an existing edit recipe, or an explicit new-source handoff.

This removes recipe selection from the known-source workflow. It does NOT infer
new editorial decisions. Unknown recordings never inherit a matching filename's
cuts. Optional ASR preparation continues that branch without claiming completion.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse
import json
import re
import shutil
import sys

from run_longform import (binding, command, initialize as initialize_pipeline,
                         job_lock, read, run, save, sha, stage, verify_bindings)
from review_movie import require

KIT = Path(__file__).resolve().parents[1]
SCHEMA = 'atrass-raw-start-v1'


def publish(path, data):
    if path.exists():
        require(read(path) == data, 'Completed raw-start result changed')
    else:
        save(path, data)


def select_recipe(catalog, raw_sha, variant):
    """Catalogue is data, never an executable command or a filename heuristic."""
    catalog = Path(catalog).resolve(strict=True)
    spec = read(catalog)
    require(spec.get('schema') == 'atrass-source-catalog-v1', 'Unsupported source catalogue')
    require(isinstance(spec.get('entries'), list), 'Catalogue entries must be an array')
    package = catalog.parent.parent
    matches, identities = [], set()
    for entry in spec['entries']:
        require(set(entry) == {'id', 'variant', 'source_sha256', 'recipe'}, 'Invalid catalogue fields')
        require(isinstance(entry['id'], str) and re.fullmatch(r'[a-z0-9-]+', entry['id']), 'Invalid recipe ID')
        require(entry['variant'] in ('A', 'B'), 'Invalid variant')
        require(isinstance(entry['source_sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', entry['source_sha256']), 'Invalid source SHA')
        identity = (entry['source_sha256'], entry['variant'])
        require(identity not in identities, 'Ambiguous recipes for the same source and variant')
        identities.add(identity)
        descriptor = entry['recipe']
        require(isinstance(descriptor, dict) and set(descriptor) == {'path', 'sha256'}, 'Invalid recipe binding')
        relative = Path(descriptor['path'])
        require(not relative.is_absolute() and '..' not in relative.parts, 'Recipe path must stay in catalogue directory')
        path = (catalog.parent/relative).resolve(strict=True)
        require(path.is_relative_to(package), 'Recipe escapes the package')
        require(sha(path) == descriptor['sha256'], 'Catalogue recipe bytes changed')
        recipe = read(path)
        require(recipe.get('schema') == 'atrass-source-style-recipe-v1'
                and recipe.get('source_sha256') == entry['source_sha256']
                and recipe.get('variant') == entry['variant'], 'Catalogue and recipe identity differ')
        if identity == (raw_sha, variant):
            matches.append({**entry, 'recipe': binding(path)})
    return matches[0] if matches else None


def resolve_ffmpeg(value=None):
    if value:
        return binding(shutil.which(value) or value)
    installed = shutil.which('ffmpeg')
    if installed:
        return binding(installed)
    try:
        import imageio_ffmpeg
        return binding(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, OSError, RuntimeError) as exc:
        raise ValueError('FFmpeg unavailable; configure --ffmpeg. No software is installed automatically.') from exc


def initialize(args):
    root = Path(args.output_dir).absolute()
    require(not root.exists() and not root.is_symlink(), 'Choose a new output directory; use resume for existing work')
    raw = binding(args.raw)
    # Probe the actual source before accepting even an unregistered input.
    import av
    with av.open(raw['path']) as container:
        require(container.streams.video and container.streams.audio, 'A filmed source with video and original audio is required')
        video = container.streams.video[0]
        metadata = {'width': video.width, 'height': video.height,
                    'fps': str(video.average_rate), 'duration_seconds': container.duration / av.time_base}
    draft = bool(getattr(args, 'draft', False))
    editorial = getattr(args, 'editorial_decisions', None)
    require(not editorial or draft, 'Editorial decisions require the transcript draft route')
    require(not draft or (args.asr_json or args.model_dir), 'Automatic draft requires a source-bound transcript or an existing local ASR model')
    selected = None if draft else select_recipe(args.catalog, raw['sha256'], args.variant)
    inputs = [raw, binding(args.catalog)]
    inputs.extend(binding(p) for p in sorted((KIT/'engine').glob('*.py')))
    config = {'schema': SCHEMA, 'raw': raw, 'variant': args.variant, 'metadata': metadata,
              'selected': selected, 'inputs': inputs, 'release_ready': False,
              'editorial_decisions_inferred': False, 'draft_from_transcript': draft}
    if editorial:
        config['editorial_decisions'] = binding(editorial)
        inputs.append(config['editorial_decisions'])
    if selected:
        require(not args.asr_json and not args.model_dir, 'Known source already has bound decisions; do not mix a new transcript into it')
        config['ffmpeg'] = resolve_ffmpeg(args.ffmpeg)
        inputs.extend([selected['recipe'], config['ffmpeg']])
    elif args.asr_json or args.model_dir:
        require((KIT/'scripts/audio_audit.py').is_file() or args.asr_json,
                'Local ASR requires the full kit; this rendering package does not include research scripts')
        config['ffmpeg'] = resolve_ffmpeg(args.ffmpeg)
        inputs.append(config['ffmpeg'])
        if args.asr_json:
            transcript = binding(args.asr_json)
            require(read(transcript['path']).get('source_sha256') == raw['sha256'],
                    'Transcript must declare the actual raw SHA; a filename match is insufficient')
            config['asr_json'] = transcript
            inputs.append(transcript)
        else:
            model = Path(args.model_dir).resolve(strict=True)
            require(model.is_dir(), 'An existing local ASR model directory is required')
            config['model_dir'] = str(model)
            model_files = [binding(p) for p in sorted(model.rglob('*')) if p.is_file()]
            require(model_files, 'ASR model directory is empty')
            inputs.extend(model_files)
            inputs.append(binding(KIT/'scripts/audio_audit.py'))
    verify_bindings(inputs)
    root.mkdir(parents=True, exist_ok=False)
    save(root/'raw-start.json', config)
    return root


def execute(root, prepare_only=False):
    root = Path(root).resolve(strict=True)
    with job_lock(root):
        config = read(root/'raw-start.json')
        require(config.get('schema') == SCHEMA, 'Unsupported raw start job')
        verify_bindings(config['inputs'])
        if not config['selected']:
            prepared = None
            if config.get('asr_json') or config.get('model_dir'):
                def prepare(attempt):
                    destination = attempt/'prepared'
                    cmd = [sys.executable, KIT/'engine/prepare_job.py', '--raw', config['raw']['path'],
                           '--variant', config['variant'], '--output-dir', destination,
                           '--ffmpeg', config['ffmpeg']['path']]
                    if config.get('asr_json'):
                        cmd += ['--asr-json', config['asr_json']['path']]
                    else:
                        cmd += ['--model-dir', config['model_dir']]
                    if config.get('draft_from_transcript'):
                        cmd += ['--normalize-asr-overlap']
                    command(cmd, attempt)
                    return {'job': str(destination/'job.json')}, [p for p in destination.rglob('*') if p.is_file()]
                prepared = stage(root, '01-prepare', config['inputs'], prepare)
            if config.get('draft_from_transcript'):
                require(prepared is not None, 'Draft requires a prepared transcript')
                from draft_from_transcript import build
                def draft(attempt):
                    result = build(prepared['job'], attempt/'draft', config['ffmpeg']['path'])
                    return result, list((attempt/'draft').glob('*.json'))
                drafted = stage(root, '02-draft', config['inputs'] + [binding(prepared['job'])], draft)
                if config.get('editorial_decisions'):
                    from refine_transcript_draft import build as refine
                    job = read(prepared['job'])
                    transcript_path = Path(prepared['job']).parent/job['files']['transcript']
                    def editorial(attempt):
                        result = refine(drafted['plan'], transcript_path,
                                        config['editorial_decisions']['path'], attempt/'refined')
                        return result, list((attempt/'refined').glob('*.json'))
                    drafted = stage(root, '02-editorial', config['inputs'] +
                        [binding(drafted['plan']), binding(transcript_path)], editorial)
                result = {'schema': SCHEMA, 'status': 'transcript_draft_prepared_review_pending',
                          **drafted, 'recipe_selected': False, 'editorial_decisions_inferred': False,
                          'source_editorial_decisions_applied': bool(config.get('editorial_decisions'))}
                publish(root/'prepared-result.json', result)
                if prepare_only:
                    return result
                pipeline = root/'03-longform'
                if not pipeline.exists():
                    initialize_pipeline(SimpleNamespace(output_dir=str(pipeline), raw=config['raw']['path'],
                        project=None, plan=drafted['plan'], job=None, recipe=None, decisions=None,
                        adopt_output=None, ffmpeg=config['ffmpeg']['path']))
                with job_lock(pipeline):
                    rendered = run(pipeline)
                result.update(status='transcript_draft_rendered_review_pending', movie=rendered['movie'],
                              movie_sha256=rendered['movie_sha256'], review=rendered['review'])
                publish(root/'result.json', result)
                return result
            result = {'schema': SCHEMA, 'status': 'needs_new_source_editorial_decisions',
                      'raw': config['raw'], 'prepared': prepared, 'recipe_selected': False,
                      'editorial_decisions_inferred': False, 'release_ready': False,
                      'next': 'The agent must prepare/read this source transcript, author source-specific takes, captions and visual decisions, then assemble and run a new recipe. Do not ask the user to write JSON; do not copy an existing recording timeline.',
                      'full_kit_skill': str(KIT/'SKILL.md') if (KIT/'SKILL.md').is_file() else None}
            publish(root/'source-request.json', result)
            return result

        from bind_recipe_source import bind_source
        from compose_source_style import compose
        def bind(attempt):
            result = bind_source(config['selected']['recipe']['path'], config['raw']['path'], attempt/'binding')
            return result, list((attempt/'binding').glob('*.json'))
        bound = stage(root, '01-bind', config['inputs'], bind)
        composed = compose(bound['recipe'], config['raw']['sha256'], root/'02-compose')
        result = {'schema': SCHEMA, 'status': 'known_source_recipe_composed',
                  'source_sha256': config['raw']['sha256'], 'recipe_id': config['selected']['id'],
                  'project': composed['project'], 'project_sha256': sha(composed['project']),
                  'composition_steps': len(composed['steps']), 'release_ready': False,
                  'editorial_decisions_inferred': False,
                  'scope': 'Replays decisions only for these exact raw bytes; not new-recording semantic editing'}
        publish(root/'prepared-result.json', result)
        if prepare_only:
            return result
        pipeline = root/'03-longform'
        if not pipeline.exists():
            initialize_pipeline(SimpleNamespace(output_dir=str(pipeline), raw=config['raw']['path'],
                project=composed['project'], plan=None, job=None, recipe=None, decisions=None,
                adopt_output=None, ffmpeg=config['ffmpeg']['path']))
        with job_lock(pipeline):
            rendered = run(pipeline)
        result.update(status='known_source_rendered_review_pending', movie=rendered['movie'],
                      movie_sha256=rendered['movie_sha256'], review=rendered['review'])
        publish(root/'result.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    start = sub.add_parser('start')
    start.add_argument('--raw', required=True)
    start.add_argument('--output-dir', required=True)
    start.add_argument('--variant', choices=['A', 'B'], default='A')
    start.add_argument('--catalog', default=str(KIT/'recipes/source-catalog.json'))
    start.add_argument('--ffmpeg')
    transcript = start.add_mutually_exclusive_group()
    transcript.add_argument('--asr-json')
    transcript.add_argument('--model-dir')
    start.add_argument('--prepare-only', action='store_true')
    start.add_argument('--draft', action='store_true', help='Build a conservative dialogue draft from this transcript, without reusing any registered episode recipe; requires --asr-json or --model-dir')
    start.add_argument('--editorial-decisions', help='Apply the agent-authored, hash-bound source speech decisions after --draft, before rendering')
    resume = sub.add_parser('resume')
    resume.add_argument('directory')
    resume.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    try:
        root = initialize(args) if args.action == 'start' else Path(args.directory)
        result = execute(root, args.prepare_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3 if result['status'] == 'needs_new_source_editorial_decisions' else 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
