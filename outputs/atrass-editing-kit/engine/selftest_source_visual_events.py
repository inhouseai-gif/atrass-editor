#!/usr/bin/env python3
"""Source-anchored visual-event regressions; no semantic or visual approval."""
from pathlib import Path
from fractions import Fraction
import argparse,copy,json,tempfile
from assemble_edit_plan import assemble,digest
from compile_plan import compile_plan

def write(path,value):
    with Path(path).open('x',encoding='utf-8')as h:json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n')
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def tests():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='atrass-source-visual-')as folder:
        root=Path(folder);raw=root/'raw.mov';raw.write_bytes(b'synthetic-timing-source')
        transcript={'source_sha256':digest(raw),'segments':[
            {'id':0,'start':1,'end':3,'text':'하나 둘 셋 넷','words':[{'word':' 하나','start':1,'end':1.5},{'word':' 둘','start':1.5,'end':2},{'word':' 셋','start':2,'end':2.5},{'word':' 넷','start':2.5,'end':3}]},
            {'id':0,'start':5,'end':6,'text':'다른 문장','words':[{'word':' 다른','start':5,'end':5.5},{'word':' 문장','start':5.5,'end':6}]},
            {'id':1,'start':7,'end':9,'text':'단어 시각 없는 문장'}]}
        tp=root/'transcript.json';write(tp,transcript);jp=root/'job.json';write(jp,{'schema':'atrass-job-v1','variant':'A','raw':{'path':str(raw),'sha256':digest(raw),'duration_seconds':12},'files':{'transcript':'transcript.json'}})
        unresolved=root/'editorial-candidates.json';write(unresolved,{'candidates':[{'decision':None}]});unchanged=digest(unresolved)
        def take(tid,a,b,**kw):return {'id':tid,'action':'keep','source_in':a,'source_out':b,'reason':'Synthetic timing fixture','evidence':'No editorial approval',**kw}
        def cue(cid,tid,index,**kw):return {'id':cid,'take_id':tid,'segment_index':index,**kw}
        def visual(vid,tid,index,**kw):return {**cue(vid,tid,index),'template':'large_emphasis','reason':'Explicit synthetic visual intent','evidence':'Synthetic source word timestamps',**kw}
        base={'schema':'atrass-source-dialogue-v1','job_sha256':digest(jp),'transcript_sha256':digest(tp),'fps':'60','takes':[take('a',1,3),take('b',5,6)],'captions':[cue('line-a','a',0),cue('line-b','b',1)],'visual_events':[visual('emph','a',0,word_range=[1,3]),visual('label','a',0,template='situation_label',text='[상황 예시]',position=[960,700],anchor='top_center')]}
        serial=0
        def run(d):
            nonlocal serial
            serial+=1;dp=root/f'd{serial}.json';op=root/f'p{serial}.json';write(dp,d);return assemble(jp,dp,op),op,dp
        def ledger(p,vid='emph'):return next(v for v in p['source_visual_assembly']['events']if v['id']==vid)
        def check(label,condition):assert condition,label;checks.append(label)
        def bad(label,change,expected=None):
            d=copy.deepcopy(base);change(d)
            try:run(d)
            except ValueError as exc:
                if expected:assert expected in str(exc),(label,str(exc))
                checks.append(label)
            else:raise AssertionError('Missing rejection: '+label)
        p,plan,dp=run(base);check('ASR array indices and selected word IDs drive emphasis, not repeated ASR ids',ledger(p)['output_frames']==[30,90]and ledger(p)['text']=='둘 셋'and len(ledger(p)['word_keys'])==2)
        check('independent label can coexist with emphasis on one occurrence',ledger(p,'label')['output_frames']==[0,120])
        project=root/'compiled.json';compile_plan(plan,project);q=read(project)
        emph=next(e for e in q['layers']if e['id']=='emph');label=next(e for e in q['layers']if e['id']=='label')
        check('compiler preserves visual binding and actual emphasis half-open interval',emph['in']==.5 and emph['out']==1.5 and emph['source_visual_binding']['take_id']=='a')
        check('emphasis suppresses only overlapping dialogue, independent label survives',[(e['in'],e['out'])for e in q['layers']if e['kind']=='caption'and e['source_dialogue_binding']['take_id']=='a']==[(0,.5),(1.5,2)] and label['in']==0 and label['out']==2)
        rev=copy.deepcopy(base);rev['takes'].reverse();r,_,_=run(rev);check('reorder remaps same source emphasis and label to their occurrence',ledger(r)['output_frames']==[90,150]and ledger(r,'label')['output_frames']==[60,180])
        for speed,span in [(2,[15,45]),(.5,[60,180])]:
            d=copy.deepcopy(base);d['takes'][0]['rate']=speed;r,_,_=run(d);check('source visual follows audio rate '+str(speed),ledger(r)['output_frames']==span)
        d=copy.deepcopy(base);d['takes'][0].update(source_in=.5,source_out=2.5,audio_source_in=1);r,_,_=run(d);check('independent audio origin controls visuals rather than video source time',ledger(r)['output_frames']==[30,90]and r['takes'][0]['source_in']==.5)
        d=copy.deepcopy(base);d['takes'].append(take('again',1,3));d['captions'].append(cue('again-line','again',0));d['visual_events'].append(visual('again-emph','again',0,word_range=[1,3]));r,_,_=run(d);check('source reuse gets distinct occurrence and output intervals',ledger(r,'again-emph')['output_frames']==[210,270]and ledger(r,'again-emph')['source_range']==ledger(r)['source_range'])
        d=copy.deepcopy(base);d['takes']=[take('left',1,2),take('right',2,3)];d['captions']=[cue('left-line','left',0,word_range=[0,2]),cue('right-line','right',0,word_range=[2,4])];d['visual_events']=[visual('left-emph','left',0,word_range=[0,2]),visual('right-emph','right',0,word_range=[2,4])];r,_,_=run(d);check('word boundary split is adjacent half-open, no duplicated selected words',[x['output_frames']for x in r['source_visual_assembly']['events']]==[[0,60],[60,120]]and r['source_visual_assembly']['events'][0]['word_keys'][-1]!=r['source_visual_assembly']['events'][1]['word_keys'][0])
        d=copy.deepcopy(base);d['takes']=[take('a',7,9)];d['captions']=[cue('line-a','a',2)];d['visual_events']=[visual('emph','a',2)];r,_,_=run(d);check('whole no-word cue retains original text without inferred substring',ledger(r)['text']==transcript['segments'][2]['text'])
        d['takes']=[take('a',7,8,caption_omission_reason='Synthetic no-body caption')];d['captions']=[]
        try:run(d)
        except ValueError as ex:check('partial no-word cue is rejected without explicit wording','crosses' in str(ex))
        else:raise AssertionError('Partial cue accepted')
        d['visual_events'][0].update(source_range=[7,8],text='명시한 부분문구');r,_,_=run(d);check('explicit partial text plus source range and evidence remains declared',ledger(r)['text_basis']=='explicit_text_and_range_not_inferred_asr_substring')
        d=copy.deepcopy(base);d['visual_events']=[];r,_,_=run(d);check('explicit empty visual decisions do not imply whole style completion',r['source_visual_assembly']['events']==[]and r['release_ready']is False)
        def only_visual(d):d['captions']=[];[t.update(caption_omission_reason='Synthetic visual timing guard')for t in d['takes']];d['visual_events']=d['visual_events'][:1]
        def cross(d):only_visual(d);d['takes'][0]['source_out']=2
        bad('visual word selection crossing selected audio cut rejected',cross,'crosses')
        def round_tail(d):only_visual(d);d['takes'][0]['source_out']=1.04;d['visual_events'][0].pop('word_range');d['visual_events'][0].update(source_range=[1,1.04],text='명시문구')
        bad('visual tail truncated by output-frame rounding rejected',round_tail,'rounding would truncate')
        def collapsed(d):only_visual(d);d['visual_events'][0].pop('word_range');d['visual_events'][0].update(source_range=[1.0001,1.0002],text='짧음')
        bad('visual selection collapsed by frame quantization rejected',collapsed,'collapsed')
        bad('duplicate take occurrence rejected',lambda d:d['takes'][1].update(id='a'))
        bad('missing take occurrence rejected',lambda d:d['visual_events'][0].update(take_id='absent'))
        def dropped(d):d['takes'][0]['action']='drop';d['captions']=d['captions'][1:]
        bad('visual targeting explicit drop rejected',dropped)
        bad('visual/caption ID collision rejected',lambda d:d['visual_events'][0].update(id='line-a'))
        bad('overlapping same-template visuals rejected',lambda d:d['visual_events'].append(visual('collision','a',0,word_range=[1,4])))
        bad('missing semantic reason rejected',lambda d:d['visual_events'][0].pop('reason'))
        bad('missing source evidence rejected',lambda d:d['visual_events'][0].pop('evidence'))
        bad('absolute output-time fields rejected',lambda d:d['visual_events'][0].update(**{'in':0,'out':1}))
        bad('unsupported visual type rejected',lambda d:d['visual_events'][0].update(template='image_asset'))
        bad('unsupported animation keys rejected',lambda d:d['visual_events'][0].update(opacity_keys=[[0,0],[1,1]]))
        bad('large-emphasis custom geometry explicitly rejected',lambda d:d['visual_events'][0].update(position=[10,10]))
        bad('label needs explicit position',lambda d:d['visual_events'][1].pop('position'))
        bad('label unsupported anchor rejected',lambda d:d['visual_events'][1].update(anchor='bottom_right'))
        bad('label nonfinite position rejected',lambda d:d['visual_events'][1].update(position=[float('nan'),0]))
        bad('boolean ASR index rejected',lambda d:d['visual_events'][0].update(segment_index=True))
        bad('word range cannot be combined with source range',lambda d:d['visual_events'][0].update(source_range=[1,2],text='x'))
        bad('source selection outside declared cue rejected',lambda d:d['visual_events'][0].update(segment_index=1))
        bad('empty visual text override rejected',lambda d:d['visual_events'][0].update(text=''))
        bad('control-character visual text rejected',lambda d:d['visual_events'][0].update(text='두\n줄'))
        before=digest(plan)
        try:assemble(jp,dp,plan)
        except ValueError:check('existing plan bytes preserved',digest(plan)==before)
        else:raise AssertionError('Overwrite allowed')
        check('unresolved deletion candidates remain byte-identical',digest(unresolved)==unchanged)
        bad('stale decision job SHA rejected',lambda d:d.update(job_sha256='0'*64))
        bad('stale transcript SHA rejected',lambda d:d.update(transcript_sha256='0'*64))
        # The source adapter now returns dialogue + visuals; CLI counts must not
        # call all events captions. Exercise the actual entry point on the fixture.
        import contextlib,io,sys
        from assemble_edit_plan import main as assemble_main
        stdout=io.StringIO();saved_argv=sys.argv
        try:
            sys.argv=['assemble_edit_plan.py','--job',str(jp),'--decisions',str(dp),'--output',str(root/'cli-plan.json')]
            with contextlib.redirect_stdout(stdout):code=assemble_main()
        finally:sys.argv=saved_argv
        summary=json.loads(stdout.getvalue());check('CLI reports dialogue, visual and total event counts separately',code==0 and summary['captions']==2 and summary['visual_events']==2 and summary['events']==4)

    return checks


