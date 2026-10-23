#!/usr/bin/env python3
"""Assemble source-relative dialogue selections into an existing edit-plan draft.

This is a deterministic timing adapter, not an editorial/ASR approval system.
Keeps/drops and caption groups are explicit agent decisions. Source reuse requires
an occurrence-specific take ID. One prepared source, basic dialogue, and occurrence-bound large_emphasis /
situation_label decisions are supported. No automatic deletion, inferred partial
cue text, asset selection or semantic/style approval is performed.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, hashlib, json, math, re, sys


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def read_bound(path):
    data=Path(path).read_bytes()
    return json.loads(data.decode('utf-8')),hashlib.sha256(data).hexdigest()
def require(test,message):
    if not test:raise ValueError(message)
def number(v):return type(v) in (int,float) and math.isfinite(v)
def keys(obj,allowed,where):
    require(isinstance(obj,dict),where+' must be an object')
    require(not set(obj)-set(allowed),where+' unsupported keys: '+str(sorted(set(obj)-set(allowed))))
def nonempty(v):return isinstance(v,str) and bool(v.strip())
def sha_equal(expected,actual,label):
    require(isinstance(expected,str) and re.fullmatch(r'[0-9a-fA-F]{64}',expected) is not None,label+' needs SHA-256')
    require(expected.lower()==actual,label+' SHA-256 mismatch')
def interval(a,b,limit,label):
    require(number(a) and number(b) and 0<=a<b<=limit+1e-8,label+' needs finite ordered source times within duration')
def frame_ceil(seconds,fps):return math.ceil(seconds*float(fps)-1e-8)


def selected_text(c,segments,duration,transcript_sha):
    """Shared explicit ASR selection; never infer a partial cue's words."""
    index=c.get('segment_index');require(type(index) is int and 0<=index<len(segments),'Use transcript array segment_index, not repeated ASR id')
    s=segments[index];interval(s.get('start'),s.get('end'),duration,'Selected ASR cue')
    word_keys=None;explicit='source_range' in c
    if explicit:
        require('word_range' not in c,'source_range and word_range conflict')
        span=c['source_range'];require(isinstance(span,list) and len(span)==2,'Explicit source_range needs two values')
        a,b=span;interval(a,b,duration,'Explicit caption source range')
        require(s['start']-1e-8<=a<b<=s['end']+1e-8,'Explicit range outside ASR cue')
        require(nonempty(c.get('text')) and nonempty(c.get('evidence')),'Partial/explicit cue needs text and evidence')
        text=c['text'];basis='explicit_text_and_range_not_inferred_asr_substring'
    else:
        words=s.get('words')
        if words:
            require(isinstance(words,list),'ASR words must be an array')
            span=c.get('word_range',[0,len(words)])
            require(isinstance(span,list) and len(span)==2 and all(type(v) is int for v in span) and 0<=span[0]<span[1]<=len(words),'word_range is a half-open array index range')
            chosen=words[span[0]:span[1]];prev=None
            for w in chosen:
                interval(w.get('start'),w.get('end'),duration,'Selected ASR word')
                require(nonempty(w.get('word')),'Selected ASR word text missing')
                require(s['start']-1e-8<=w['start']<w['end']<=s['end']+1e-8,'Word outside its ASR cue')
                require(prev is None or w['start']>=prev-1e-8,'Selected ASR words overlap or reverse')
                prev=w['end']
            a=chosen[0]['start'];b=chosen[-1]['end'];text=''.join(w['word'] for w in chosen).strip()
            word_keys=[f'{transcript_sha}:{index}:{i}' for i in range(*span)];basis='selected_asr_words_unverified'
        else:
            require('word_range' not in c,'Word selection needs word timestamps')
            a=s['start'];b=s['end'];text=s.get('text');basis='whole_asr_cue_unverified'
        if 'text' in c:
            require(nonempty(c['text']) and nonempty(c.get('evidence')),'Text override needs explicit text and evidence')
            text=c['text'];basis='explicit_text_override_with_source_selection'
    require(nonempty(text) and not any(ord(ch)<32 for ch in text),'Dialogue needs nonempty single-line text without controls')
    return a,b,text,basis,word_keys,index,s


