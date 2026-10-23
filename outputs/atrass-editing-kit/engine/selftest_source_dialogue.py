#!/usr/bin/env python3
"""Meaningful source/timeline regressions, with optional real-raw example rendering."""
from pathlib import Path
from fractions import Fraction
import argparse, copy, json, tempfile
from assemble_edit_plan import assemble,digest
from compile_plan import compile_plan


def put(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def tests():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='atrass-source-dialogue-') as folder:
        root=Path(folder);raw=root/'raw.mov';raw.write_bytes(b'hash-test-source-not-a-video')
        # Duplicate ASR id is deliberate: only array/word indices are identity.
        transcript={'input':str(raw),'source_sha256':digest(raw),'segments':[
          {'id':0,'start':1,'end':3,'text':' 하나 둘 셋 넷','words':[{'word':' 하나','start':1,'end':1.5},{'word':' 둘','start':1.5,'end':2},{'word':' 셋','start':2,'end':2.5},{'word':' 넷','start':2.5,'end':3}]},
          {'id':0,'start':5,'end':6,'text':' 다른 문장','words':[{'word':' 다른','start':5,'end':5.5},{'word':' 문장','start':5.5,'end':6}]},
          {'id':1,'start':7,'end':9,'text':' 단어 시각이 없는 문장'}]}
        tp=root/'transcript.json';put(tp,transcript)
        job={'schema':'atrass-job-v1','variant':'A','raw':{'path':str(raw),'sha256':digest(raw),'duration_seconds':12},'files':{'transcript':'transcript.json','editorial_candidates':'candidates.json'}};jp=root/'job.json';put(jp,job)
        candidate=root/'candidates.json';put(candidate,{'candidates':[{'decision':None}]});candidate_hash=digest(candidate)
        def take(id,a,b,**kw):return {'id':id,'action':'keep','source_in':a,'source_out':b,'reason':'synthetic timing regression','evidence':'Generated timestamps; no editorial approval',**kw}
        def cap(id,tid,index,**kw):return {'id':id,'take_id':tid,'segment_index':index,**kw}
        base={'schema':'atrass-source-dialogue-v1','job_sha256':digest(jp),'transcript_sha256':digest(tp),'fps':'60000/1001','takes':[take('a',1,3),take('b',5,6)],'captions':[cap('a-line','a',0),cap('b-line','b',1)]}
        serial=0
        def run(d):
            nonlocal serial
            serial+=1;dp=root/f'd{serial}.json';out=root/f'p{serial}.json';put(dp,d);return assemble(jp,dp,out),out
        def bad(label,change):
            d=copy.deepcopy(base);change(d)
            try:run(d)
            except ValueError:checks.append(label)
            else:raise AssertionError('Missing rejection: '+label)
        p,out=run(base);compile_plan(out,root/'compiled.json');compiled=json.loads((root/'compiled.json').read_text(encoding='utf-8'))
        assert [e['text'] for e in p['events']]==['하나 둘 셋 넷','다른 문장']
        assert p['source_dialogue_assembly']['output_frame_count']==round(compiled['duration']*float(Fraction(p['fps'])))
        assert [(e['in'],e['out']) for e in p['events']]==[(e['in'],e['out']) for e in compiled['layers']]
        checks.append('basic source words compile to identical frame intervals; duplicate ASR ids safe')
        reverse=copy.deepcopy(base);reverse['takes'].reverse();q,_=run(reverse)
        assert q['events'][0]['in']>q['events'][1]['in']
        for e in q['events']:
            m=next(x for x in q['source_dialogue_assembly']['take_mapping'] if x['take_id']==e['source_dialogue_binding']['take_id'])
            t=(e['in']+e['out'])/2;s=m['audio_source_range_requested'][0]+(t-m['output_frames'][0]/float(Fraction(q['fps'])))*m['rate']
            a,b=e['source_dialogue_binding']['source_range'];assert a<=s<b
        checks.append('reorder remaps captions with their source speech')
        reuse=copy.deepcopy(base);reuse['takes'].append(take('again',1,3));reuse['captions'].append(cap('again-line','again',0));q,_=run(reuse)
        assert q['events'][2]['text']==q['events'][0]['text'] and q['events'][2]['in']>q['events'][0]['out'];checks.append('same-source reuse has separate occurrence mapping')
        split=copy.deepcopy(base);split['takes']=[take('left',1,2),take('right',2,3)];split['captions']=[cap('left-line','left',0,word_range=[0,2]),cap('right-line','right',0,word_range=[2,4])];q,_=run(split)
        assert q['events'][0]['out']==q['events'][1]['in'] and [x['text'] for x in q['events']]==['하나 둘','셋 넷'];checks.append('word-boundary split is half-open with no repeated or lost selected words')
        off=copy.deepcopy(base);off['takes'][0]=take('a',.5,2.5,audio_source_in=1);q,_=run(off);assert q['events'][0]['in']==0 and q['takes'][0]['source_in']==.5 and q['takes'][0]['audio_source_in']==1;checks.append('captions follow independent audio origin, not video origin')
        speed=copy.deepcopy(base);speed['takes'][0]['rate']=.5;q,_=run(speed);assert q['events'][0]['out']>3.9;checks.append('audio rate remaps caption duration')
        no_words=copy.deepcopy(base);no_words['takes']=[take('a',7,9)];no_words['captions']=[cap('full','a',2)];q,_=run(no_words);assert q['events'][0]['text']==transcript['segments'][2]['text'];checks.append('whole cue without words is supported without invented partial text')
        no_words['takes']=[take('a',7,8)]
        try:run(no_words)
        except ValueError:checks.append('partial no-word cue rejected')
        else:raise AssertionError('Partial cue was copied')
        no_words['captions']=[cap('partial','a',2,source_range=[7,8],text='명시한 수정 문구',evidence='Synthetic explicit wording decision')];q,_=run(no_words);assert q['events'][0]['text']=='명시한 수정 문구';checks.append('partial no-word cue requires explicit text range and evidence')
        drop=copy.deepcopy(base);drop['takes'].insert(1,{'id':'omit','action':'drop','source_in':3,'source_out':4,'reason':'synthetic fixture only','evidence':'Not a real retake judgement'});q,_=run(drop);assert len(q['takes'])==2 and len(q['source_dialogue_assembly']['drop_decisions'])==1;checks.append('explicit drop records reason but creates no output clip')
        bad('cut through selected word rejected',lambda d:d['takes'][0].update(source_in=1.2))
        bad('repeated take occurrence ID rejected',lambda d:d['takes'][1].update(id='a'))
        bad('ASR id field cannot substitute array index',lambda d:d['captions'][0].update(segment_id=0))
        bad('boolean segment index rejected',lambda d:d['captions'][0].update(segment_index=True))
        bad('invalid word indices rejected',lambda d:d['captions'][0].update(word_range=[2,1]))
        bad('missing editorial reason rejected',lambda d:d['takes'][0].update(reason=''))
        bad('unbound wording override rejected',lambda d:d['captions'][0].update(text='말하지 않은 새 문장'))
        bad('overlapping captions rejected',lambda d:d['captions'].append(cap('overlap','a',0,word_range=[0,1])))
        bad('stale job hash rejected',lambda d:d.update(job_sha256='0'*64))
        bad('stale ASR hash rejected',lambda d:d.update(transcript_sha256='0'*64))
        bad('unsupported audio speed rejected',lambda d:d['takes'][0].update(rate=3))
        bad('source interval outside duration rejected',lambda d:d['takes'][0].update(source_out=13))
        bad('caption on dropped occurrence rejected',lambda d:d['takes'][0].update(action='drop'))
        bad('undeclared caption omission rejected',lambda d:d['captions'].pop())
        none=copy.deepcopy(base);none['captions'].pop();none['takes'][1]['caption_omission_reason']='Synthetic intentional non-captioned pause';q,_=run(none);assert len(q['events'])==1;checks.append('explicit omission reason preserved')
        tiny=copy.deepcopy(base);tiny['takes']=[take('a',1,2)];tiny['captions']=[cap('tiny','a',0,source_range=[1.0001,1.0002],text='짧음',evidence='Synthetic quantization conflict')]
        try:run(tiny)
        except ValueError:checks.append('subframe collapsed caption rejected')
        else:raise AssertionError('Collapsed caption survived')
        rounding=copy.deepcopy(base);rounding['fps']='60';rounding['takes']=[take('a',1,1.04)];rounding['captions']=[cap('rounding-tail','a',0,source_range=[1,1.04],text='선택한 발화',evidence='Synthetic full selected source range')]
        try:run(rounding)
        except ValueError as exc:
            assert 'rounding would truncate' in str(exc);checks.append('frame rounding cannot silently truncate selected speech tail')
        else:raise AssertionError('Selected speech tail truncated')
        before=digest(out)
        try:assemble(jp,root/'d1.json',out)
        except ValueError:checks.append('existing output preserved')
        else:raise AssertionError('Overwrite allowed')
        assert digest(out)==before and digest(candidate)==candidate_hash;checks.append('output and unresolved editorial candidates bytes preserved')
        raw.write_bytes(b'changed media')
        try:run(base)
        except ValueError:checks.append('raw source byte change rejected')
        else:raise AssertionError('Changed raw accepted')
        raw.write_bytes(b'hash-test-source-not-a-video');transcript['source_sha256']='0'*64;put(tp,transcript);changed=copy.deepcopy(base);changed['transcript_sha256']=digest(tp)
        try:run(changed)
        except ValueError:checks.append('transcript/source identity mismatch rejected even with fresh transcript hash')
        else:raise AssertionError('Wrong source ASR accepted')
    return checks


