#!/usr/bin/env python3
"""Compose explicit source editing decisions into one resumable A style graph.

A recipe binds a plan or a prepared job+decisions, then applies existing typed
adapters. It never executes recipe-supplied code or invents editorial approval.
Each adapter receives the actual preceding project SHA. Source intervals and
take IDs remain explicit; copying a reference movie is not a supported step.
"""
from pathlib import Path
import argparse
import copy
import importlib
import json
import re

from apply_source_assets import keys, require
from run_longform import (binding, read, save, sha, verify_bindings, stage,
                         project_bindings, job_lock)

KIT = Path(__file__).resolve().parents[1]
SCHEMA = 'atrass-source-style-recipe-v1'
OPERATIONS = {
    'natural_captions': ('restyle_source_captions', 'apply', None),
    'source_assets': ('apply_source_assets', 'apply_assets', 'atrass-source-assets-v1'),
    'source_chapters': ('apply_source_chapters', 'apply_chapters', 'atrass-source-chapters-v1'),
    'source_chat': ('apply_source_chat', 'apply_chat', 'atrass-source-chat-v1'),
    'number_contrast': ('apply_number_contrast', 'apply', 'atrass-source-number-contrast-v1'),
    'source_ending': ('append_source_ending', 'apply', 'atrass-source-ending-v1'),
    'panel_shadow': ('create_panel_shadow', 'create', 'atrass-recipe-panel-shadow-v1'),
    'deduplicate_chapter_text': ('deduplicate_chapter_text', 'apply', 'atrass-source-chapter-dedup-v1'),
    'silence_chapter_readouts': ('silence_chapter_readouts', 'apply', 'atrass-silent-chapter-readouts-v1'),
    'terminal_chapter': ('set_terminal_chapter', 'apply', 'atrass-terminal-chapter-v1'),
    'extend_source_caption': ('extend_source_caption', 'apply', 'atrass-source-caption-extension-v1'),
    'extend_source_tail': ('extend_source_tail', 'apply', 'atrass-source-tail-extension-v1'),
    'caption_copy': ('edit_caption_copy', 'apply', 'atrass-caption-copy-v1'),
    'chapter_cover': ('extend_chapter_cover', 'apply', 'atrass-chapter-cover-v1'),
    'protected_text_placement': ('place_source_text', 'apply', 'atrass-protected-text-placement-v1'),
}