def mapped_selection(a,b,m,fps):
    """Map source audio to an occurrence's half-open output frames."""
    audio_a,audio_b=m['audio_source_range_requested']
    require(a>=audio_a-1e-8 and b<=audio_b+1e-8,'Caption crosses the selected audio cut; select complete words, or supply explicit text/range/evidence')
    require(b<=m['audio_source_range_rendered'][1]+1e-8,'Output frame rounding would truncate the selected source speech; explicitly revise the take tail with source evidence')
    lo,hi=m['output_frames'];mapped_a=lo/float(fps)+(a-audio_a)/m['rate'];mapped_b=lo/float(fps)+(b-audio_a)/m['rate']
    start=max(lo,frame_ceil(mapped_a,fps));end=min(hi,frame_ceil(mapped_b,fps))
    require(start<end,'Caption collapsed at output frame quantization; revise selection')
    return start,end,mapped_a,mapped_b


def assemble(job_path,decisions_path,output):
    job_path=Path(job_path).resolve();decisions_path=Path(decisions_path).resolve();output=Path(output).resolve()
    require(not output.exists(),'Choose a new output plan path')
    job,job_sha=read_bound(job_path);d,decisions_sha=read_bound(decisions_path)
    keys(d,['schema','title','job_sha256','transcript_sha256','fps','takes','captions','visual_events','logo_decision'],'decisions')
    require(d.get('schema')=='atrass-source-dialogue-v1','Expected atrass-source-dialogue-v1')
    require(job.get('schema')=='atrass-job-v1','Expected prepared atrass-job-v1')
    sha_equal(d.get('job_sha256'),job_sha,'Prepared job')
    transcript_path=(job_path.parent/job['files']['transcript']).resolve();transcript,transcript_sha=read_bound(transcript_path)
    sha_equal(d.get('transcript_sha256'),transcript_sha,'Transcript')
    raw=Path(job['raw']['path']);raw=raw if raw.is_absolute() else (job_path.parent/raw).resolve()
    require(raw.is_file(),'Prepared raw source is missing')
    raw_stat=raw.stat();raw_sha=digest(raw);sha_equal(job['raw'].get('sha256'),raw_sha,'Raw source')
    sha_equal(transcript.get('source_sha256'),raw_sha,'Transcript source')
    duration=job['raw']['duration_seconds'];require(number(duration) and duration>0,'Invalid prepared raw duration')
    variant=job.get('variant');require(variant in ('A','B'),'Invalid prepared variant')
    try:fps=Fraction(d.get('fps','60000/1001' if variant=='A' else '60'))
    except (ValueError,TypeError,ZeroDivisionError):raise ValueError('Invalid output FPS')
    require(fps>0,'Output FPS must be positive')
    if variant=='B':require(nonempty(d.get('logo_decision')),'B requires explicit logo_decision')
    require(isinstance(d.get('takes'),list) and d['takes'],'Explicit take decisions required')
    require(isinstance(d.get('captions'),list),'Explicit caption selections required')
    segments=transcript.get('segments');require(isinstance(segments,list),'Transcript segments required')
    takes=[];maps={};drops=[];ids=set();cursor=0
    for t in d['takes']:
        keys(t,['id','action','source_in','source_out','audio_source_in','rate','geometry','voice_gain_db','reason','evidence','caption_omission_reason'],'take')
        tid=t.get('id');require(nonempty(tid) and tid not in ids,'Take IDs must be unique occurrence IDs');ids.add(tid)
        require(t.get('action') in ('keep','drop'),'Take action must explicitly be keep/drop')
        require(nonempty(t.get('reason')) and nonempty(t.get('evidence')),'Every keep/drop needs reason and evidence')
        a=t.get('source_in');b=t.get('source_out');interval(a,b,duration,'Take '+tid)
        if t['action']=='drop':
            require(not set(t)&{'audio_source_in','rate','geometry','voice_gain_db','caption_omission_reason'},'Drop decision cannot contain ignored render options')
            drops.append(copy.deepcopy(t));continue
        rate=t.get('rate',1);require(number(rate) and .5<=rate<=2,'Take rate must be in renderer audio range .5..2')
        audio_in=t.get('audio_source_in',a);interval(audio_in,audio_in+b-a,duration,'Take audio '+tid)
        # Deliberately the existing compile_plan contract, including round-to-even.
        count=round((b-a)/rate*float(fps));require(count>=1,'Take shorter than one output frame')
        lo=cursor;cursor+=count;hi=cursor;seconds=count/float(fps)
        require(a+seconds*rate<=duration+1e-8 and audio_in+seconds*rate<=duration+1e-8,'Quantized take exceeds source duration; choose an earlier endpoint')
        item={'id':tid,'source':'raw','source_in':a,'source_out':b,'audio_source_in':audio_in,'rate':rate,'status':'explicit_agent_decision_draft_not_semantically_verified'}
        for k in ('geometry','voice_gain_db'):
            if k in t:item[k]=copy.deepcopy(t[k])
        takes.append(item)
        maps[tid]={'take_id':tid,'output_frames':[lo,hi],'video_source_range_requested':[a,b],'audio_source_range_requested':[audio_in,audio_in+b-a],'audio_source_range_rendered':[audio_in,audio_in+seconds*rate],'rate':rate,'output_endpoint_rounding_seconds':seconds-(b-a)/rate,'reason':t['reason'],'evidence':t['evidence'],'caption_omission_reason':t.get('caption_omission_reason'),'caption_count':0}
    require(takes,'At least one kept take required')
    events=[];caption_ledger=[];event_ids=set()
    for c in d['captions']:
        keys(c,['id','take_id','segment_index','word_range','source_range','text','evidence'],'caption')
        cid=c.get('id');require(nonempty(cid) and cid not in event_ids,'Caption IDs must be unique');event_ids.add(cid)
        tid=c.get('take_id');require(tid in maps,'Caption must name a kept take occurrence')
        a,b,text,basis,word_keys,index,s=selected_text(c,segments,duration,transcript_sha)
        m=maps[tid];start,end,mapped_a,mapped_b=mapped_selection(a,b,m,fps)
        m['caption_count']+=1
        events.append({'id':cid,'template':'dialogue','in':float(Fraction(start,1)/fps),'out':float(Fraction(end,1)/fps),'text':text,'source_dialogue_binding':{'take_id':tid,'transcript_sha256':transcript_sha,'segment_index':index,'source_range':[a,b]}})
        caption_ledger.append({'id':cid,'take_id':tid,'segment_index':index,'asr_id_not_used_as_identity':s.get('id'),'word_keys':word_keys,'text_basis':basis,'evidence':c.get('evidence'),'source_range':[a,b],'output_frames':[start,end],'unquantized_output_seconds':[mapped_a,mapped_b],'start_quantization_seconds':start/float(fps)-mapped_a,'end_quantization_seconds':end/float(fps)-mapped_b,'text':text})
    by_time=sorted(caption_ledger,key=lambda x:x['output_frames'])
    for first,second in zip(by_time,by_time[1:]):
        require(first['output_frames'][1]<=second['output_frames'][0],'Dialogue captions overlap after frame quantization: '+first['id']+' / '+second['id'])
    for m in maps.values():require(m['caption_count']>0 or nonempty(m['caption_omission_reason']),'Kept take needs caption selections or caption_omission_reason: '+m['take_id'])
    result={'schema':'atrass-edit-plan-v1','title':d.get('title','Source-relative dialogue draft'),'variant':variant,'fps':str(fps),'release_ready':False,'media':{'raw':{'path':str(raw),'role':'source_footage','sha256':raw_sha}},'takes':takes,'audio_follow_takes':True,'events':events,'source_dialogue_assembly':{'schema':'atrass-source-dialogue-assembly-v1','status':'draft_timing_transformation_not_semantic_approval','input_sha256':{'job':job_sha,'transcript':transcript_sha,'decisions':decisions_sha,'raw':raw_sha,'assembler':digest(Path(__file__))},'input_paths':{'job':str(job_path),'transcript':str(transcript_path),'decisions':str(decisions_path)},'output_frame_count':cursor,'take_mapping':list(maps.values()),'captions':caption_ledger,'drop_decisions':drops,'unselected_source_policy':'Not selected for this draft excerpt; absence is not an approved deletion.','editorial_candidates_policy':'Original job candidate file is read-only and unresolved decisions remain unresolved.','frame_policy':'Take lengths use existing compiler round(duration/rate*fps); caption boundaries use ceil to output frames, half-open, clamped to their take. Rounding residuals are reported, not certified phoneme boundaries.','limitations':['One hash-verified prepared raw source; basic dialogue only.','Agent supplies take choices, caption groups and any wording corrections; no silence/repetition decisions are inferred.','ASR text/timing and source edits remain unverified until visual/audio editorial review.','No reference finished video/audio, original reference coverage or complete new-footage semantic-edit claim.']}}
    if 'visual_events' in d:
        require(isinstance(d['visual_events'],list),'visual_events must be an array')
        visual_ledger=[]
        for v in d['visual_events']:
            keys(v,['id','take_id','segment_index','word_range','source_range','text','evidence','reason','template','position','anchor'],'visual event')
            vid=v.get('id');require(nonempty(vid) and vid not in event_ids,'Visual/caption IDs must be globally unique');event_ids.add(vid)
            tid=v.get('take_id');require(tid in maps,'Visual event must name a kept take occurrence')
            template=v.get('template');require(template in ('large_emphasis','situation_label'),'Unsupported source visual template; use an explicit separate plan for other effects')
            require(nonempty(v.get('reason')) and nonempty(v.get('evidence')),'Visual decision requires reason and evidence; it is not automatic semantic approval')
            placement={}
            if template=='situation_label':
                pos=v.get('position');require(isinstance(pos,list) and len(pos)==2 and all(number(x) for x in pos) and 0<=pos[0]<1920 and 0<=pos[1]<1080,'Situation label requires an explicit finite position in the 1920x1080 template canvas')
                require(v.get('anchor') in ('top_left','top_center','center'),'Situation label requires an explicit supported anchor')
                placement={'position':copy.deepcopy(pos),'anchor':v['anchor']}
            else:require(not {'position','anchor'}&set(v),'Large emphasis placement is owned by the existing compiler auto-fit policy')
            a,b,text,basis,word_keys,index,s=selected_text(v,segments,duration,transcript_sha)
            m=maps[tid];start,end,mapped_a,mapped_b=mapped_selection(a,b,m,fps)
            binding={'take_id':tid,'transcript_sha256':transcript_sha,'segment_index':index,'source_range':[a,b]}
            events.append({'id':vid,'template':template,'in':float(Fraction(start,1)/fps),'out':float(Fraction(end,1)/fps),'text':text,'source_visual_binding':binding,**placement})
            visual_ledger.append({'id':vid,'template':template,'take_id':tid,'segment_index':index,'asr_id_not_used_as_identity':s.get('id'),'word_keys':word_keys,'text_basis':basis,'reason':v['reason'],'evidence':v['evidence'],'source_range':[a,b],'output_frames':[start,end],'unquantized_output_seconds':[mapped_a,mapped_b],'start_quantization_seconds':start/float(fps)-mapped_a,'end_quantization_seconds':end/float(fps)-mapped_b,'text':text,'placement':placement})
        # Two different kinds may intentionally coexist; equal kinds must not
        # silently stack. Adjacency remains half-open and requires no gap.
        for template in ('large_emphasis','situation_label'):
            same=sorted((v for v in visual_ledger if v['template']==template),key=lambda v:v['output_frames'])
            for first,second in zip(same,same[1:]):
                require(first['output_frames'][1]<=second['output_frames'][0],'Source visual events of the same template overlap after frame quantization: '+first['id']+' / '+second['id'])
        result['source_dialogue_assembly']['limitations'][0]='One hash-verified prepared raw source; dialogue timing here, optional source_visual_assembly separately.'
        result['source_visual_assembly']={'schema':'atrass-source-visual-assembly-v1','status':'draft_timing_transformation_not_semantic_or_style_approval','input_sha256':copy.deepcopy(result['source_dialogue_assembly']['input_sha256']),'events':visual_ledger,'time_basis':'Selected original audio, mapped through one kept take occurrence; never video source_in when audio differs.','frame_policy':result['source_dialogue_assembly']['frame_policy'],'limitations':['Only large_emphasis and static situation_label; no implicit asset, chapter or animation generation.','Existing compiler owns typography and dialogue suppression; original style identity is unverified.','Situation label position/anchor are explicit agent choices; glyph bounds/readability require compiled/rendered visual verification.','Semantic selection, ASR wording/timing and audio cuts remain unverified until editorial review.']}
    if variant=='B':result['logo_decision']=d['logo_decision']
    # Hashes are checked again before writing to catch concurrent metadata edits.
    sha_equal(d['job_sha256'],digest(job_path),'Prepared job after assembly');sha_equal(d['transcript_sha256'],digest(transcript_path),'Transcript after assembly')
    sha_equal(decisions_sha,digest(decisions_path),'Decisions after assembly')
    current_raw_stat=raw.stat()
    require((raw_stat.st_dev,raw_stat.st_ino,raw_stat.st_size,raw_stat.st_mtime_ns)==(current_raw_stat.st_dev,current_raw_stat.st_ino,current_raw_stat.st_size,current_raw_stat.st_mtime_ns),'Raw source changed during assembly')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--job',required=True);ap.add_argument('--decisions',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    try:
        p=assemble(a.job,a.decisions,a.output);print(json.dumps({'output':str(Path(a.output).resolve()),'frames':p['source_dialogue_assembly']['output_frame_count'],'takes':len(p['takes']),'captions':sum(e['template']=='dialogue' for e in p['events']),'visual_events':len(p.get('source_visual_assembly',{}).get('events',[])),'events':len(p['events']),'release_ready':False},ensure_ascii=False));return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:print(str(exc),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
