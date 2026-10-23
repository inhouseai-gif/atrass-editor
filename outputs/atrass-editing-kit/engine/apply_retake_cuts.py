#!/usr/bin/env python3
"""Apply explicit source retake exclusions BEFORE compiling captions and graphics.

No automatic retake/word judgement. Every removed caption is separately declared;
kept captions/graphics must fit a single retained source interval. Rate1 with
identical voice/video source clocks only. New output directory, no overwrites.
"""
from pathlib import Path
import argparse,copy,json,math,re
import assemble_edit_plan as assembly

SCHEMA='atrass-source-retake-cuts-v1'
require=assembly.require

def fields(row,allowed,needed,label):
    require(isinstance(row,dict) and set(row)<=set(allowed) and set(needed)<=set(row),'Unknown/missing '+label+' fields')

def transform(decisions,transcript,spec,decisions_sha,transcript_sha,source_sha,duration):
    fields(spec,('schema','input_decisions_sha256','transcript_sha256','source_sha256','cuts','remove_captions'),('schema','input_decisions_sha256','transcript_sha256','source_sha256','cuts','remove_captions'),'selection')
    require(spec['schema']==SCHEMA,'Retake schema')
    require(spec['input_decisions_sha256']==decisions_sha and spec['transcript_sha256']==transcript_sha and spec['source_sha256']==source_sha,'Retake input hashes differ')
    require(decisions['schema']=='atrass-source-dialogue-v1' and decisions['transcript_sha256']==transcript_sha,'Dialogue input schema/hash')
    cuts=spec['cuts'];require(isinstance(cuts,list)and cuts,'Explicit nonempty cuts required');known=set();previous=-1
    for c in cuts:
        fields(c,('id','source_range','reason','evidence'),('id','source_range','reason','evidence'),'cut')
        require(isinstance(c['id'],str) and re.fullmatch('[A-Za-z0-9_-]+',c['id'])and c['id']not in known,'Unique safe cut ID');known.add(c['id'])
        require(len(c['source_range'])==2,'Cut range pair');lo,hi=c['source_range'];assembly.interval(lo,hi,duration,'Retake cut');require(lo>=previous,'Cuts must be sorted without overlap');previous=hi
        require(assembly.nonempty(c['reason'])and assembly.nonempty(c['evidence']),'Retake reason/evidence required')
    result=copy.deepcopy(decisions);result['takes']=[];mapping={};taken_ids={x['id']for x in decisions['takes']};affected=[]
    for old in decisions['takes']:
        if old['action']=='drop':result['takes'].append(copy.deepcopy(old));continue
        require(old['action']=='keep' and old.get('rate',1)==1 and old.get('audio_source_in',old['source_in'])==old['source_in'],'Retake tool only supports exact rate1 and identical source clocks')
        require(set(old.get('geometry',{}))<=set(('scale','scale_x','scale_y','tx','ty')),'Animated/unknown take geometry must be explicitly retimed before retake cuts')
        fragments=[(old['source_in'],old['source_out'])];hit=[]
        for c in cuts:
            lo,hi=c['source_range'];next_fragments=[]
            for a,z in fragments:
                if hi<=a or lo>=z:next_fragments.append((a,z));continue
                hit.append(c['id'])
                if a<lo:next_fragments.append((a,lo))
                if hi<z:next_fragments.append((hi,z))
            fragments=next_fragments
        children=[]
        for i,(a,z)in enumerate(fragments,1):
            e=copy.deepcopy(old);tid=old['id']if i==1 else old['id']+f'__retake_{i:03d}'
            require(i==1 or tid not in taken_ids,'Child take ID collision');taken_ids.add(tid)
            e.update(id=tid,source_in=a,source_out=z)
            if 'audio_source_in'in e:e['audio_source_in']=a
            if hit:
                e['reason']+=' Explicit retake exclusions: '+','.join(sorted(set(hit)))
                e['evidence']+=' Source-retake selection controls this retained interval; speech/pacing approval remains pending.'
            result['takes'].append(e);children.append({'id':tid,'source_range':[a,z]})
        mapping[old['id']]=children
        if hit:affected.append({'original_take':old,'retained':children,'cut_ids':sorted(set(hit))})
    require(affected,'No selected take intersects cuts')
    # Input drop decisions are preserved and new retake exclusions are explicit.
    for c in cuts:
        tid='retake-drop-'+c['id'];require(tid not in taken_ids,'Drop ID collision')
        result['takes'].append({'id':tid,'action':'drop','source_in':c['source_range'][0],'source_out':c['source_range'][1],'reason':c['reason'],'evidence':c['evidence']})
    remove={};captions={x['id']:x for x in decisions['captions']};segments=transcript['segments'];removed=[];cut_map={x['id']:x for x in cuts}
    require(isinstance(spec['remove_captions'],list),'Explicit removed-caption list required')
    for r in spec['remove_captions']:
        fields(r,('id','expected_text','expected_source_range','cut_id','retained_prefix_note'),('id','expected_text','expected_source_range','cut_id'),'removed caption')
        cid=r['id'];require(cid in captions and cid not in remove and r['cut_id']in cut_map,'Removed caption identity/cut')
        c=captions[cid];a,z,text,*_=assembly.selected_text(c,segments,duration,transcript_sha)
        require(text==r['expected_text'] and [a,z]==r['expected_source_range'],'Removed caption text/source mismatch')
        lo,hi=cut_map[r['cut_id']]['source_range'];require(a<hi and z>lo and z<=hi,'Removed caption must overlap cut without retained suffix')
        if a<lo:
            require(lo-a<=.300000001 and assembly.nonempty(r.get('retained_prefix_note')),'ASR prefix outside cut needs explicit <=300ms boundary explanation')
        remove[cid]=r;removed.append({'selection':r,'original_caption':c,'source_range':[a,z],'text':text})
    reassignments=[]
    for group in('captions','visual_events'):
        kept=[]
        for c in decisions.get(group,[]):
            if group=='captions'and c['id']in remove:continue
            a,z,*_=assembly.selected_text(c,segments,duration,transcript_sha)
            children=[x for x in mapping[c['take_id']]if x['source_range'][0]-1e-8<=a<z<=x['source_range'][1]+1e-8]
            require(len(children)==1,'Retained '+group+' crosses removed source: '+c['id'])
            item=copy.deepcopy(c);item['take_id']=children[0]['id'];kept.append(item)
            if item['take_id']!=c['take_id']:reassignments.append({'id':c['id'],'group':group,'from':c['take_id'],'to':item['take_id'],'source_range':[a,z]})
        if group in decisions:result[group]=kept
    receipt={'schema':SCHEMA,'input_decisions_sha256':decisions_sha,'transcript_sha256':transcript_sha,'source_sha256':source_sha,'cuts':cuts,'affected_takes':affected,'take_rebinding':mapping,'removed_captions':removed,'reassigned_selections':reassignments,'caption_words_changed':False,'automatic_semantic_approval':False,'direct_listening_seconds':0,'release_ready':False}
    return result,receipt