def validate_example(plan_path,project_path,video_path,evidence_dir,ffmpeg):
    """Full decode + independent assembly-frame/opaque-caption and waveform checks.

    Saves selected images for a separate direct visual review. This function does
    not mark images viewed, certify ASR wording, or claim listening occurred.
    """
    import math,subprocess
    import av,numpy as np
    from PIL import Image,ImageDraw,ImageFont
    from render import Composer,resolve
    plan_path=Path(plan_path).resolve();project_path=Path(project_path).resolve();video_path=Path(video_path).resolve();dest=Path(evidence_dir).resolve()
    if dest.exists():raise ValueError('Choose a new example evidence directory')
    plan=json.loads(plan_path.read_text(encoding='utf-8'));p=json.loads(project_path.read_text(encoding='utf-8'));assembly=plan['source_dialogue_assembly'];fps=Fraction(p['fps']);count=assembly['output_frame_count']
    assert 'reference_range_frames' not in p and not p['release_ready']
    render_record=json.loads(video_path.with_suffix('.render.json').read_text(encoding='utf-8'))
    assert render_record['project_sha256']==digest(project_path) and render_record['output_sha256']==digest(video_path)
    assert render_record['frame_count']==count and digest(plan_path)==digest(Path(p['plan_source']))
    assert assembly['input_sha256']['assembler']==digest(Path(__file__).with_name('assemble_edit_plan.py'))
    source=resolve(project_path.parent,p['media']['raw']['path']);assert digest(source)==assembly['input_sha256']['raw']
    captions=assembly['captions'];by_id={x['id']:x for x in p['layers']};comp=Composer(p,project_path.parent)
    sample_frames={0,count-1}
    for m in assembly['take_mapping']:
        a,b=m['output_frames'];sample_frames.update([a,(a+b)//2,b-1])
    images={};roi_errors=[];source_checks=0
    dest.mkdir(parents=True)
    with av.open(str(video_path)) as container:
        stream=container.streams.video[0];stream.codec_context.thread_count=2;n=0
        for n,frame in enumerate(container.decode(stream),1):
            index=n-1;t=float(Fraction(index,1)/fps)
            assert (frame.width,frame.height)==(1920,1080) and abs(float(frame.pts*frame.time_base)-t)<1e-6
            expected=[x for x in captions if x['output_frames'][0]<=index<x['output_frames'][1]]
            actual=[e for e in p['layers'] if e['in']<=t+1e-9<e['out']]
            assert {e['id'] for e in actual}=={e['id'] for e in expected}
            arr=frame.to_ndarray(format='rgb24')
            for c in expected:
                e=by_id[c['id']];assert e['text']==c['text'];im,xy=comp.image(e,t);assert im.getchannel('A').getextrema()==(255,255)
                x,y=xy;observed=arr[y:y+im.height,x:x+im.width].astype(np.float32);reference=np.asarray(im.convert('RGB'),dtype=np.float32)
                mae=float(np.mean(np.abs(observed-reference)));assert mae<4,('caption pixels',index,mae);roi_errors.append(mae)
                audio=next(a for a in p['audio'] if a['in']<=t+1e-9<a['out']);st=audio['source_in']+(t-audio['in'])*audio.get('rate',1)
                assert c['source_range'][0]-1e-8<=st<c['source_range'][1]+1e-8;source_checks+=1
            if index in sample_frames:
                im=Image.fromarray(arr);path=dest/f'frame-{index:06d}.png';im.save(path);images[index]=path
        assert n==count
    # Machine waveform agreement validates actual muxed speech ordering, not wording.
    def pcm(path,start=None,duration=None):
        cmd=[ffmpeg,'-v','error','-threads','2']
        if start is not None:cmd+=['-ss',f'{start:.9f}','-t',f'{duration:.9f}']
        cmd+=['-i',str(path),'-vn','-ac','1','-ar','48000','-f','f32le','pipe:1']
        return np.frombuffer(subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout,dtype=np.float32)
    output_audio=pcm(video_path);waveforms=[]
    for a in p['audio']:
        assert a.get('rate',1)==1,'Example waveform checker only supports rate=1; unit tests cover rate remapping'
        raw_audio=pcm(source,a['source_in'],a['out']-a['in']);offset=round(a['in']*48000);available=min(len(raw_audio),len(output_audio)-offset);margin=2400
        assert available>2*margin
        x=raw_audio[margin:available-margin:4].astype(float);y=output_audio[offset+margin:offset+available-margin:4].astype(float)
        corr=float(np.corrcoef(x,y)[0,1]);assert corr>.97,('waveform order',a['id'],corr)
        waveforms.append({'audio_id':a['id'],'source_in':a['source_in'],'output_span':[a['in'],a['out']],'sampled_correlation':corr,'excluded_edge_samples_each_side':margin,'sample_stride':4,'direct_listening':False})
    font=ImageFont.truetype(str(resolve(project_path.parent,p['fonts']['body']['path'])),18)
    sheet=Image.new('RGB',(1920,math.ceil(len(images)/3)*390),'#202020');draw=ImageDraw.Draw(sheet)
    for j,(index,path) in enumerate(sorted(images.items())):
        x=(j%3)*640;y=(j//3)*390;sheet.paste(Image.open(path).resize((640,360)),(x,y+30));draw.text((x+6,y+5),f'frame {index} | output {index/float(fps):.6f}s',font=font,fill='white')
    sheet.save(dest/'contact-sheet.jpg',quality=94)
    strips=Image.new('RGB',(1920,len(images)*154),'#202020');draw=ImageDraw.Draw(strips)
    for j,(index,path) in enumerate(sorted(images.items())):
        strips.paste(Image.open(path).crop((0,910,1920,1034)),(0,j*154+30));draw.text((8,j*154+5),f'frame {index}',font=font,fill='white')
    strips.save(dest/'caption-boundaries-native.png')
    result={'status':'machine_checks_passed_visual_review_pending','raw_visual_reference_coverage_frames':0,'decoded_frames':count,'pts_checked_frames':count,'caption_frame_source_mapping_checks':source_checks,'caption_roi_mae_mean':float(np.mean(roi_errors)),'caption_roi_mae_max':max(roi_errors),'waveform_checks':waveforms,'selected_frames_saved':sorted(images),'selected_frames_viewed':[],'direct_listening_seconds':0,'same_A_source_functional_demo_not_new_footage_semantic_approval':True,'sha256':{'plan':digest(plan_path),'project':digest(project_path),'video':digest(video_path),'render_record':digest(video_path.with_suffix('.render.json')),'assembler':digest(Path(__file__).with_name('assemble_edit_plan.py')),'selftest':digest(Path(__file__)),'compiler':digest(Path(__file__).with_name('compile_plan.py')),'renderer':digest(Path(__file__).with_name('render.py'))},'image_sha256':{str(x.relative_to(dest)):digest(x) for x in dest.iterdir() if x.suffix in ('.png','.jpg')}}
    with (dest/'machine-validation.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path);ap.add_argument('--validate-example',nargs=3,metavar=('PLAN','PROJECT','VIDEO'));ap.add_argument('--evidence-dir',type=Path);ap.add_argument('--ffmpeg',default='ffmpeg');a=ap.parse_args()
    if a.validate_example:
        if not a.evidence_dir:ap.error('--evidence-dir is required with --validate-example')
        result=validate_example(*a.validate_example,a.evidence_dir,a.ffmpeg);print(json.dumps(result,ensure_ascii=False,indent=2));return
    if a.output and a.output.exists():ap.error('Choose a new selftest result path')
    checks=tests();result={'status':'passed','checks':checks,'check_count':len(checks),'scope':'Synthetic timing/compiler checks only; no media or semantic approval'}
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
