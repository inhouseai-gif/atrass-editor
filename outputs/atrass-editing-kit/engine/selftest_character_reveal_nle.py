#!/usr/bin/env python3
"""NLE effect regression on a short raw-A fixture; not reference reconstruction QA.

python engine/selftest_character_reveal_nle.py --source /path/to/raw.mov \
  --output-dir exports/character-reveal-nle-local --ffmpeg /path/to/ffmpeg
Requires Python engine dependencies, FFmpeg, Node, and the provided raw footage.
Writes only a NEW exports folder. Does not import into Adobe or modify source media.
"""
import argparse
import copy
import json
import math
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np
from PIL import Image, ImageDraw
import export_nle as n
import validate_nle_export as validator


def run(source, destination, ffmpeg):
    out=Path(destination).resolve();source=Path(source).resolve();kit=Path(__file__).resolve().parent.parent
    if not source.is_file():raise ValueError('Missing raw source '+str(source))
    if out.exists() and any(out.iterdir()):raise ValueError('Use a new, empty fixture directory')
    out.mkdir(parents=True,exist_ok=True)
    fps=Fraction(60000,1001);duration=float(96/fps);checks=[]
    def check(name,condition=True):
        if not condition:raise AssertionError(name)
        checks.append({'name':name,'pass':True})
    def reject(name,fn):
        try:fn()
        except ValueError:check(name);return
        raise AssertionError(name+' did not reject')
    p={'schema':'atrass-render-v1','title':'NLE character reveal effect regression — raw A','variant':'A',
       'width':1920,'height':1080,'fps':str(fps),'duration':duration,'release_ready':False,
       'purpose':'effect_regression_only_not_reference_reconstruction',
       'media':{'rawA':{'path':str(source),'role':'source_footage'}},
       'fonts':{'body':{'path':os.path.relpath(kit/'assets/fonts/SUIT-Variable.ttf',out),'weight':400,'status':'candidate_not_original_identified'}},
       'clips':[{'id':'raw-A-native-regression','in':0,'out':duration,'source':'rawA','source_in':18.05,
                 'source_frame_times':[(1083+i)/60 for i in range(96)],'geometry':{'scale':.75,'tx':0,'ty':0}}],
       'layers':[
           {'id':'independent-backplate','kind':'rect','in':0,'out':duration,'position':[140,690],'size':[1640,350],'fill':'#161A21EE','radius':18,'z':10},
           {'id':'fixed-layout-reveal','kind':'text','in':.103,'out':1.5,'position':[260.4,738.6],'font':'body','font_size':72,'fill':'#F4D48FFF','tracking':2,'line_gap':16,'padding':4,'align':'center','text':'마음은 서두르지 않고\n한 글자씩 전합니다','character_reveal':{'duration':.71,'glyph_fade_duration':.07,'start':.041,'easing':'smoothstep'},'z':20},
           {'id':'single-glyph-reveal','kind':'text','in':.25,'out':1.4,'position':[1500,925],'font':'body','font_size':45,'fill':'#82DDFFFF','text':'끝','character_reveal':{'duration':.5,'glyph_fade_duration':.1,'start':.04},'z':21},
           {'id':'independent-label','kind':'text','in':0,'out':duration,'position':[280,970],'font':'body','font_size':28,'fill':'#FFFFFF','text':'NLE STATE REGRESSION','z':30}],
       'audio':[{'id':'raw-only-audio','in':0,'out':duration,'source':'rawA','source_in':18.05075}],
       'limitations':['Synthetic effect test on supplied raw A footage; not a measured reference title or added reconstruction coverage.','No actual Adobe application import or playback verification.']}
    project=out/'fixture.json';n.atomic(project,p);manifest=n.export(project,out/'nle',ffmpeg)
    result=validator.validate(out/'nle');n.atomic(out/'semantic-validation.json',result)
    check('Generic NLE semantic validator passed')
    reversed_rows=list(reversed(manifest['components']))
    reject('Parent stacking order cannot silently change',lambda:n.audit_reveals(p,reversed_rows,out/'nle',out))
    node=shutil.which('node')
    if not node:raise ValueError('Node is required for JSX mock validation')
    subprocess.run([node,str(kit/'engine/validate_nle_jsx.js'),str(out/'nle/after-effects.jsx'),str(out/'ae-generic-mock.json')],check=True)
    subprocess.run([node,str(kit/'engine/selftest_character_reveal_nle.js'),str(out/'nle/after-effects.jsx'),str(out/'ae-reveal-mock.json')],check=True)
    check('Both JSX mocks executed; not actual AE import')
    composer=n.renderer.Composer(p,out)
    # Directly evaluate EVERY output frame; the assertion does not reuse visual_span.
    for parent in (e for e in p['layers'] if e.get('character_reveal') is not None):
        rows=[r for r in manifest['components'] if r['event_id']==parent['id']]
        images={r['id']:Image.open(out/'nle'/r['path']).convert('RGBA') for r in rows}
        for i in range(96):
            t=float(Fraction(i,1)/fps);active=parent['in']<=t+1e-9<parent['out']
            current=[r for r in rows if r['in_frame']<=i<r['out_frame']]
            check(f"{parent['id']} f{i}: parent/child activity",len(current)==int(active))
            if active:
                im,xy=composer.image(parent,t);r=current[0]
                check(f"{parent['id']} f{i}: exact RGBA + fixed position",im.tobytes()==images[r['id']].tobytes() and list(xy)==r['position'])
        final=images[rows[-1]['id']]
        check(parent['id']+' completed state uses same full canvas',final.tobytes()==composer.character_layer(parent).static_image().tobytes())
    # Small isolated cases: off-grid/tolerance boundaries, instantaneous, one
    # glyph, whitespace/newline, truncated parent, negative screen position.
    cases=[('step',{'duration':.6,'glyph_fade_duration':0},'가 나\n다'),
           ('instant',{'duration':0,'glyph_fade_duration':0,'start':.2},'가나'),
           ('whitespace',{},'  \n '),('empty',{},''),
           ('truncated',{'duration':3,'glyph_fade_duration':.2},'가나다라마'),
           ('tolerance',{'duration':.1,'glyph_fade_duration':0},'가')]
    for name,timing,text in cases:
        q=copy.deepcopy(p);e=copy.deepcopy(p['layers'][1]);e.update(id=name,text=text,position=[-10.4,-3.2],character_reveal=timing)
        e['in']=float(6/fps)+(0.5e-9 if name=='tolerance' else .003);e['out']=float(60/fps)+.5e-9
        q['layers']=[e];comp=n.renderer.Composer(q,out);states=list(n.reveal_states(e,q,comp));decoded=0
        for i in range(96):
            t=float(Fraction(i,1)/fps);current=[s for s in states if s['start']<=i<s['end']]
            assert len(current)==int(e['in']<=t+1e-9<e['out'])
            if current:
                im,xy=comp.image(e,t);assert im.tobytes()==current[0]['pixels'] and list(xy)==current[0]['position'];decoded+=1
        check(name+' every affected output frame exactly reproduced ('+str(decoded)+')')
    for key,value in [('opacity',.7),('opacity_keys',[[0,0],[1,1]]),('rotation',1),('rotation_keys',[[0,0],[1,2]]),('x_keys',[[0,0],[1,2]]),('y_keys',[[0,0],[1,2]]),('scale',1.25),('scale_keys',[[0,1],[1,2]]),('reveal_keys',[[0,0],[1,1]]),('fade_in',.2),('fade_out',.2),('anchor','center'),('background','#FFFFFF'),('box',[0,0,30,30])]:
        q=copy.deepcopy(p);q['layers'][1][key]=value;reject('Still rejects unsupported '+key,lambda:n.preflight(q))
    q=copy.deepcopy(p);e=q['layers'][1];e['in']=.001;e['out']=.002
    reject('Reject positive-duration reveal with no sampled output frames',lambda:list(n.reveal_states(e,q,n.renderer.Composer(q,out))))
    with tempfile.TemporaryDirectory(prefix='empty-reveal-',dir=out) as td:
        folder=Path(td);q=copy.deepcopy(p);q['duration']=float(4/fps);q['audio']=[]
        q['fonts']['body']['path']=str(n.renderer.resolve(out,p['fonts']['body']['path']))
        q['clips'][0]['out']=q['duration'];q['clips'][0]['source_frame_times']=q['clips'][0]['source_frame_times'][:4]
        e=copy.deepcopy(p['layers'][1]);e.update(id='empty-single-state',text='',character_reveal={},**{'in':0,'out':q['duration']});q['layers']=[e]
        n.atomic(folder/'fixture.json',q);single=n.export(folder/'fixture.json',folder/'nle',ffmpeg)
        check('Generic validator handles one transparent state without assuming glyphs or multiple states',len(single['components'])==1 and validator.validate(folder/'nle')['pass'])
    # Actual raw-based renderer output, independently recomposed with exported
    # full-frame PNGs. Encoded MP4 is lossy; pre-encode RGB is compared exactly.
    render=n.renderer.render(project,out/'renderer.mp4',draft=True,ffmpeg=ffmpeg)
    reader=n.renderer.SourceReader(source);video=av.open(str(out/'renderer.mp4'));video.streams.video[0].codec_context.thread_count=2;decoded=video.decode(video=0)
    boundary={0,95}
    for r in manifest['components']:
        if r.get('character_reveal_state'):
            boundary.update(i for edge in (r['in_frame'],r['out_frame']) for i in (edge-1,edge) if 0<=i<96)
    maes=[];maxdiff=0;panels=[];sample_frames=[0,8,13,23,34,45,56,89]
    cached={};composite_frames=[]
    try:
        for i in range(96):
            t=float(Fraction(i,1)/fps);raw,_=reader.get(p['clips'][0]['source_frame_times'][i])
            base=cv2.warpAffine(raw,np.array([[.75,0,0],[0,.75,0]],np.float32),(1920,1080),flags=cv2.INTER_AREA,borderMode=cv2.BORDER_CONSTANT)
            active=[e for e in p['layers'] if e['in']<=t+1e-9<e['out']]
            expected=composer.compose(base,active,t);exported=Image.fromarray(base).convert('RGBA')
            active_rows=[r for r in manifest['components'] if (r['in_frame']<=i<r['out_frame'] if r.get('character_reveal_state') else r['in']<=t+1e-9<r['out'])]
            needed={r['id'] for r in active_rows};cached={k:v for k,v in cached.items() if k in needed}
            for r in active_rows:
                if r['id'] not in cached:cached[r['id']]=Image.open(out/'nle'/r['canvas_path']).convert('RGBA')
                exported.alpha_composite(cached[r['id']])
            got=np.asarray(exported.convert('RGB'));delta=int(np.abs(got.astype(np.int16)-expected.astype(np.int16)).max());maxdiff=max(maxdiff,delta)
            if delta:raise AssertionError('Exported composite differs at frame '+str(i))
            encoded=next(decoded).to_ndarray(format='rgb24');maes.append(float(np.abs(encoded.astype(np.float32)-expected).mean()))
            if i in boundary:composite_frames.append(i)
            if i in sample_frames:
                panel=Image.new('RGB',(960,294),'#17202b');panel.paste(Image.fromarray(expected).resize((480,270)),(0,24));panel.paste(exported.convert('RGB').resize((480,270)),(480,24));ImageDraw.Draw(panel).text((10,5),f'f{i} renderer pre-encode | exported PNG composition; max RGB difference={delta}',fill='white');panels.append(panel)
        try:next(decoded);raise AssertionError('Unexpected extra rendered video frame')
        except StopIteration:pass
    finally:reader.close();video.close()
    check('All 96 raw-source composite frames byte-exact before encoding; all state boundaries included',maxdiff==0)
    check('Actual renderer MP4 decoded 96 frames',len(maes)==96)
    sheet=Image.new('RGB',(960,len(panels)*294),'#17202b')
    for i,im in enumerate(panels):sheet.paste(im,(0,i*294))
    sheet.save(out/'boundary-comparison.jpg',quality=90)
    summary={'pass':True,'purpose':'Character reveal NLE functionality regression, not reference reconstruction coverage',
             'project_sha256':n.renderer.sha(project),'exporter_sha256':n.renderer.sha(kit/'engine/export_nle.py'),
             'selftest_sha256':n.renderer.sha(__file__),'semantic_validator_sha256':n.renderer.sha(kit/'engine/validate_nle_export.py'),
             'renderer_sha256':n.renderer.sha(kit/'engine/render.py'),'character_reveal_sha256':n.renderer.sha(kit/'engine/character_reveal.py'),
             'checks':checks,'check_count':len(checks),'semantic_checks':result['count'],'output_frames':96,
             'character_reveal_states':manifest['validation']['character_reveal_state_count'],
             'png_components':len(manifest['components']),'logical_layers':len(p['layers']),
             'raw_render':render,'pre_encode_RGB_max_absolute_difference':maxdiff,
             'before_at_all_state_boundary_frames_compared':sorted(boundary),'boundary_frames_compared':composite_frames,
             'final_state_verified':True,'encoded_MP4_vs_preencode_RGB_MAE':{'mean':float(np.mean(maes)),'max':max(maes),'note':'Lossy H.264/YUV420 comparison, not Adobe playback'},
             'actual_application_import_verified':False,'ae_installed_or_executed':False,'release_ready':False,
             'limitations':['Static geometry plus sampled codepoint-opacity reveal only. Other dynamic opacity/rotation/keys remain rejected.','PNG state text is not native editable text. Change source JSON and re-export.','Full-text positions and canvas are preserved; no substring re-centering.','XML/JSX structure and JavaScript mocks are verified. Premiere timeline playback and AE application import remain unverified.','Synthetic fixture on raw A footage does not expand observed reference reconstruction coverage.']}
    n.atomic(out/'character-reveal-nle-validation.json',summary)
    n.atomic(out/'character-reveal-nle-validation.txt','글자별 등장 NLE 기능 회귀 검증\n\n'+f'96개 원본 기반 출력 프레임의 PNG 재합성 RGB 최대 차이: {maxdiff}/255\n상태 PNG: {summary["character_reveal_states"]}, 전체 구성요소: {len(manifest["components"])}\n자체 검사 {len(checks)}개, 일반 NLE 의미 검사 {result["count"]}개 통과.\n'+
             '문장·글립·색·부모 프레임 범위를 소스 JSON에서 보존하고 상태 PNG로 내보냅니다. 최종 완성 상태도 같은 캔버스입니다.\n문장 자체는 AE 편집 가능한 문자 레이어가 아닙니다. JSON 수정 후 새 폴더로 재생성해야 합니다.\nXML 구조·AE JavaScript mock 검증이며 실제 Adobe 가져오기/재생 검증이 아닙니다. 기존 완성본 재현 범위에 합산하지 않습니다.\n\n재실행: python engine/selftest_character_reveal_nle.py --source /원본/경로.mov --output-dir exports/새검증폴더 --ffmpeg /실제/ffmpeg\n상태만 재생성: python engine/export_nle.py exports/현재폴더/fixture.json --output-dir exports/새NLE폴더 --ffmpeg /실제/ffmpeg\n')
    print(json.dumps({k:summary[k] for k in ('pass','check_count','semantic_checks','character_reveal_states','pre_encode_RGB_max_absolute_difference')},ensure_ascii=False))
    return summary


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--source',required=True);ap.add_argument('--output-dir',required=True);ap.add_argument('--ffmpeg',default='ffmpeg');a=ap.parse_args();run(a.source,a.output_dir,a.ffmpeg)
