#!/usr/bin/env python3
"""Bind an existing plan recipe to the same raw bytes on another machine.

Copy the complete kit first. This creates a new plan/recipe beside a receipt;
it never changes source footage, changes editorial timing, or substitutes a
different recording under an existing source identity. Prepared jobs should
instead be created on the destination using the normal job workflow.
"""
from pathlib import Path
import argparse
import copy
import json

from apply_source_assets import require
from compose_source_style import inspect_recipe, resolved_decision
from run_longform import read, save, sha, binding, verify_bindings


def bind_source(recipe, raw, directory):
    recipe = Path(recipe).resolve(strict=True)
    raw = Path(raw).resolve(strict=True)
    root = Path(directory).resolve()
    require(not root.exists(), 'Choose a new binding directory; existing work is preserved')
    spec = read(recipe)
    require(spec.get('schema') == 'atrass-source-style-recipe-v1', 'A style recipe is required')
    raw_binding = binding(raw)
    require(raw_binding['sha256'] == spec.get('source_sha256'),
            'Raw SHA differs: create new source editing decisions for a different recording')
    base = spec['base']
    require(base['mode'] == 'plan', 'Binding supports plan recipes; prepare new jobs locally')
    source = (recipe.parent/base['input']['path']).resolve(strict=True)
    require(sha(source) == base['input']['sha256'], 'Base plan SHA mismatch')
    plan = copy.deepcopy(read(source))
    require(plan.get('schema') == 'atrass-edit-plan-v1', 'A plan recipe is required')
    before = copy.deepcopy(plan)
    matched = []
    for group in ('media', 'fonts'):
        for key, item in plan.get(group, {}).items():
            if group == 'media' and item.get('role') == 'source_footage':
                require(item.get('sha256') == raw_binding['sha256'],
                        'Every source footage entry must declare this raw identity')
                item['path'] = str(raw)
                matched.append(key)
            else:
                item['path'] = str((source.parent/item['path']).resolve(strict=True))
    require(matched, 'No declared source footage found in the plan')
    result = copy.deepcopy(spec)
    for step in result['steps']:
        if 'decision' in step:
            # Reuse typed path handling; the previous-project hash will be
            # injected when composing, so it must not persist in the recipe.
            decision = resolved_decision(step, recipe.parent, source)
            decision.pop('project_sha256')
            step['decision'] = decision
    # Byte-bind inputs used above before publishing; failed attempts remain
    # separate and cannot replace a previous successful package.
    original_inputs = [binding(recipe), binding(source), raw_binding]
    root.mkdir(parents=True)
    try:
        plan_path = root/'source.plan.json'
        save(plan_path, plan)
        result['base']['input'] = {'path': plan_path.name, 'sha256': sha(plan_path)}
        recipe_path = root/'source.recipe.json'
        save(recipe_path, result)
        _, dependencies = inspect_recipe(recipe_path, raw_binding['sha256'])
        verify_bindings(original_inputs)
        verify_bindings(dependencies)
        for key in before:
            if key not in ('media', 'fonts'):
                require(before[key] == plan[key], 'Editorial data changed during path binding')
        report = {'schema': 'atrass-source-recipe-binding-v1', 'source_recipe': str(recipe),
                  'source_recipe_sha256': original_inputs[0]['sha256'],
                  'recipe': str(recipe_path), 'recipe_sha256': sha(recipe_path),
                  'raw': raw_binding, 'matched_media_ids': matched,
                  'dependencies': dependencies, 'editorial_data_unchanged': True,
                  'old_raw_path_required': False, 'raw_copied': False,
                  'scope': 'Same source identity at a new path, not editing a new recording',
                  'release_ready': False}
        save(root/'binding-result.json', report)
        return report
    except Exception as exc:
        save(root/'binding-failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recipe', required=True)
    parser.add_argument('--raw', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    result = bind_source(args.recipe, args.raw, args.output_dir)
    print(json.dumps({k: result[k] for k in ('recipe', 'recipe_sha256', 'editorial_data_unchanged')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