def inspect_recipe(path, raw_sha):
    """Resolve all declared files before creating a job; return immutable inputs."""
    path = Path(path).resolve(strict=True)
    spec = read(path)
    keys(spec, ('schema', 'variant', 'source_sha256', 'base', 'steps'),
         ('schema', 'variant', 'source_sha256', 'base', 'steps'), 'Style recipe')
    require(spec['schema'] == SCHEMA and spec['variant'] == 'A',
            'Style recipe currently supports the observed A adapters only')
    require(spec['source_sha256'] == raw_sha, 'Recipe belongs to another raw source')
    rows = {str(path): binding(path)}

    def bound(root, descriptor):
        keys(descriptor, ('path', 'sha256'), ('path', 'sha256'), 'File binding')
        p = (root/descriptor['path']).resolve(strict=True)
        row = binding(p)
        require(descriptor['sha256'] == row['sha256'], 'Recipe dependency SHA mismatch: '+str(p))
        rows[str(p)] = row
        return p

    base = spec['base']
    keys(base, ('mode', 'input', 'decisions'), ('mode', 'input'), 'Recipe base')
    require(base['mode'] in ('plan', 'job'), 'Recipe base must be a plan or prepared job')
    require(('decisions' in base) == (base['mode'] == 'job'), 'Only a prepared job needs decisions')
    source = bound(path.parent, base['input'])
    source_data = read(source)
    require(source_data.get('variant') == 'A', 'Recipe base variant mismatch')
    if base['mode'] == 'job':
        require(source_data.get('schema') == 'atrass-job-v1', 'Prepared job required')
        decisions = bound(path.parent, base['decisions'])
        d = read(decisions)
        require(d.get('job_sha256') == sha(source), 'Decisions belong to another prepared job')
        transcript = (source.parent/source_data['files']['transcript']).resolve(strict=True)
        bound(source.parent, {'path': str(transcript), 'sha256': d['transcript_sha256']})
        require(read(transcript).get('source_sha256') == raw_sha, 'Transcript belongs to another raw source')
        raw = bound(source.parent, {k: source_data['raw'][k] for k in ('path', 'sha256')})
        require(rows[str(raw)]['sha256'] == raw_sha, 'Prepared job raw mismatch')
    else:
        require(source_data.get('schema') == 'atrass-edit-plan-v1', 'Edit plan required')
        found = False
        for group in ('media', 'fonts'):
            for item in source_data.get(group, {}).values():
                p = (source.parent/item['path']).resolve(strict=True)
                row = binding(p)
                require(not item.get('sha256') or item['sha256'] == row['sha256'], 'Plan resource SHA mismatch')
                require(item.get('role') not in ('reference', 'reference_video', 'finished_reference'), 'Reference movie in plan')
                rows[str(p)] = row
                if item.get('role') == 'source_footage' and row['sha256'] == raw_sha:
                    found = True
        require(found, 'Plan must bind the supplied raw source')

    steps = spec['steps']
    require(isinstance(steps, list) and steps, 'A recipe needs explicit style steps')
    ids = set()
    for step in steps:
        keys(step, ('id', 'operation', 'decision'), ('id', 'operation'), 'Recipe step')
        sid = step['id']
        require(isinstance(sid, str) and re.fullmatch('[a-z][a-z0-9-]{0,63}', sid) and sid not in ids,
                'Step IDs must be distinct safe names')
        ids.add(sid)
        op = step['operation']
        require(op in OPERATIONS, 'Unsupported recipe operation: '+str(op))
        schema = OPERATIONS[op][2]
        require(('decision' in step) == (schema is not None), 'Decision fields do not match operation')
        if schema is None:
            continue
        d = step['decision']
        require(isinstance(d, dict) and d.get('schema') == schema, 'Adapter decision schema mismatch')
        require('project_sha256' not in d, 'Recipe decisions bind the previous step; do not supply a stale project SHA')
        if 'source_sha256' in d:
            require(d['source_sha256'] == raw_sha, 'Step belongs to another raw source')
        if op == 'source_assets':
            for event in d['events']:
                asset = event['asset']
                bound(path.parent, {k: asset[k] for k in ('path', 'sha256')})
                bound(path.parent, asset['provenance'])
        if op == 'source_ending':
            bound(path.parent, d['effect']['profile'])
        if op == 'terminal_chapter':
            bound(path.parent, {k: d['reference'][k] for k in ('path', 'sha256')})
        if op in ('extend_source_caption', 'extend_source_tail'):
            bound(path.parent, d['transcript'])
        if op == 'panel_shadow':
            keys(d, ('schema', 'layer_ids', 'sigma', 'opacity', 'dx', 'dy'),
                 ('schema', 'layer_ids', 'sigma', 'opacity', 'dx', 'dy'), 'Panel shadow decision')
            require(isinstance(d['layer_ids'], list) and d['layer_ids'] and
                    all(isinstance(x, str) for x in d['layer_ids']), 'Explicit shadow parent IDs required')
    # Freeze implicit template inputs as well as explicit recipe resources.
    implicit = list((KIT/'engine').glob('*.py')) + [p for p in (KIT/'assets/fonts').glob('*') if p.is_file()]
    if any(s['operation'] == 'source_chapters' for s in steps):
        pilot = KIT/'projects/A-chapter-pilot-procedural-v1.json'
        implicit += [pilot, KIT/'assets/chapter-pilot/provenance.json']
        for group in ('media', 'fonts'):
            implicit += [(pilot.parent/item['path']).resolve() for item in read(pilot)[group].values()]
    if any(s['operation'] == 'source_chat' for s in steps):
        implicit += [KIT/'assets/chat/avatar-profile-native-reference.png', KIT/'references/A-chat-composites-measured.json']
    for p in implicit:
        rows[str(p.resolve())] = binding(p)
    return spec, list(rows.values())


