#!/usr/bin/env python3
"""Apply explicit, occurrence-bound measured quiet decisions without changing words.

Rate-1 aligned audio only. Cuts snap inward to each original take's CFR grid.
Any caption/visual lifetime or protected semantic range conflict is skipped.
This is a draft timing transformation, never automatic silence/meaning approval.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, hashlib, json, math, sys
from assemble_edit_plan import read_bound, digest, require, keys, selected_text, number, sha_equal


def overlap(a,b,c,d): return a<d-1e-9 and c<b-1e-9

def protected_speech(asr,duration,guard):
    spans=[]
    for i,s in enumerate(asr['segments']):
        words=s.get('words') or []
        valid=[w for w in words if number(w.get('start')) and number(w.get('end')) and 0<=w['start']<w['end']<=duration+1e-8]
        if len(valid)!=len(words) or not words:
            require(number(s.get('start')) and number(s.get('end')) and 0<=s['start']<s['end']<=duration+1e-8,'Invalid ASR fallback cue')
            spans.append((max(0,s['start']-guard),min(duration,s['end']+guard)))
        spans.extend((max(0,w['start']-guard),min(duration,w['end']+guard)) for w in valid)
    return spans


def transform(d,asr,q,selection,duration):
    require(d.get('schema')=='atrass-source-dialogue-v1','Expected source-relative decisions')
    require(q.get('schema')=='atrass-quiet-cut-candidates-v1','Expected measured quiet report')
    require(selection.get('schema')=='atrass-quiet-application-selection-v1','Expected explicit quiet selection')
    keys(selection,['schema','decisions_sha256','quiet_report_sha256','cuts','protected_ranges','policy'],'selection')
    require(isinstance(selection.get('policy'),str) and selection['policy'].strip(),'Explicit policy required')
    fps=Fraction(d.get('fps','60000/1001'));require(fps>0,'Positive output FPS required');r=float(fps)
    native=Fraction(q['native_video_fps']);require(native>0,'Positive measured native FPS required')
    guard=q.get('speech_guard_seconds');require(number(guard) and guard>=0,'Invalid speech guard')
    speech=protected_speech(asr,duration,guard)
    ids=[t['id'] for t in d['takes']];require(len(ids)==len(set(ids)),'Duplicate take occurrence IDs')
    keeps={t['id']:t for t in d['takes'] if t['action']=='keep'}
    require(keeps,'No kept takes')
    for t in keeps.values():
        require(t.get('rate',1)==1,'Quiet application supports rate 1 only')
        require(abs(t.get('audio_source_in',t['source_in'])-t['source_in'])<1e-9,'Quiet application requires aligned video/audio source clocks')
        require(0<=t['source_in']<t['source_out']<=duration+1e-8,'Invalid kept source range')
        count=round((t['source_out']-t['source_in'])*r)
        require(count>=1 and t['source_in']+count/r<=duration+1e-8,'Original quantized take exceeds source duration or collapses')
    bindings=[]
    for kind in ('captions','visual_events'):
        for c in d.get(kind,[]):
            require(c['take_id'] in keeps,'Selection names a missing kept occurrence')
            a,b,text,basis,wordkeys,index,s=selected_text(c,asr['segments'],duration,d['transcript_sha256'])
            t=keeps[c['take_id']];end=t['source_in']+round((t['source_out']-t['source_in'])*r)/r
            require(t['source_in']-1e-8<=a<b<=min(end,t['source_out'])+1e-8,'Original selected speech already crosses take/rendered boundary')
            bindings.append({'kind':kind,'id':c['id'],'take_id':c['take_id'],'a':a,'b':b})
    protected=selection.get('protected_ranges',[])
    require(isinstance(protected,list),'protected_ranges must be an array')
    for p in protected:
        keys(p,['take_id','source_range','reason'],'semantic protection')
        require(p.get('take_id') in keeps and isinstance(p.get('reason'),str) and p['reason'].strip(),'Semantic protection needs occurrence and reason')
        a,b=p['source_range'];require(number(a) and number(b) and 0<=a<b<=duration+1e-8,'Invalid semantic protection interval')
    rows=[];accepted={tid:[] for tid in keeps};seen=set()
    require(isinstance(selection.get('cuts'),list),'Explicit cuts list required')
    for choice in selection['cuts']:
        keys(choice,['candidate_index','take_id','action','reason'],'quiet choice')
        i=choice.get('candidate_index');tid=choice.get('take_id');action=choice.get('action')
        require(type(i) is int and 0<=i<len(q['candidates']),'Candidate array index out of range')
        require((i,tid) not in seen,'Duplicate candidate occurrence selection');seen.add((i,tid))
        require(action in ('apply','skip') and isinstance(choice.get('reason'),str) and choice['reason'].strip(),'Every quiet choice needs explicit action/reason')
        row=copy.deepcopy(choice);row['status']='skipped';rows.append(row)
        if action=='skip':row['skip_reason']='agent_explicit_skip';continue
        require(tid in keeps,'Quiet choice needs a kept occurrence ID')
        c=q['candidates'][i];a,b=c['remove_source_range'];qa,qb=c['quiet_range'];na,nb=c['remove_source_frames']
        require(all(number(x) for x in (a,b,qa,qb)) and 0<=qa<=a<b<=qb<=duration+1e-8,'Invalid measured candidate interval')
        require(type(na) is int and type(nb) is int and na<nb and abs(na/float(native)-a)<1e-8 and abs(nb/float(native)-b)<1e-8,'Measured native-frame/range mismatch')
        edge=q['retained_quiet_per_edge_seconds'];require(number(edge) and edge>=0 and a-qa>=edge-1e-8 and qb-b>=edge-1e-8,'Candidate removed retained edge guards')
        require(not any(overlap(a,b,x,y) for x,y in speech),'Measured candidate overlaps protected ASR speech')
        t=keeps[tid];origin=t['source_in'];count=round((t['source_out']-origin)*r)
        if not origin<a<b<min(t['source_out'],origin+count/r)-1e-9:row['skip_reason']='not_wholly_inside_kept_occurrence';continue
        # Inward snapping never removes outside the measured interval. It also
        # keeps every child on the original take's sampling phase, including tail.
        f0=math.ceil((a-origin)*r-1e-8);f1=math.floor((b-origin)*r+1e-8)
        if f1<=f0:row['skip_reason']='collapsed_after_inward_CFR_snap';continue
        aa=origin+f0/r;bb=origin+f1/r
        require(aa>=a-1e-8 and bb<=b+1e-8,'Inward snap escaped measured range')
        conflicts=[x['kind']+':'+x['id'] for x in bindings if x['take_id']==tid and overlap(aa,bb,x['a'],x['b'])]
        conflicts += ['semantic:'+p['reason'] for p in protected if p['take_id']==tid and overlap(aa,bb,*p['source_range'])]
        if conflicts:row['skip_reason']='protected_lifetime_or_semantic_boundary';row['conflicts']=conflicts;continue
        require(not any(overlap(f0,f1,x['frames'][0],x['frames'][1]) for x in accepted[tid]),'Accepted quiet cuts overlap')
        row.update(status='accepted_draft',measured_source_range=[a,b],applied_source_range=[aa,bb],parent_frame_range=[f0,f1],removed_output_frames=f1-f0,inward_seconds=[aa-a,b-bb])
        accepted[tid].append({'frames':[f0,f1],'row':row})
    result=copy.deepcopy(d);result['title']=d.get('title','Source draft')+' · explicit measured-quiet draft';newtakes=[];children={};childrows=[]
    for t in d['takes']:
        tid=t['id']
        if t['action']!='keep' or not accepted[tid]:newtakes.append(copy.deepcopy(t));children[tid]=[(tid,t.get('source_in'),t.get('source_out'))];continue
        origin=t['source_in'];count=round((t['source_out']-origin)*r);cursor=0;parts=[]
        for cut in sorted(accepted[tid],key=lambda c:c['frames']):
            lo,hi=cut['frames'];parts.append((cursor,lo));cursor=hi
        parts.append((cursor,count));require(all(lo<hi for lo,hi in parts),'Quiet application created empty kept piece')
        children[tid]=[]
        for j,(lo,hi) in enumerate(parts):
            nt=copy.deepcopy(t);cid=f'{tid}__quiet_{j+1:03d}';require(cid not in ids,'Generated take ID collision');ids.append(cid)
            aa=origin+lo/r;bb=origin+hi/r;nt.update(id=cid,source_in=aa,source_out=bb)
            if 'audio_source_in' in nt:nt['audio_source_in']=aa
            # Old reason/evidence are preserved verbatim; the separate report
            # binds their original parent and new quiet transformation.
            if not any(x['kind']=='captions' and x['take_id']==tid and aa-1e-8<=x['a']<x['b']<=bb+1e-8 for x in bindings):
                nt['caption_omission_reason']='This child has no caption selection; no text was inferred. Original omission reason: '+str(t.get('caption_omission_reason','none supplied; all original caption selections remain on their assigned children'))
            newtakes.append(nt);children[tid].append((cid,aa,bb));childrows.append({'parent_take_id':tid,'take_id':cid,'parent_frames':[lo,hi],'source_range':[aa,bb]})
    result['takes']=newtakes
    for kind in ('captions','visual_events'):
        for c in result.get(kind,[]):
            oldtid=c['take_id'];x=next(x for x in bindings if x['kind']==kind and x['id']==c['id'])
            hits=[cid for cid,aa,bb in children[oldtid] if aa-1e-8<=x['a']<x['b']<=bb+1e-8]
            require(len(hits)==1,'Selected caption/visual would split or disappear');c['take_id']=hits[0]
    # Only take_id may change in any selection. This preserves ASR word indices,
    # explicit overlap repairs, strings and visual policy without token repeats.
    for kind in ('captions','visual_events'):
        require([{k:v for k,v in c.items() if k!='take_id'} for c in result.get(kind,[])]==[{k:v for k,v in c.items() if k!='take_id'} for c in d.get(kind,[])],'Selection content changed')
    original_frames=sum(round((t['source_out']-t['source_in'])*r) for t in keeps.values())
    new_frames=sum(round((t['source_out']-t['source_in'])*r) for t in newtakes if t['action']=='keep')
    removed=sum(x['removed_output_frames'] for x in rows if x['status']=='accepted_draft')
    require(original_frames-new_frames==removed,'Frame conservation failed')
    report={'schema':'atrass-applied-quiet-draft-v1','release_ready':False,'direct_listening_seconds':0,'policy':selection['policy'],'fps':str(fps),'original_frames':original_frames,'output_frames':new_frames,'removed_frames':removed,'removed_seconds':removed/r,'output_seconds':new_frames/r,'accepted_count':sum(x['status']=='accepted_draft' for x in rows),'skipped_count':sum(x['status']=='skipped' for x in rows),'caption_selections_preserved':len(result.get('captions',[])),'visual_selections_preserved':len(result.get('visual_events',[])),'kept_occurrences':sum(t['action']=='keep' for t in newtakes),'cuts':rows,'children':childrows,'limits':['Transcript-guided draft; measured quiet is not proof of inaudibility or acting quality.','Rate 1 and aligned source audio only; semantic protections are explicit agent decisions.','Caption and visual lifetime crossings are skipped, not split or repeated.','Original rendered CFR source phase and tail are preserved; measured cuts shrink inward by less than one output frame at each edge.','No finished-reference edit map used; original candidate decision=null records remain unchanged.']}
    return result,report


def apply(job_path,decisions_path,quiet_path,measured_asr_path,selection_path,output_dir):
    paths=[Path(x).resolve() for x in (job_path,decisions_path,quiet_path,measured_asr_path,selection_path)]
    out=Path(output_dir).resolve();require(not out.exists(),'Output directory must be new')
    reads=[read_bound(p) for p in paths];(job,js),(d,ds),(q,qs),(measured,ms),(selection,ss)=reads
    require(job.get('schema')=='atrass-job-v1','Expected prepared job schema')
    require(Fraction(q['native_video_fps'])==Fraction(job['raw']['fps']),'Quiet native FPS differs from prepared source')
    transcript_path=(paths[0].parent/job['files']['transcript']).resolve();asr,ts=read_bound(transcript_path)
    sha_equal(d['job_sha256'],js,'Prepared job');sha_equal(d['transcript_sha256'],ts,'Prepared transcript')
    sha_equal(selection.get('decisions_sha256'),ds,'Selection decisions');sha_equal(selection.get('quiet_report_sha256'),qs,'Selection quiet report')
    sha_equal(q.get('asr_sha256'),ms,'Measured ASR')
    raw=Path(job['raw']['path']);raw=raw if raw.is_absolute() else paths[0].parent/raw
    stat=raw.stat();raw_sha=digest(raw)
    for label,sha in [('job',job['raw']['sha256']),('transcript',asr.get('source_sha256')),('quiet report',q.get('raw_sha256'))]:sha_equal(sha,raw_sha,label+' raw')
    # Prepared ASR may expand cue envelopes around existing word times. It may
    # not alter any text/word record under this bridge.
    require([(s['text'],s.get('words')) for s in asr['segments']]==[(s['text'],s.get('words')) for s in measured['segments']],'Prepared/measured ASR text or word timestamps differ')
    result,report=transform(d,asr,q,selection,job['raw']['duration_seconds'])
    report['input_bindings']={label:{'path':str(p),'sha256':sha} for label,p,(_,sha) in zip(('job','decisions','quiet_report','measured_asr','selection'),paths,reads)}
    report['input_bindings'].update(prepared_asr={'path':str(transcript_path),'sha256':ts},raw={'path':str(raw),'sha256':raw_sha},tool={'path':str(Path(__file__).resolve()),'sha256':digest(__file__)})
    report['asr_bridge']='All segment text and complete word records identical by array index; cue-envelope metadata may differ.'
    for p,(_,sha) in zip(paths,reads):sha_equal(sha,digest(p),'Input before publication')
    sha_equal(ts,digest(transcript_path),'Prepared ASR before publication')
    now=raw.stat();require((stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)==(now.st_dev,now.st_ino,now.st_size,now.st_mtime_ns),'Raw changed during application')
    out.mkdir(parents=True,exist_ok=False)
    for name,value in [('decisions.json',result),('application-report.json',report)]:
        with (out/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('job','decisions','quiet-report','measured-asr','selection','output-dir'):ap.add_argument('--'+name,required=True)
    a=ap.parse_args()
    try:
        r=apply(a.job,a.decisions,a.quiet_report,a.measured_asr,a.selection,a.output_dir)
        print(json.dumps({k:r[k] for k in ('accepted_count','skipped_count','output_frames','output_seconds','removed_seconds','kept_occurrences')}));return 0
    except (ValueError,KeyError,TypeError,OSError) as e:print(str(e),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