def apply(job_path,decisions_path,selection_path,output_dir):
    job_path,decisions_path,selection_path=map(lambda p:Path(p).resolve(),(job_path,decisions_path,selection_path));out=Path(output_dir).absolute();require(not out.exists(),'Choose new output directory')
    job,jsha=assembly.read_bound(job_path);d,dsha=assembly.read_bound(decisions_path);spec,ssha=assembly.read_bound(selection_path)
    transcript_path=(job_path.parent/job['files']['transcript']).resolve();transcript,tsha=assembly.read_bound(transcript_path)
    require(d['job_sha256']==jsha and job['raw']['sha256']==transcript['source_sha256'],'Prepared job/source binding')
    raw=Path(job['raw']['path']);raw=raw if raw.is_absolute()else(job_path.parent/raw).resolve();stat=(raw.stat().st_dev,raw.stat().st_ino,raw.stat().st_size,raw.stat().st_mtime_ns);require(assembly.digest(raw)==job['raw']['sha256'],'Actual source SHA')
    result,receipt=transform(d,transcript,spec,dsha,tsha,job['raw']['sha256'],job['raw']['duration_seconds'])
    require(assembly.digest(job_path)==jsha and assembly.digest(decisions_path)==dsha and assembly.digest(selection_path)==ssha and assembly.digest(transcript_path)==tsha and (raw.stat().st_dev,raw.stat().st_ino,raw.stat().st_size,raw.stat().st_mtime_ns)==stat,'Inputs changed while applying retake cuts')
    out.mkdir(parents=True,exist_ok=False)
    receipt.update(job_sha256=jsha,selection_sha256=ssha,implementation_sha256=assembly.digest(Path(__file__)))
    with(out/'decisions.json').open('x',encoding='utf-8')as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    receipt['output_decisions_sha256']=assembly.digest(out/'decisions.json')
    with(out/'retake-application.json').open('x',encoding='utf-8')as f:json.dump(receipt,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    # The established assembler rechecks complete selected words and quantization.
    assembly.assemble(job_path,out/'decisions.json',out/'edit-plan.json')
    return {'output_directory':str(out),'retake_cuts':len(spec['cuts']),'removed_captions':len(receipt['removed_captions']),'reassigned_selections':len(receipt['reassigned_selections']),'release_ready':False}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for name in('job','decisions','selection','output-dir'):ap.add_argument('--'+name,required=True)
    a=ap.parse_args();print(json.dumps(apply(a.job,a.decisions,a.selection,a.output_dir),ensure_ascii=False))
