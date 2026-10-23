#!/usr/bin/env python3
"""Replace explicitly identified production-heading speech with a silent card.

Only an existing source-bound chapter can authorize a gap. Caption words must
be exactly its number and title. Video, duration and unrelated dialogue remain
unchanged. The removed original caption/audio and half-open silence are recorded.
"""
from pathlib import Path
from fractions import Fraction
import argparse,copy,json,os,re,tempfile
import apply_source_assets as shared
from render import validate

SCHEMA='atrass-silent-chapter-readouts-v1'


def title_words(s):
    return ' '.join(s.strip().rstrip('.?!。？！').split())


def transform(p,decisions,source_sha256):
    shared.require(isinstance(decisions,list) and decisions,'Explicit readout decisions required')
    fps,takes=shared.schedule(p,source_sha256)
    layers={e['id']:e for e in p['layers']};removed=set();intervals=[];records=[]
    for d in decisions:
        shared.keys(d,('caption_id','chapter_id','expected_text','reason','edge_fade_seconds'),('caption_id','chapter_id','expected_text','reason','edge_fade_seconds'),'silent chapter decision')
        cid=d['caption_id'];shared.require(cid in layers and cid not in removed,'Unknown/repeated readout caption')
        c=layers[cid];shared.require(c['kind']=='caption' and c.get('source_dialogue_binding'),'Readout must be a source-bound caption')
        shared.require(c['text']==d['expected_text'],'Readout text changed')
        shared.require(isinstance(d['reason'],str) and len(d['reason'].strip())>=10,'Editorial reason required')
        fade=d['edge_fade_seconds'];shared.require(isinstance(fade,(int,float)) and not isinstance(fade,bool) and 0<=fade<=.05,'Fade must be within0..50ms')
        cards=[e for e in p['layers']if e.get('source_chapter_binding',{}).get('event_id')==d['chapter_id']]
        shared.require(len(cards)==5 and len({(e['in'],e['out'])for e in cards})==1,'Require one complete five-layer chapter fragment')
        titles=[e for e in cards if e['id'].endswith('-chapter-title')];heads=[e for e in cards if e['id'].endswith('-chapter-heading')]
        shared.require(len(titles)==1 and len(heads)==1,'Require title and chapter number')
        number=re.fullmatch(r'CHAPTER\s+(\d+)',heads[0]['text']);spoken=re.fullmatch(r'(\d+)번\s+(.+)',c['text'].strip())
        shared.require(number and spoken and int(number[1])==int(spoken[1]) and title_words(titles[0]['text'])==title_words(spoken[2]),'Spoken number/title differs from chapter')
        a,z=cards[0]['in'],cards[0]['out'];shared.require(a<=c['in']<c['out']<=z,'Card does not cover the entire readout')
        tid=c['source_dialogue_binding']['take_id'];shared.require(tid in takes and all(e['source_chapter_binding']['take_id']==tid for e in cards),'Readout/card take mismatch')
        take=takes[tid];shared.require(take['clip']['in']<=a<z<=take['clip']['out'],'Chapter outside source take')
        shared.require(not any(a<y and z>x for x,y in intervals),'Readout silence overlaps');intervals.append((a,z))
        shared.require(not any(e['kind']=='caption' and e['id']!=cid and e['in']<z+fade and e['out']>a-fade for e in p['layers']),'Silence/fade overlaps another dialogue caption')
        audio=[x for x in p['audio']if x['in']<z and x['out']>a]
        shared.require(len(audio)==1,'Require exactly one source speech track')
        voice=audio[0];shared.require(voice['in']<=a<z<=voice['out'] and voice['source']==take['clip']['source'] and voice.get('rate',1)==1,'Readout speech clock mismatch')
        shared.require(voice.get('fade_in',0)==voice.get('fade_out',0)==0,'Existing fades need separate treatment')
        shared.require(abs(voice['source_in']+a-voice['in']-(take['clip']['source_in']+a-take['clip']['in']))<1e-8,'Readout source mismatch')
        source_a=voice['source_in']+a-voice['in'];source_z=voice['source_in']+z-voice['in']
        removed.add(cid);records.append({'decision':copy.deepcopy(d),'take_id':tid,'removed_caption':copy.deepcopy(c),'original_audio':copy.deepcopy(voice),'silence_output_range':[a,z],'silence_output_frames':[shared.frame_boundary(a,fps),shared.frame_boundary(z,fps)],'source_range':[source_a,source_z],'card_layer_ids':[e['id']for e in cards],'edge_fade_seconds':fade})
    result=copy.deepcopy(p);result['layers']=[e for e in p['layers']if e['id']not in removed];voices=[]
    for voice in p['audio']:
        cuts=sorted([r for r in records if r['original_audio']['id']==voice['id']],key=lambda r:r['silence_output_range'])
        if not cuts:voices.append(copy.deepcopy(voice));continue
        cursor=voice['in'];left_fade=0.;fragments=[]
        for r in cuts:
            a,z=r['silence_output_range'];fade=r['edge_fade_seconds']
            if a>cursor:
                shared.require(a-cursor>=left_fade+fade,'Remaining voice too short for fades')
                fragments.append((cursor,a,left_fade,fade))
            cursor=z;left_fade=fade
        if cursor<voice['out']:
            shared.require(voice['out']-cursor>=left_fade,'Remaining voice too short for fade')
            fragments.append((cursor,voice['out'],left_fade,0.))
        for n,(a,z,fi,fo)in enumerate(fragments,1):
            piece=copy.deepcopy(voice);piece.update({'id':voice['id']+f'__silent_chapter_part{n:02d}','in':a,'out':z,'source_in':voice['source_in']+a-voice['in'],'fade_in':fi,'fade_out':fo,'silent_chapter_edit':{'original_audio_id':voice['id'],'output_range':[a,z]}});voices.append(piece)
    shared.require(len({a['id']for a in voices})==len(voices),'Audio fragment ID collision')
    result['audio']=voices
    for r in records:
        a,z=r['silence_output_range'];shared.require(not any(x['in']<z and x['out']>a for x in voices),'Readout still audible')
    shared.require(result['clips']==p['clips'] and result['duration']==p['duration'] and result.get('audio_master')==p.get('audio_master'),'Unrelated clock changed')
    return result,records