def validate_example(plan_path,project_path,video_path,dest,ffmpeg,assembler_snapshot=None):
    """Decode all demo frames; compare own recipe and independent source ledger.

    This supports the real rate=1 demos below, not a universal output certifier.
    It saves images but never records them viewed or marks release ready.
    """
    import math,subprocess
    import av,cv2,numpy as np
    from PIL import Image,ImageDraw,ImageFont
    from render import Composer,SourceReader,resolve
    cv2.setNumThreads(2)
    plan_path=Path(plan_path).resolve();project_path=Path(project_path).resolve();video_path=Path(video_path).resolve();dest=Path(dest).resolve()
    if dest.exists():raise ValueError('Choose a new visual example evidence directory')
    plan=read(plan_path);p=read(project_path);assembly=plan['source_dialogue_assembly'];visuals=plan['source_visual_assembly']['events'];fps=Fraction(p['fps']);count=assembly['output_frame_count'];rr=read(video_path.with_suffix('.render.json'))
    assert rr['project_sha256']==digest(project_path)and rr['output_sha256']==digest(video_path)and rr['frame_count']==count
    assembler_at_build=Path(assembler_snapshot).resolve() if assembler_snapshot else Path(__file__).with_name('assemble_edit_plan.py')
    assert assembly['input_sha256']['assembler']==digest(assembler_at_build),'Build-time assembler hash mismatch'
    assert plan['source_visual_assembly']['input_sha256']==assembly['input_sha256']
    assert 'reference_range_frames'not in p and p['release_ready']is False
    raw=resolve(project_path.parent,p['media']['raw']['path']);assert digest(raw)==assembly['input_sha256']['raw']
    compiled_visuals={e['id']:e for e in p['layers']if e.get('source_visual_binding')};assert set(compiled_visuals)=={v['id']for v in visuals}
    sample={0,count-1};bounds=[]
    comp=Composer(p,project_path.parent)
    for v in visuals:
        a,b=v['output_frames'];e=compiled_visuals[v['id']];assert round(e['in']*float(fps))==a and round(e['out']*float(fps))==b and e['text']==v['text']
        sample.update(f for f in (a-1,a,(a+b)//2,b-1,b)if 0<=f<count)
        im,(x,y)=comp.image(e,e['in']);assert 0<=x and 0<=y and x+im.width<=1920 and y+im.height<=1080
        bounds.append({'id':e['id'],'xy':[x,y],'size':list(im.size),'fully_inside_canvas':True})
    for m in assembly['take_mapping']:sample.update([m['output_frames'][0],m['output_frames'][1]-1])
    dest.mkdir(parents=True);reader=SourceReader(raw);rows=[];images={};visual_checks=0
    try:
        with av.open(str(video_path))as container:
            stream=container.streams.video[0];stream.codec_context.thread_count=2;n=0
            for n,f in enumerate(container.decode(stream),1):
                i=n-1;t=float(Fraction(i,1)/fps);assert f.width==1920 and f.height==1080 and abs(float(f.pts*f.time_base)-t)<1e-6
                m=next(m for m in assembly['take_mapping']if m['output_frames'][0]<=i<m['output_frames'][1]);clip=next(c for c in p['clips']if c['id']==m['take_id']);audio=next(a for a in p['audio']if a['id']==m['take_id']+'-voice')
                assert abs(audio['source_in']-m['audio_source_range_requested'][0])<1e-8
                expected_visual={v['id']for v in visuals if v['output_frames'][0]<=i<v['output_frames'][1]}
                expected_captions={c['id']for c in assembly['captions']if c['output_frames'][0]<=i<c['output_frames'][1]}
                if any(v['template']=='large_emphasis'and v['id']in expected_visual for v in visuals):expected_captions=set()
                active=[e for e in p['layers']if e['in']<=t+1e-9<e['out']]
                assert {e['id']for e in active if e.get('source_visual_binding')}==expected_visual
                actual_caps={e['source_dialogue_binding']['take_id'] for e in active if e['kind']=='caption'}
                assert actual_caps=={c['take_id']for c in assembly['captions']if c['id']in expected_captions}
                for v in visuals:
                    if v['id']not in expected_visual:continue
                    assert v['take_id']==m['take_id'];s=audio['source_in']+(t-audio['in'])*audio.get('rate',1);assert v['source_range'][0]-1e-8<=s<v['source_range'][1]+1e-8;visual_checks+=1
                requested=clip['source_in']+(t-clip['in'])*clip.get('rate',1);source,pts=reader.get(requested);g=clip['geometry'];transform=np.array([[g['scale'],0,g.get('tx',0)],[0,g['scale'],g.get('ty',0)]],np.float32);base=cv2.warpAffine(source,transform,(1920,1080),flags=cv2.INTER_AREA,borderMode=cv2.BORDER_CONSTANT)
                recipe=comp.compose(base,active,t);arr=f.to_ndarray(format='rgb24');mae=float(np.mean(np.abs(arr.astype(np.float32)-recipe.astype(np.float32))));assert mae<4,(i,mae)
                rows.append({'frame':i,'take_id':m['take_id'],'decoded_source_pts_seconds':pts,'visual_ids':sorted(expected_visual),'caption_ids':sorted(expected_captions),'encoded_vs_own_recipe_mae':mae})
                if i in sample:
                    path=dest/f'output-f{i:06d}.png';Image.fromarray(arr).save(path);images[i]=path
            assert n==count
    finally:reader.close()
    def pcm(path,start=None,duration=None):
        cmd=[ffmpeg,'-v','error','-threads','2']
        if start is not None:cmd+=['-ss',f'{start:.9f}','-t',f'{duration:.9f}']
        cmd+=['-i',str(path),'-vn','-ac','1','-ar','48000','-f','f32le','pipe:1'];return np.frombuffer(subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout,dtype=np.float32)
    decoded=pcm(video_path);waves=[]
    for a in p['audio']:
        assert a.get('rate',1)==1,'Demo waveform validator is rate=1 only; regressions test speed mapping'
        x=pcm(raw,a['source_in'],a['out']-a['in']);offset=round(a['in']*48000);length=min(len(x),len(decoded)-offset);margin=2400;assert length>2*margin
        corr=float(np.corrcoef(x[margin:length-margin:4].astype(float),decoded[offset+margin:offset+length-margin:4].astype(float))[0,1]);assert corr>.97
        waves.append({'id':a['id'],'source_in':a['source_in'],'output_span':[a['in'],a['out']],'correlation':corr,'sample_stride':4,'edge_samples_excluded':margin,'listened':False})
    font=ImageFont.truetype(str(resolve(project_path.parent,p['fonts']['body']['path'])),20)
    sheets=[]
    for start in range(0,len(images),6):
        selected=list(sorted(images.items()))[start:start+6];sheet=Image.new('RGB',(1920,math.ceil(len(selected)/3)*390),'#222222');draw=ImageDraw.Draw(sheet)
        for k,(f,path)in enumerate(selected):
            x=k%3*640;y=k//3*390;sheet.paste(Image.open(path).resize((640,360)),(x,y+30));draw.text((x+8,y+5),'output frame '+str(f),font=font,fill='white')
        path=dest/f'comparison-{start//6+1:02d}.jpg';sheet.save(path,quality=94);sheets.append({'path':path.name,'sha256':digest(path),'frames':[f for f,_ in selected]})
    write(dest/'all-frame-rows.json',rows)
    result={'status':'machine_checked_visual_review_pending','decoded_frames':count,'all_frame_recipe_checks':count,'source_visual_checks':visual_checks,'native_reference_reconstruction_frames':0,'visual_bounds':bounds,'selected_frames_saved':sorted(images),'selected_frames_viewed':[],'sheets':sheets,'recipe_mae_mean':sum(r['encoded_vs_own_recipe_mae']for r in rows)/count,'recipe_mae_max':max(r['encoded_vs_own_recipe_mae']for r in rows),'waveform_checks':waves,'direct_listening_seconds':0,'sha256':{'plan':digest(plan_path),'project':digest(project_path),'video':digest(video_path),'render_report':digest(video_path.with_suffix('.render.json')),'assembler_at_build':digest(assembler_at_build),'assembler_current':digest(Path(__file__).with_name('assemble_edit_plan.py')),'compiler':digest(Path(__file__).with_name('compile_plan.py')),'renderer':digest(Path(__file__).with_name('render.py')),'validator':digest(Path(__file__))},'assembler_verification_basis':{'path':str(assembler_at_build),'explicit_historical_snapshot':assembler_snapshot is not None},'limitations':['Same existing A raw source; functional timing demo, not a complete unseen-source semantic edit.','Own recipe/pixel comparison is not comparison with the reference finished video.','ASR wording, meanings, fonts, style and audio cuts are not certified.'],'release_ready':False}
    write(dest/'machine-validation.json',result);return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path);ap.add_argument('--validate-example',nargs=3);ap.add_argument('--evidence-dir',type=Path);ap.add_argument('--ffmpeg',default='ffmpeg');ap.add_argument('--assembler-snapshot',type=Path,help='Explicit historical build script; must exactly match plan input SHA. No execution.');a=ap.parse_args()
    if a.validate_example:
        if not a.evidence_dir:ap.error('Need --evidence-dir')
        result=validate_example(*a.validate_example,a.evidence_dir,a.ffmpeg,a.assembler_snapshot)
    else:
        if a.output and a.output.exists():ap.error('Choose a new result path')
        checks=tests();result={'status':'passed','checks':checks,'check_count':len(checks),'assembler_sha256':digest(Path(__file__).with_name('assemble_edit_plan.py')),'selftest_sha256':digest(Path(__file__)),'scope':'Synthetic source/occurrence/frame/compiler guards only; no human visual or audio approval'}
        if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);write(a.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
