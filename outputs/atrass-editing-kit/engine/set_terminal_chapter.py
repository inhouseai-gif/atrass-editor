#!/usr/bin/env python3
"""Use the observed LAST CHAPTER heading on the explicitly selected final card.
Only heading text/x changes. Source timing, Korean title, other layers and audio
stay literal. Explicit SHA-bound evidence and a new adjacent output are required.
"""
from pathlib import Path
import argparse,copy,json,math,os,tempfile
from apply_source_assets import read,sha,keys,require,relative,path_at
from render import validate,Composer
SCHEMA='atrass-terminal-chapter-v1'

def transform(p,spec,measure):
    keys(spec,('schema','project_sha256','event_id','expected_heading','expected_title','reference'),('schema','project_sha256','event_id','expected_heading','expected_title','reference'),'terminal chapter')
    require(spec['schema']==SCHEMA and p['variant']=='A','Terminal chapter schema/variant')
    require('terminal_chapter_heading'not in p,'Terminal heading already applied')
    require(len({e['id']for e in p['layers']})==len(p['layers']),'Duplicate layer IDs')
    titles=[e for e in p['layers']if e.get('source_chapter_binding')and e['id'].endswith('-chapter-title')]
    target=[e for e in titles if e['source_chapter_binding']['event_id']==spec['event_id']]
    require(target and all(e['text']==spec['expected_title']for e in target),'Expected terminal title missing/different')
    first=min(e['in']for e in target)
    require(all(e['out']<=first for e in titles if e not in target),'Selected card is not the final source chapter')
    result=copy.deepcopy(p);records=[]
    for e in result['layers']:
        if not(e.get('source_chapter_binding',{}).get('event_id')==spec['event_id']and e['id'].endswith('-chapter-heading')):continue
        require(e['kind']=='text'and e['text']==spec['expected_heading'],'Heading differs from explicit decision')
        require(all(k not in e for k in ['glyph_width','position_keys','scale_keys','rotation','scale']),'Only static natural-width headings supported')
        title=next((t for t in target if t['in']==e['in']and t['out']==e['out']and t['source_chapter_binding']==e['source_chapter_binding']),None)
        require(title is not None,'Heading/title source lifetime mismatch')
        old=copy.deepcopy(e);e['text']='LAST CHAPTER';width,height=measure(e);require(width>0 and height>0 and width<=p['width']-192,'Heading fails safe width')
        e['position']=[math.floor((p['width']-width)/2+.5),old['position'][1]]
        require(0<=e['position'][1]and e['position'][1]+height<=p['height'],'Heading outside canvas')
        records.append({'id':e['id'],'old_layer':old,'new_layer':copy.deepcopy(e),'new_glyph_size':[width,height]})
    require(len(records)==len(target),'Exactly one terminal heading per title fragment required')
    return result,records

def apply(project,decision,output):
    project,decision,output=[Path(p).absolute()for p in (project,decision,output)]
    require(output.suffix=='.json'and output.parent.resolve()==project.parent.resolve(),'New JSON must be beside input')
    require(not os.path.lexists(output),'Output exists');project=project.resolve();decision=decision.resolve();output=output.parent.resolve()/output.name
    p,psha=read(project);spec,dsha=read(decision);require(spec['project_sha256']==psha,'Project SHA mismatch');ref=spec['reference'];keys(ref,('path','sha256','layer_id'),('path','sha256','layer_id'),'reference')
    refpath=path_at(decision.parent,ref['path']);rp,rsha=read(refpath);require(rsha==ref['sha256'],'Reference SHA mismatch');matched=[e for e in rp['layers']if e['id']==ref['layer_id']];require(len(matched)==1 and matched[0]['kind']=='text'and matched[0]['text']=='LAST CHAPTER','Evidence does not identify LAST CHAPTER')
    validate(p,project.parent,True);comp=Composer(p,project.parent);result,records=transform(p,spec,lambda e:comp.text_mask(e['text'],e).size)
    # Resource identity is checked independently of the template's nominal font.
    fonts=[]
    for fid in sorted({r['new_layer']['font']for r in records}):
        row=p['fonts'][fid];path=path_at(project.parent,row['path']);require(sha(path)==row['sha256'],'Font identity');fonts.append({'id':fid,'path':str(path),'sha256':row['sha256']})
    result['terminal_chapter_heading']={'schema':SCHEMA,'input_project':relative(project,output.parent),'input_sha256':psha,'decisions':relative(decision,output.parent),'decisions_sha256':dsha,'implementation_sha256':sha(Path(__file__)),'reference_sha256':rsha,'changed_headings':records,'fonts':fonts,'preserved':'Every field/layer except selected heading text and horizontal centering; source timing, audio, Korean title and typography unchanged.','direct_listening_seconds':0,'release_ready':False};result['release_ready']=False
    validate(result,output.parent,True);require(sha(project)==psha and sha(decision)==dsha and sha(refpath)==rsha,'Inputs changed while applying terminal heading')
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=output.parent,prefix='.terminal-chapter-',suffix='.tmp',delete=False,encoding='utf-8')as f:
            tmp=f.name;json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.link(tmp,output)
    finally:
        if tmp is not None:os.unlink(tmp)
    return {'output':str(output),'sha256':sha(output),'headings_changed':len(records),'timing_audio_unchanged':True,'release_ready':False}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for k in ['project','decision','output']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();print(json.dumps(apply(a.project,a.decision,a.output),ensure_ascii=False))
