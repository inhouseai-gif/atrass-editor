#!/usr/bin/env python3
"""Guard known-source routing and unknown-source non-substitution with real media."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import copy
import json
import subprocess
import sys

from start_from_raw import initialize, execute, select_recipe
from run_longform import save, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--ffmpeg', required=True)
    a = parser.parse_args()
    root = Path(a.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    recipes = root/'recipes'
    recipes.mkdir()
    source = root/'원본 이름.mov'
    subprocess.run([a.ffmpeg, '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                    'color=c=navy:s=320x180:r=30:d=0.6', '-f', 'lavfi', '-i',
                    'sine=frequency=400:sample_rate=48000:duration=0.6', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source)], check=True)
    digest = sha(source)
    recipe = recipes/'selected.json'
    save(recipe, {'schema': 'atrass-source-style-recipe-v1', 'variant': 'A', 'source_sha256': digest})
    row = {'id': 'fixture', 'variant': 'A', 'source_sha256': digest,
           'recipe': {'path': recipe.name, 'sha256': sha(recipe)}}
    catalogue = recipes/'source-catalog.json'
    save(catalogue, {'schema': 'atrass-source-catalog-v1', 'entries': [row]})
    checks = []
    def denied(name, fn):
        try:
            fn()
        except (ValueError, OSError):
            checks.append(name)
        else:
            raise AssertionError(name)
    assert select_recipe(catalogue, digest, 'A')['id'] == 'fixture'
    checks.append('exact_source_hash_selects_recipe')
    assert select_recipe(catalogue, '0'*64, 'A') is None
    assert select_recipe(catalogue, digest, 'B') is None
    checks.append('other_bytes_or_variant_never_inherit_recipe')
    for name, mutate in [
        ('ambiguous_source_rejected', lambda x: x['entries'].append(copy.deepcopy(row))),
        ('changed_recipe_hash_rejected', lambda x: x['entries'][0]['recipe'].update(sha256='0'*64)),
        ('foreign_recipe_source_rejected', lambda x: x['entries'][0].update(source_sha256='0'*64)),
        ('path_traversal_rejected', lambda x: x['entries'][0]['recipe'].update(path='../recipes/selected.json')),
    ]:
        data = {'schema': 'atrass-source-catalog-v1', 'entries': [copy.deepcopy(row)]}
        mutate(data); path = recipes/(name+'.json');save(path, data)
        denied(name, lambda: select_recipe(path, digest, 'A'))
    empty = recipes/'empty.json';save(empty, {'schema':'atrass-source-catalog-v1', 'entries':[]})
    def args(output, asr=None):
        return SimpleNamespace(raw=str(source), output_dir=str(root/output), catalog=str(empty),
                               variant='A', asr_json=asr, model_dir=None, ffmpeg=a.ffmpeg)
    unknown = initialize(args('unknown'))
    result = execute(unknown, True)
    assert result['status'] == 'needs_new_source_editorial_decisions' and not result['recipe_selected']
    assert not (unknown/'02-compose').exists() and not (unknown/'03-longform').exists()
    assert execute(unknown, True) == result
    checks.append('unknown_source_receipt_resumes_without_reusing_cuts_or_rendering')
    denied('existing_job_preserved', lambda: initialize(args('unknown')))
    asr = root/'transcript.json'
    save(asr, {'source_sha256':digest, 'segments':[{'start':0.05,'end':0.5,'text':'합성 입력 시험'}]})
    prepared = initialize(args('unknown-with-asr', str(asr)))
    prepared_result = execute(prepared, True)
    job = json.loads(Path(prepared_result['prepared']['job']).read_text())
    assert job['status'] == 'input_prepared_edit_plan_pending' and job['raw']['sha256'] == digest
    assert not job['asr_text_verified'] and prepared_result['release_ready'] is False
    assert execute(prepared, True) == prepared_result
    checks.append('real_media_and_bound_transcript_prepared_without_false_semantic_approval')
    false_asr = root/'foreign-transcript.json';save(false_asr, {'source_sha256':'0'*64,'segments':[]})
    denied('foreign_transcript_rejected_before_work_creation', lambda: initialize(args('bad-asr',str(false_asr))))
    assert not (root/'bad-asr').exists()
    before = sha(source)
    assert before == digest; checks.append('source_bytes_preserved_by_all_operations')
    with source.open('ab') as f:f.write(b'fixture mutation for rejection test')
    denied('source_change_blocks_resume', lambda: execute(unknown, True))
    report = {'pass': True, 'count': len(checks), 'checks': checks,
              'scope':'Routing and unknown-source handoff; synthetic media is not a new filmed episode',
              'release_ready':False}
    save(root/'validation.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
