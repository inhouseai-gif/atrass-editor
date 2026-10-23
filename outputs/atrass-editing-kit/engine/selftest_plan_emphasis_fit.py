#!/usr/bin/env python3
"""Regression and finite raw-source examples for the new emphasis layout policy.

New template examples are not reference reconstructions. Existing outputs are
never overwritten. Machine checks do not create a manual visual approval.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, hashlib, json, math, subprocess, sys, tempfile
import av
import cv2
import numpy as np
from PIL import Image, ImageDraw
from compile_plan import compile_plan, EMPHASIS_FIT_POLICY
from render import Composer, SourceReader, render, sha

KIT=Path(__file__).resolve().parents[1]
FPS=Fraction(60000,1001);COUNT=150;DURATION=float(Fraction(COUNT,1)/FPS)
RAW_SHA='63ff90fb7c12b1440f832ba5a9e2ac3fd67e46f640de53a3494eba68c2cfdfff'
TEXTS={'short':'작은 행동의 힘','long':'관계를 바꾸는 것은 매일 반복하는 작은 행동입니다'}
BASELINE_RGBA_SHA='46c5cff705bbf607e1436ffc734d1e77a60956ce911bb66c58d2afb738c5d509'
BASELINE_COMPILER_SHA='cc60669b1b8883805ea6b4597a69a19c1585a94e371582704bc6868249e099a5'
cv2.setNumThreads(2)

def sec(i):return float(Fraction(i,1)/FPS)
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write_new(p,d):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf-8') as f:f.write(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def record(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}
def require(ok,message):
    if not ok:raise AssertionError(message)

def plan(source,text,source_type='video'):
    return {'schema':'atrass-edit-plan-v1','variant':'A','title':'새 문구 강조 템플릿 기능 예시',
            'media':{'rawA':{'path':str(source),'role':'source_footage',**({'type':'image'} if source_type=='image' else {})}},
            'fps':str(FPS),'takes':[{'id':'demo-raw','source':'rawA','source_in':0 if source_type=='image' else 18.083333333333332,
                       'source_out':DURATION if source_type=='image' else 18.083333333333332+DURATION,
                       'audio_source_in':18.05075,'geometry':{'scale':1 if source_type=='image' else .75,'tx':0,'ty':0},
                       'status':'new_text_function_example_not_reference_mapping'}],
            'audio_follow_takes':source_type!='image','events':[
                {'id':'body','template':'dialogue','in':0,'out':DURATION,'text':'새 문구 강조 템플릿 테스트'},
                {'id':'emphasis','template':'large_emphasis','in':sec(15),'out':sec(135),'text':text}],
            'coverage_checklist':{'scope':'finite_new_text_template_function_example','reference_reconstruction':False,
                                  'speech_semantic_alignment_approved':False,'full_video_automation_complete':False}}

def unit_checks():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='atrass-emphasis-fit-') as td:
        d=Path(td);asset=d/'still.png';Image.new('RGB',(1920,1080),'#304050').save(asset)
        def compile_case(name,text=TEXTS['short'],changes=None):
            p=plan(asset,text,'image')
            if changes:p['events'][1].update(changes)
            path=d/(name+'.plan.json');out=d/(name+'.project.json')
            # Deliberately allow a malformed NaN fixture to reach compiler validation.
            with path.open('x',encoding='utf-8') as f:f.write(json.dumps(p,ensure_ascii=False))
            compile_plan(path,out)
            q=read(out);return p,q,out,next(e for e in q['layers'] if e['id']=='emphasis')
        p,q,out,e=compile_case('short');comp=Composer(q,out.parent);im,xy=comp.image(e,sec(75))
        require(im.size==(870,141) and xy==(525,805) and hashlib.sha256(im.tobytes()).hexdigest()==BASELINE_RGBA_SHA,'Short text changed from actual pre-change baseline')
        checks.append({'name':'short_actual_prechange_pixel_and_position_preservation','pass':True,'size':im.size,'xy':xy,'RGBA_sha256':BASELINE_RGBA_SHA,'prechange_compiler_sha256':BASELINE_COMPILER_SHA})
        _,ql,ol,el=compile_case('long',TEXTS['long']);c=Composer(ql,ol.parent);natural_layer=copy.deepcopy(el);natural_layer.pop('glyph_height');natural_layer.pop('layout_fit')
        natural=c.text_mask(el['text'],natural_layer);fitted=c.text_mask(el['text'],el);expected=natural.resize((round(natural.width*el['glyph_height']/natural.height),el['glyph_height']),Image.Resampling.LANCZOS)
        require(fitted.tobytes()==expected.tobytes(),'Long text is not an entire-mask uniform resize')
        require('glyph_width' not in el and fitted.width<=1728 and fitted.height>=72,'Long fit dimensions invalid')
        require(abs(fitted.width-natural.width*fitted.height/natural.height)<=.5,'Aspect ratio changed beyond integer rounding')
        iim,pos=Composer(ql,ol.parent).image(el,sec(75));require(pos[0]>=96 and pos[0]+iim.width<=1824 and pos[1]+iim.height<=984,'Long text crosses safe area')
        checks.append({'name':'long_uniform_aspect_and_safe_area','pass':True,'natural_size':natural.size,'fitted_size':fitted.size,'xy':pos,'uniform_resample_pixels_identical':True})
        _,qs,op,es=compile_case('double-spaces','작은  행동의  힘');space_mask=Composer(qs,op.parent).text_mask(es['text'],es)
        require(es['text']=='작은  행동의  힘' and space_mask.width>im.width,'Internal spaces collapsed')
        _,qsl,osl,esl=compile_case('long-spaces',TEXTS['long'].replace(' ', '  ',2));cc=Composer(qsl,osl.parent);nl=copy.deepcopy(esl);nl.pop('glyph_height');nl.pop('layout_fit');nm=cc.text_mask(esl['text'],nl);sm=cc.text_mask(esl['text'],esl)
        require(esl['text'].count(' ')==TEXTS['long'].count(' ')+2 and sm.tobytes()==nm.resize(sm.size,Image.Resampling.LANCZOS).tobytes(),'Scaled internal spaces altered')
        checks.append({'name':'single_and_repeated_internal_spaces_survive_whole_mask_fit','pass':True,'short_single_space_width':im.width,'double_space_width':space_mask.width,'scaled_text':esl['text']})
        def reject(name,text=TEXTS['short'],changes=None):
            try:compile_case('invalid-'+name,text,changes)
            except (ValueError,TypeError) as exc:
                require(not (d/('invalid-'+name+'.project.json')).exists(),'Rejected input wrote a project')
                checks.append({'name':'reject_'+name,'pass':True,'message':str(exc)})
            else:raise AssertionError('Accepted invalid '+name)
        reject('minimum_height','오늘 우리가 서로에게 건네는 작은 말과 행동이 오랜 관계를 바꾸는 시작입니다')
        reject('blank','   ');reject('nonstring',42);reject('multiline','첫째 줄\n둘째 줄');reject('outer_spaces',' 시작')
        reject('unsupported_glyph','지원 안 되는 글자 '+chr(0x10ffff));reject('zero_font_size',changes={'font_size':0})
        reject('noninteger_font_size',changes={'font_size':150.5});reject('nan_tracking',changes={'tracking':float('nan')})
        reject('font_too_small',changes={'font_size':40});reject('negative_stroke',changes={'stroke_width':-1})
        for name,change in [('explicit_glyph_width',{'glyph_width':900}),('explicit_glyph_bbox',{'glyph_bbox_1080':[100,800,1800,940]}),
                            ('custom_position',{'position':[500,805]}),('custom_anchor',{'anchor':'center'}),
                            ('anisotropic_scale',{'scale_x':.5}),('scale_animation',{'scale_keys':[[0,1],[1,2]]}),
                            ('rotation',{'rotation':5}),('x_animation',{'x_keys':[[0,960],[1,1500]]}),
                            ('custom_fit_policy',{'fit_policy':{'minimum_glyph_height_px':1}})]:reject(name,changes=change)
        # Match renderer's actual epsilon + half-open convention at all 150 times.
        for frame in range(COUNT):
            t=sec(frame);active=[x['id'] for x in ql['layers'] if x['in']<=t+1e-9<x['out']]
            require(active==(['emphasis'] if 15<=frame<135 else ['body-part1'] if frame<15 else ['body-part2']),'Caption suppression boundary changed')
        checks.append({'name':'all_150_halfopen_memberships_and_dialogue_restore','pass':True,'emphasis_frames':[15,135]})
        before=out.read_bytes();source_before=asset.read_bytes()
        try:compile_plan(d/'short.plan.json',out)
        except ValueError:pass
        else:raise AssertionError('Compiler overwrote existing output')
        require(out.read_bytes()==before and asset.read_bytes()==source_before,'Existing file changed')
        checks.append({'name':'overwrite_rejection_preserves_project_and_source_bytes','pass':True})
        require('reference_range_frames' not in ql and not ql['release_ready'],'Template example misclassified as reference coverage/release')
        checks.append({'name':'no_reference_reconstruction_or_release_claim','pass':True})
    return checks

def example(a,name,text):
    pd,ed,rd=map(Path,[a.project_dir,a.evidence_dir,a.render_dir]);pp=pd/(name+'.plan.json');project=pd/(name+'.project.json');out=rd/(name+'.mp4')
    p=plan(Path(a.raw).resolve(),text);p['media']['rawA']['sha256']=RAW_SHA;write_new(pp,p);compile_plan(pp,project)
    q=read(project);require('reference_range_frames' not in q,'Unexpected reference range')
    report=render(project,out,draft=True,ffmpeg=a.ffmpeg);require(report['frame_count']==COUNT,'Wrong rendered frame count')
    samples={};rows=[];comp=Composer(q,project.parent);source=SourceReader(a.raw)
    with av.open(str(out)) as container:
        stream=container.streams.video[0];stream.codec_context.thread_count=2
        require(stream.average_rate==FPS and (stream.width,stream.height)==(1920,1080),'Output format mismatch')
        for i,f in enumerate(container.decode(stream)):
            require(i<COUNT and abs(float(f.pts*f.time_base)-sec(i))<1e-8,'Output PTS/frame count mismatch')
            t=sec(i);active=[e for e in q['layers'] if e['in']<=t+1e-9<e['out']]
            require([e['id'] for e in active]==(['emphasis'] if 15<=i<135 else ['body-part1'] if i<15 else ['body-part2']),'Wrong output layer membership')
            for e in active:
                im,(x,y)=comp.image(e,t)
                require(x>=0 and y>=0 and x+im.width<=1920 and y+im.height<=1080,'Layer clipped by frame')
                if e['id']=='emphasis':require(x>=96 and x+im.width<=1824 and y+im.height<=984,'Emphasis outside safe area')
            raw,chosen=source.get(q['clips'][0]['source_in']+t)
            base=cv2.warpAffine(raw,np.array([[.75,0,0],[0,.75,0]],np.float32),(1920,1080),flags=cv2.INTER_AREA,borderMode=cv2.BORDER_CONSTANT)
            expected=comp.compose(base,active,t);actual=f.to_ndarray(format='rgb24');diff=np.abs(actual.astype(np.int16)-expected.astype(np.int16))
            rows.append({'frame':i,'pts':f.pts,'time_base':str(f.time_base),'active_ids':[e['id'] for e in active],
                         'raw_decoded_seconds':chosen,'full_frame_MAE':float(diff.mean()),'caption_band_MAE':float(diff[800:1020].mean())})
            if i in [0,14,15,75,134,135,149]:samples[i]=actual
    source.close();require(len(rows)==COUNT,'Decoded frame count mismatch')
    require(max(r['full_frame_MAE'] for r in rows)<5 and max(r['caption_band_MAE'] for r in rows)<7,'Unexpected encode/render difference')
    ed.mkdir(parents=True,exist_ok=True)
    for i in [15,75,134]:Image.fromarray(samples[i]).save(ed/(name+'-f%03d.png'%i))
    strips=Image.new('RGB',(1920,4*300),'#242424');draw=ImageDraw.Draw(strips)
    for n,i in enumerate([14,15,134,135]):
        y=n*300;draw.text((10,y+4),'%s / output f%d / %s / 1:1 pixels'%(name,i,','.join(rows[i]['active_ids'])),fill='white');strips.paste(Image.fromarray(samples[i][790:1070]),(0,y+20))
    strips.save(ed/(name+'-boundary-strips.png'))
    result={'name':name,'text':text,'plan':record(pp),'project':record(project),'render':record(out),'render_report':record(out.with_suffix('.render.json')),
            'decoded_frames':len(rows),'frame_rows':rows,'full_frame_MAE_max':max(r['full_frame_MAE'] for r in rows),
            'caption_band_MAE_max':max(r['caption_band_MAE'] for r in rows),'fit':next(e for e in q['layers'] if e['id']=='emphasis')['layout_fit'],
            'sample_files':[record(ed/(name+'-f%03d.png'%i)) for i in [15,75,134]]+[record(ed/(name+'-boundary-strips.png'))],
            'reference_reconstruction_frames':0,'manual_visual_review':False,'direct_listened_seconds':0}
    write_new(ed/(name+'-decode.json'),result);return result

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--raw',required=True);ap.add_argument('--ffmpeg',default='ffmpeg')
    ap.add_argument('--project-dir',default=str(KIT/'projects/emphasis-template-examples'))
    ap.add_argument('--evidence-dir',default=str(KIT/'evidence/emphasis-template-examples'))
    ap.add_argument('--render-dir',default=str(KIT.parent/'reconstruction/emphasis-template-examples'))
    a=ap.parse_args();pd,ed,rd=map(Path,[a.project_dir,a.evidence_dir,a.render_dir])
    targets=[ed/'machine-validation.json',ed/'existing-11-regression.log']
    for name in TEXTS:
        targets += [pd/(name+'.plan.json'),pd/(name+'.project.json'),rd/(name+'.mp4'),rd/(name+'.render.json'),ed/(name+'-decode.json'),ed/(name+'-boundary-strips.png')]
        targets += [ed/(name+'-f%03d.png'%i) for i in [15,75,134]]
    for p in targets:
        if p.exists():raise ValueError('Refusing existing example output: '+str(p))
    require(sha(a.raw)==RAW_SHA,'Provided raw source SHA mismatch')
    checks=unit_checks()
    legacy=subprocess.run([sys.executable,str(KIT/'engine/selftest_plan.py'),'--ffmpeg',a.ffmpeg],capture_output=True,text=True,check=True)
    start=legacy.stdout.rfind('{\n  "status"');old=json.loads(legacy.stdout[start:]);require(old['check_count']==11 and old['status']=='passed','Existing 11 checks failed')
    ed.mkdir(parents=True,exist_ok=True)
    with (ed/'existing-11-regression.log').open('x',encoding='utf-8') as f:f.write(legacy.stdout+'\n'+legacy.stderr)
    examples=[example(a,n,t) for n,t in TEXTS.items()]
    report={'schema':'atrass-emphasis-template-machine-validation-v1','pass':True,'new_policy':EMPHASIS_FIT_POLICY,
            'checks':checks,'check_count':len(checks),'existing_regression':old,'examples':[{k:v for k,v in x.items() if k!='frame_rows'} for x in examples],
            'input_bindings':{'raw':record(a.raw),'compiler':record(KIT/'engine/compile_plan.py'),'renderer':record(KIT/'engine/render.py'),
                              'font':record(KIT/'assets/fonts/NotoSansKR[wght].ttf'),'selftest':record(__file__)},
            'total_output_frames_decoded':sum(x['decoded_frames'] for x in examples),'reference_reconstruction_frames':0,
            'manual_visual_review':False,'release_ready':False,
            'limits':['New text template policy and candidate fonts, not measured original emphasis settings.',
                      'Raw speech is demonstration media; the new overlay text is not an approved semantic summary of that speech.',
                      'All-frame decoding and numeric comparisons do not mean all-frame direct visual review or listening.',
                      'Fixed one-line position only. Explicit manual geometry/effects must use the primitive template.']}
    write_new(ed/'machine-validation.json',report)
    print(json.dumps({'pass':True,'checks':len(checks),'existing_checks':11,'raw_example_frames':300,'reference_reconstruction_frames':0,'report':str(ed/'machine-validation.json')},ensure_ascii=False))

if __name__=='__main__':main()