def apply(project,decisions,output):
    project,decisions,output=[Path(x).absolute()for x in (project,decisions,output)]
    shared.require(output.suffix=='.json' and output.parent.resolve()==project.parent.resolve(),'New JSON must be beside input')
    shared.require(not os.path.lexists(output),'Output exists')
    project=project.resolve();decisions=decisions.resolve();output=output.parent.resolve()/output.name
    p,psha=shared.read(project);spec,dsha=shared.read(decisions)
    shared.keys(spec,('schema','project_sha256','source_sha256','decisions'),('schema','project_sha256','source_sha256','decisions'),'readout decisions')
    shared.require(spec['schema']==SCHEMA and spec['project_sha256']==psha,'Readout schema/project SHA mismatch');shared.hash_literal(spec['source_sha256'],'source_sha256')
    validate(p,project.parent,True)
    paths={shared.path_at(project.parent,m['path'])for m in p['media'].values()if m.get('role')=='source_footage' and m.get('sha256')==spec['source_sha256']}
    shared.require(len(paths)==1,'One raw source required');raw=next(iter(paths));stat=shared.stat_signature(raw);shared.require(shared.sha(raw)==spec['source_sha256'],'Actual raw SHA mismatch')
    result,records=transform(p,spec['decisions'],spec['source_sha256'])
    result['silent_chapter_readouts']={'schema':SCHEMA,'input_project':shared.relative(project,output.parent),'input_sha256':psha,'decisions':shared.relative(decisions,output.parent),'decisions_sha256':dsha,'source_sha256':spec['source_sha256'],'implementation_sha256':shared.sha(Path(__file__)),'records':records,'silence_is_explicit_editorial_decision':True,'BGM_SFX_added':False,'direct_listening_seconds':0,'release_ready':False}
    result['release_ready']=False;validate(result,output.parent,True)
    shared.require(shared.sha(project)==psha and shared.sha(decisions)==dsha and shared.stat_signature(raw)==stat,'Inputs changed during readout edit')
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=output.parent,prefix='.silent-chapter-',suffix='.tmp',encoding='utf-8',delete=False)as f:
            tmp=f.name;json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.link(tmp,output)
    finally:
        if tmp is not None:os.unlink(tmp)
    return {'output':str(output),'sha256':shared.sha(output),'readouts':len(records),'layers':len(result['layers']),'audio_pieces':len(result['audio']),'release_ready':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for k in ('project','decisions','output'):parser.add_argument('--'+k,required=True)
    a=parser.parse_args();print(json.dumps(apply(a.project,a.decisions,a.output),ensure_ascii=False))