def resolved_decision(step, recipe_root, previous):
    d = copy.deepcopy(step['decision'])
    d['project_sha256'] = sha(previous)
    if step['operation'] == 'source_assets':
        for event in d['events']:
            asset = event['asset']
            asset['path'] = str((recipe_root/asset['path']).resolve(strict=True))
            asset['provenance']['path'] = str((recipe_root/asset['provenance']['path']).resolve(strict=True))
    elif step['operation'] == 'source_ending':
        profile = d['effect']['profile']
        profile['path'] = str((recipe_root/profile['path']).resolve(strict=True))
    elif step['operation'] == 'terminal_chapter':
        d['reference']['path'] = str((recipe_root/d['reference']['path']).resolve(strict=True))
    elif step['operation'] in ('extend_source_caption', 'extend_source_tail'):
        d['transcript']['path'] = str((recipe_root/d['transcript']['path']).resolve(strict=True))
    return d


def compose(recipe, raw_sha, directory):
    recipe = Path(recipe).resolve(strict=True)
    root = Path(directory).resolve()
    spec, inputs = inspect_recipe(recipe, raw_sha)
    root.mkdir(parents=True, exist_ok=True)
    with job_lock(root):
        config = root/'composition-inputs.json'
        expected = {'recipe': str(recipe), 'inputs': inputs, 'source_sha256': raw_sha}
        if config.exists():
            require(read(config) == expected, 'Composition inputs changed; use a new job directory')
        else:
            require(not list(root.glob('step-*')), 'Composition has steps but no input receipt')
            save(config, expected)
        projects = root/'projects'
        projects.mkdir(exist_ok=True)

        def compile_base(attempt):
            from compile_plan import compile_plan
            base = spec['base']
            source = (recipe.parent/base['input']['path']).resolve(strict=True)
            if base['mode'] == 'job':
                from assemble_edit_plan import assemble
                decisions = (recipe.parent/base['decisions']['path']).resolve(strict=True)
                plan = attempt/'edit-plan.json'
                assemble(source, decisions, plan)
            else:
                plan = source
            project = projects/f"base-{attempt.name}.json"
            compile_plan(plan, project)
            bindings = project_bindings(project, raw_sha)
            return {'project': str(project), 'bindings': bindings}, [Path(x['path']) for x in bindings]+list(attempt.glob('edit-plan.json'))

        compiled = stage(root, 'step-00-base', inputs, compile_base)
        records = []
        for index, step in enumerate(spec['steps'], 1):
            previous = Path(compiled['project'])
            stage_inputs = inputs+compiled['bindings']

            def apply_step(attempt):
                module, fn, schema = OPERATIONS[step['operation']]
                output = projects/f"{index:02d}-{step['id']}-{attempt.name}.json"
                adapter = getattr(importlib.import_module(module), fn)
                files = []
                if schema is None:
                    detail = adapter(previous, output, sha(previous))
                elif step['operation'] == 'panel_shadow':
                    d = step['decision']
                    assets = output.with_suffix('.shadow-assets')
                    detail = adapter(previous, d['layer_ids'], output, assets,
                                     **{k: d[k] for k in ('sigma', 'opacity', 'dx', 'dy')})
                    files.extend(p for p in assets.rglob('*') if p.is_file())
                else:
                    decision = attempt/'bound-decision.json'
                    save(decision, resolved_decision(step, recipe.parent, previous))
                    files.append(decision)
                    detail = adapter(previous, decision, output)
                dependencies = project_bindings(output, raw_sha)
                result = {'id': step['id'], 'operation': step['operation'], 'project': str(output),
                          'previous_project_sha256': sha(previous), 'bindings': dependencies,
                          'adapter_result': detail, 'release_ready': False}
                return result, files+[Path(x['path']) for x in dependencies]

            compiled = stage(root, f"step-{index:02d}-{step['id']}", stage_inputs, apply_step)
            records.append({k: compiled[k] for k in ('id', 'operation', 'project', 'previous_project_sha256')})
        verify_bindings(inputs)
        result = {'schema': 'atrass-source-style-composition-v1', 'recipe_sha256': sha(recipe),
                  'project': compiled['project'], 'bindings': compiled['bindings'], 'steps': records,
                  'source_sha256': raw_sha, 'release_ready': False, 'direct_listening_seconds': 0,
                  'editorial_decisions_inferred': False}
        final = root/'composition-result.json'
        if final.exists():
            require(read(final) == result, 'Completed composition changed')
        else:
            save(final, result)
        return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--recipe', required=True)
    ap.add_argument('--source-sha256', required=True)
    ap.add_argument('--output-dir', required=True)
    a = ap.parse_args()
    print(json.dumps(compose(a.recipe, a.source_sha256, a.output_dir), ensure_ascii=False, indent=2))
