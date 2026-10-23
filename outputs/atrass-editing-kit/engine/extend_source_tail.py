#!/usr/bin/env python3
"""Extend the last continuous raw take to an explicitly reviewed frame endpoint.
Video and original voice stay on the same rate1 clock. No freeze, silence fill,
next utterance or inferred ending style. Every prior layer stays unchanged.
"""
from pathlib import Path
from fractions import Fraction
import argparse,copy,json,math,os
from apply_source_assets import read,sha,keys,require,relative,path_at
from render import validate
SCHEMA='atrass-source-tail-extension-v1'

def transform(p,spec,transcript,source_duration):
    keys(spec,('schema','project_sha256','transcript','take_id','expected_source_end','additional_frames','evidence'),('schema','project_sha256','transcript','take_id','expected_source_end','additional_frames','evidence'),'tail extension')
    require(spec['schema']==SCHEMA and isinstance(spec['evidence'],str)and spec['evidence'].strip(),'Tail schema/evidence')
    require('source_tail_extension'not in p,'Tail extension already applied')
    fps=Fraction(p['fps']);frames=spec['additional_frames'];require(type(frames)is int and 0<frames<=math.floor(2*fps),'Tail must add1frame–2seconds')
    clips=[c for c in p['clips']if c['id']==spec['take_id']];require(len(clips)==1,'Unique tail take');c=clips[0]
    require(c['out']==p['duration']and all(x['out']<=c['in'] for x in p['clips']if x is not c),'Only the last continuous take can extend')
    require(c.get('rate',1)==1 and not c.get('freeze_source') and 'source_frame_times'not in c and not any(k.endswith('_keys')for k in c.get('geometry',{})),'Tail requires static-geometry native rate1 source')
    media=p['media'][c['source']];require(media.get('role')=='source_footage'and media['sha256']==transcript['source_sha256'],'Raw source/transcript identity')
    audio=[a for a in p['audio']if a['in']==c['in']and a['out']==c['out']and a['source']==c['source']];require(len(audio)==1,'Unique uninterrupted tail voice');a=audio[0]
    require(a.get('rate',1)==1 and a['source_in']==c['source_in']and a.get('fade_out',0)==0,'Matching source clock and no fade')
    require(all(x is a or x['out']<=a['in']for x in p['audio']),'Overlapping ending audio')
    require(all(e['out']<=p['duration']for e in p['layers']),'Layer past old duration')
    old_source_end=c['source_in']+c['out']-c['in'];require(abs(old_source_end-spec['expected_source_end'])<1e-9,'Tail source endpoint differs')
    old_frames=round(p['duration']*float(fps));require(abs(p['duration']-float(Fraction(old_frames,1)/fps))<1e-8,'Old duration is not CFR')
    end=float(Fraction(old_frames+frames,1)/fps);new_source_end=c['source_in']+end-c['in'];require(new_source_end<=source_duration,'Tail outside raw source')
    words=[w for s in transcript['segments']for w in s.get('words',[])];require(words,'Word-level transcript required')
    require(not any(w['start']<new_source_end and w['end']>old_source_end for w in words),'Extension overlaps an ASR utterance; make explicit dialogue decisions instead')
    previous=[w for w in words if w['end']<=old_source_end];future=[w for w in words if w['start']>=old_source_end];require(previous,'No preceding spoken-word evidence')
    result=copy.deepcopy(p);result['duration']=end;next(x for x in result['clips']if x['id']==c['id'])['out']=end;next(x for x in result['audio']if x['id']==a['id'])['out']=end
    return result,{'take_id':c['id'],'voice_id':a['id'],'old_frames':old_frames,'new_frames':old_frames+frames,'added_frames':frames,'old_source_end':old_source_end,'new_source_end':new_source_end,'last_asr_word':max(previous,key=lambda w:w['end']),'next_asr_word':min(future,key=lambda w:w['start'])if future else None,'existing_layers_unchanged':True,'interpretation':'Explicit live-source tail, no frozen frame. ASR gap and visual endpoint do not approve sound quality.'}

def apply(project,decision,output):
    project,decision,output=[Path(x).absolute()for x in(project,decision,output)];require(output.suffix=='.json'and output.parent.resolve()==project.parent.resolve()and not os.path.lexists(output),'Choose a new adjacent JSON output')
    p,psha=read(project);spec,dsha=read(decision);require(spec['project_sha256']==psha,'Project SHA mismatch');dep=spec['transcript'];keys(dep,('path','sha256'),('path','sha256'),'transcript');tp=path_at(decision.parent,dep['path']);t,tsha=read(tp);require(tsha==dep['sha256'],'Transcript SHA mismatch');validate(p,project.parent,True)
    c=next((x for x in p['clips']if x['id']==spec['take_id']),None);require(c,'Unknown tail take');media=p['media'][c['source']];raw=path_at(project.parent,media['path']);require(sha(raw)==media['sha256'],'Actual raw source SHA')
    import av
    with av.open(str(raw))as con:
        stream=con.streams.video[0];require(stream.duration is not None,'Raw video duration unavailable');duration=float(stream.duration*stream.time_base)
    result,record=transform(p,spec,t,duration);result['source_tail_extension']={'schema':SCHEMA,'input_project':relative(project,output.parent),'input_sha256':psha,'decisions':relative(decision,output.parent),'decisions_sha256':dsha,'implementation_sha256':sha(Path(__file__)),'record':record,'direct_listening_seconds':0,'release_ready':False};validate(result,output.parent,True)
    require(sha(project)==psha and sha(decision)==dsha and sha(tp)==tsha,'Tail inputs changed')
    with output.open('x',encoding='utf-8')as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    return {'output':str(output),'sha256':sha(output),'added_frames':record['added_frames'],'new_source_end':record['new_source_end'],'release_ready':False}
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for k in['project','decision','output']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();print(json.dumps(apply(a.project,a.decision,a.output),ensure_ascii=False))
