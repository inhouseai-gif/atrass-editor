#!/usr/bin/env python3
"""Extend one existing caption to explicitly selected, previously omitted ASR words.
No source/audio edit. SHA-bound old text/range and transcript are mandatory.
Retains candidate caption font/colors, rebuilds its box and refuses overlap/overflow.
"""
from pathlib import Path
from fractions import Fraction
import argparse,copy,json,math,os,tempfile
from apply_source_assets import read,sha,keys,require,relative,path_at
from render import validate,Composer
SCHEMA='atrass-source-caption-extension-v1'

def transform(p,spec,t,measure):
    keys(spec,('schema','project_sha256','transcript','caption_id','expected_text','expected_source_range','new_word_range','evidence'),('schema','project_sha256','transcript','caption_id','expected_text','expected_source_range','new_word_range','evidence'),'caption extension')
    require(spec['schema']==SCHEMA and p['variant']=='A','Caption extension schema/variant');require(isinstance(spec['evidence'],str)and spec['evidence'].strip(),'Explicit editorial evidence required')
    require('source_caption_extension'not in p,'Extension already applied; use a fresh decision stage')
    old=[e for e in p['layers']if e['id']==spec['caption_id']];require(len(old)==1,'Unique existing caption required');old=old[0];binding=old.get('source_dialogue_binding',{})
    require(old['kind']=='caption'and old['text']==spec['expected_text']and binding.get('source_range')==spec['expected_source_range'],'Old caption text/source differs')
    require(binding.get('transcript_sha256')==spec['transcript']['sha256'],'Caption/transcript identity')
    si=binding['segment_index'];words=t['segments'][si]['words'];span=spec['new_word_range'];require(len(span)==2 and all(type(i)is int for i in span)and 0<=span[0]<span[1]<=len(words),'Explicit half-open word range')
    chosen=words[span[0]:span[1]];a,z=chosen[0]['start'],chosen[-1]['end'];old_a,old_z=binding['source_range']
    require(a==old_a and z>old_z,'Only a genuine suffix extension is supported')
    require(all(x['end']<=y['start']and x['start']<x['end']for x,y in zip(chosen,chosen[1:]))and chosen[-1]['start']<chosen[-1]['end'],'ASR word order')
    prefix=[w for w in chosen if w['end']<=old_z];require(prefix and prefix[-1]['end']==old_z and ''.join(w['word']for w in prefix).strip()==old['text'],'Old caption is not the exact selected-word prefix')
    clips=[c for c in p['clips']if c['id']==binding['take_id']];require(len(clips)==1,'Unique source take');c=clips[0];require(c.get('rate',1)==1,'Only rate1');end=c['source_in']+c['out']-c['in'];require(c['source_in']-1e-8<=a<z<=end+1e-8,'Extension crosses retained source cut')
    audio=[x for x in p['audio']if x['source']==c['source']and x['in']<=old['in']and x['out']>=old['out']]
    require(len(audio)==1 and audio[0].get('rate',1)==1 and abs((audio[0]['source_in']-audio[0]['in'])-(c['source_in']-c['in']))<1e-8,'Caption needs matching uninterrupted voice clock')
    fps=Fraction(p['fps']);newout=math.ceil((c['in']+z-c['source_in'])*float(fps)-1e-8)/float(fps)
    require(newout<=c['out']+1e-8 and newout<=audio[0]['out']+1e-8,'Extended output exceeds source/audio interval')
    require(not any(e['kind']=='caption'and e['id']!=old['id']and e['in']<newout and e['out']>old['in']for e in p['layers']),'Extended caption overlaps another caption')
    result=copy.deepcopy(p);new=next(e for e in result['layers']if e['id']==old['id']);new['text']=''.join(w['word']for w in chosen).strip();new['out']=newout;new['source_dialogue_binding']['source_range']=[a,z]
    width,height=measure(new);require(0<width<=1640 and 0<height<=80,'Extended caption must fit existing type size');width+=32;left=round(960-width/2);new['box']=[left,938,left+width,1018];new['text_offset']=[16,round((80-height)/2)]
    return result,{'old_layer':old,'new_layer':copy.deepcopy(new),'segment_index':si,'new_word_range':span,'added_words':chosen[len(prefix):],'timing_audio_unchanged':True}

def apply(project,decision,output):
    project,decision,output=[Path(x).absolute()for x in(project,decision,output)];require(output.suffix=='.json'and output.parent.resolve()==project.parent.resolve(),'New project must be beside input');require(not os.path.lexists(output),'Output exists');project=project.resolve();decision=decision.resolve();output=output.parent.resolve()/output.name
    p,psha=read(project);spec,dsha=read(decision);require(spec['project_sha256']==psha,'Project SHA mismatch');tr=spec['transcript'];keys(tr,('path','sha256'),('path','sha256'),'transcript');tp=path_at(decision.parent,tr['path']);t,tsha=read(tp);require(tsha==tr['sha256'],'Actual transcript SHA');require({m['sha256']for m in p['media'].values()if m.get('role')=='source_footage'}=={t['source_sha256']},'Source transcript identity')
    validate(p,project.parent,True);comp=Composer(p,project.parent);result,record=transform(p,spec,t,lambda e:comp.text_mask(e['text'],e).size)
    result['source_caption_extension']={'schema':SCHEMA,'input_project':relative(project,output.parent),'input_sha256':psha,'decisions':relative(decision,output.parent),'decisions_sha256':dsha,'implementation_sha256':sha(Path(__file__)),'record':record,'direct_listening_seconds':0,'release_ready':False};result['release_ready']=False;validate(result,output.parent,True);require(sha(project)==psha and sha(decision)==dsha and sha(tp)==tsha,'Inputs changed during caption extension')
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=output.parent,prefix='.caption-extension-',suffix='.tmp',encoding='utf-8',delete=False)as f:
            tmp=f.name;json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.link(tmp,output)
    finally:
        if tmp is not None:os.unlink(tmp)
    return {'output':str(output),'sha256':sha(output),'added_words':record['added_words'],'audio_unchanged':True,'release_ready':False}
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for k in ['project','decision','output']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();print(json.dumps(apply(a.project,a.decision,a.output),ensure_ascii=False))
