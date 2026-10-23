#!/usr/bin/env python3
"""Revalidate the scale guard without regenerating render/assets or visual approvals.

python engine/selftest_nle_scale_guard.py exports/character-reveal-nle-v1 \
  --existing exports/A-pilot-40s-nle --existing exports/A-chat-04-nle-v2
Writes new scale-guard-revalidation.json/.txt; refuses to overwrite prior records.
"""
import argparse
import copy
import json
from pathlib import Path
import export_nle as n
import validate_nle_export as v


def run(folder,existing):
    folder=Path(folder).resolve();engine=Path(__file__).resolve().parent
    target=folder/'scale-guard-revalidation.json';txt=target.with_suffix('.txt')
    if target.exists() or txt.exists():raise ValueError('Revalidation record already exists; preserve prior evidence')
    # Include all original delivery bytes, including the PNGs, font instances,
    # XML/JSX, renderer MP4, project and prior machine/manual review records.
    preserved={str(f.relative_to(folder)):n.renderer.sha(f) for f in sorted(folder.rglob('*')) if f.is_file()}
    manifest=json.loads((folder/'nle/nle-manifest.json').read_text(encoding='utf-8'))
    project=Path(manifest['project_path']);p=json.loads(project.read_text(encoding='utf-8'));root=project.parent;checks=[]
    def check(name,value=True):
        if not value:raise AssertionError(name)
        checks.append({'name':name,'pass':True})
    def reject(name,func):
        try:func()
        except ValueError as exc:checks.append({'name':name,'pass':True,'rejection':str(exc)});return
        raise AssertionError(name+' did not reject')
    examples=[]
    for e in p['layers']:
        label=('reveal' if e.get('character_reveal') is not None else 'static')+'-'+e['kind']
        if label in [x['label'] for x in examples]:continue
        t=e['in']+.3*(e['out']-e['in']);original=n.renderer.Composer(p,root).image(e,t)
        scaled=copy.deepcopy(e);scaled['scale']=1.25
        im,xy=n.renderer.Composer(p,root).image(scaled,t)
        check(label+': current renderer ignores top-level scale',im.size==original[0].size and im.tobytes()==original[0].tobytes() and xy==original[1])
        q=copy.deepcopy(p);q['layers']=[scaled]
        reject(label+': NLE explicitly rejects scale=1.25',lambda:n.preflight(q))
        q['layers'][0]['scale']=1;n.preflight(q);check(label+': identity scale accepted')
        keyed=copy.deepcopy(e);keyed['scale_keys']=[[0,1.25]]
        keyed_im,_=n.renderer.Composer(p,root).image(keyed,t)
        check(label+': constant scale_keys really resizes renderer canvas',keyed_im.size==tuple(max(1,round(a*1.25)) for a in original[0].size))
        q['layers']=[keyed];reject(label+': even constant scale_keys remains unsupported in NLE',lambda:n.preflight(q))
        examples.append({'label':label,'event_id':e['id'],'sample_time':t,'unscaled_size':list(original[0].size),'top_level_scale_1_25_size':list(im.size),'constant_scale_key_1_25_size':list(keyed_im.size)})
    n.preflight(p);check('Existing raw source clip geometry.scale=0.75 remains supported',p['clips'][0]['geometry']['scale']==.75)
    validation=v.validate(folder/'nle');check('Updated semantic validation passes original export')
    regressions=[]
    for path in existing:
        path=Path(path).resolve();result=v.validate(path);regressions.append({'export':str(path),'result':result})
        check('Updated existing-export regression '+path.name)
    after={str(f.relative_to(folder)):n.renderer.sha(f) for f in sorted(folder.rglob('*')) if f.is_file()}
    check('Every original delivery file has identical SHA256 after revalidation',after==preserved)
    result={'pass':True,'scope':'NLE rejection guard and semantic regression only; no new render or manual visual approval',
            'current_exporter_sha256':n.renderer.sha(engine/'export_nle.py'),
            'current_semantic_validator_sha256':n.renderer.sha(engine/'validate_nle_export.py'),
            'current_character_reveal_selftest_sha256':n.renderer.sha(engine/'selftest_character_reveal_nle.py'),
            'current_scale_guard_selftest_sha256':n.renderer.sha(__file__),
            'generation_exporter_sha256_preserved':manifest['exporter_sha256'],
            'prior_full_render_validation_sha256':preserved['character-reveal-nle-validation.json'],
            'prior_manual_visual_review_sha256':preserved['visual-review.json'],
            'checks':checks,'check_count':len(checks),'examples':examples,'updated_semantic_validation':validation,
            'existing_export_regressions':regressions,'preserved_files_sha256':preserved,
            'new_render_executed':False,'new_visual_review_executed':False,'actual_Adobe_import_executed':False,
            'finding':'render.py evaluates scale_keys with default 1 and does not read top-level layer scale for either static or reveal layers. NLE now rejects nonidentity layer scale in both paths; source clip geometry.scale stays supported.',
            'evidence_binding':'Original generation hashes, render bytes and visual-review evidence remain unchanged. Current code hashes describe this additional semantic revalidation, not a new export or render.'}
    n.atomic(target,result)
    n.atomic(txt,'NLE scale 안전 거부 후속 검증\n\n현재 render.py는 static/reveal 모두 layer.scale을 읽지 않고 scale_keys 기본값1을 사용합니다. scale=1.25가 두 종류 모두 무시되는 것을 실제 Composer.image 픽셀과 크기로 확인했습니다.\nNLE는 layer.scale≠1을 명시 거부합니다. raw source clip geometry.scale 지원은 유지하고 constant scale_keys 포함 키프레임은 계속 거부합니다.\n\n'+f'후속 검사 {len(checks)}개, 기존 fixture 의미 검사 {validation["count"]}개 통과. 기존 NLE 회귀 '+', '.join(str(r['result']['count'])+'개' for r in regressions)+' 통과.\n'+f'기존 전달 파일 {len(preserved)}개의 SHA256 동일. render/PNG/font/XML/JSX와 기존 수동 시각검수 기록을 다시 쓰지 않았습니다.\n'+
             '이 문서는 새 렌더나 시각 승인, 실제 Adobe 가져오기 검증이 아닙니다. 원래 생성 해시는 기존 nle-manifest.json에 보존하고 현재 exporter/validator 해시는 옆 JSON에 별도 기록합니다.\n')
    print(json.dumps({'pass':True,'checks':len(checks),'semantic_checks':validation['count'],'original_files_unchanged':len(preserved),'existing_checks':[r['result']['count'] for r in regressions]}))
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('fixture_dir');ap.add_argument('--existing',action='append',default=[]);a=ap.parse_args();run(a.fixture_dir,a.existing)
