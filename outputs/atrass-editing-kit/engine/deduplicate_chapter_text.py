#!/usr/bin/env python3
"""Remove explicitly selected exact-copy text duplicated by a source chapter.

Captions can be removed only when the retained chapter title covers their whole
lifetime. Labels can also immediately precede the same title. Every removed
layer is retained in provenance; footage, audio, resources and time stay literal.
"""
from pathlib import Path
import argparse,copy,json,os,tempfile
from apply_source_assets import read,sha,keys,require,relative,frame_boundary
from fractions import Fraction
from render import validate

SCHEMA='atrass-source-chapter-dedup-v1'


def resolve_decisions(p,decisions):
    require(isinstance(decisions,list) and decisions,'Explicit decisions required')
    byid={e['id']:e for e in p['layers']};removed=set();retained=set();records=[];fps=Fraction(p['fps'])
    for d in decisions:
        keys(d,('remove_id','retain_id'),('remove_id','retain_id'),'duplicate decision')
        a,b=d['remove_id'],d['retain_id'];require(a in byid and b in byid and a!=b and a not in removed,'Unknown/repeated duplicate IDs')
        old,new=byid[a],byid[b];require(old['kind']in ('caption','label'),'Only caption/label duplicates can be removed')
        require(new['kind']=='text' and new.get('source_chapter_binding') and b.endswith('-chapter-title'),'Retain a bound chapter title')
        require(old.get('source_dialogue_binding') or old.get('source_visual_binding'),'Removed text must be source bound')
        require(' '.join(old['text'].split())==' '.join(new['text'].split()),'Different text cannot be deduplicated')
        x,y=[frame_boundary(old[k],fps)for k in ('in','out')];u,v=[frame_boundary(new[k],fps)for k in ('in','out')]
        covered=u<=x<y<=v;adjacent=old['kind']=='label' and y==u
        require(covered or adjacent,'Chapter does not cover caption or immediately follow label')
        old_binding=old.get('source_dialogue_binding',old.get('source_visual_binding'))
        require(old_binding['take_id']==new['source_chapter_binding']['take_id'],'Duplicate text comes from different takes')
        removed.add(a);retained.add(b);records.append({'removed_layer':copy.deepcopy(old),'retained_layer_id':b,'reason':'covered_by_identical_chapter_title'if covered else 'immediately_repeated_by_identical_chapter_title','removed_frames':[x,y],'retained_frames':[u,v]})
    require(not removed & retained,'Retained layer cannot also be removed')
    result=copy.deepcopy(p);result['layers']=[e for e in result['layers']if e['id']not in removed]
    return result,records


def apply(project,decisions,output):
    project,decisions,output=[Path(x).absolute()for x in (project,decisions,output)]
    require(output.suffix=='.json' and output.parent.resolve()==project.parent.resolve(),'New JSON must be beside input')
    require(not os.path.lexists(output),'Output exists')
    project=project.resolve();decisions=decisions.resolve();output=output.parent.resolve()/output.name
    p,psha=read(project);spec,dsha=read(decisions)
    keys(spec,('schema','project_sha256','decisions'),('schema','project_sha256','decisions'),'dedup specification')
    require(spec['schema']==SCHEMA and spec['project_sha256']==psha,'Dedup schema/project mismatch')
    validate(p,project.parent,True);result,records=resolve_decisions(p,spec['decisions'])
    result['source_chapter_deduplication']={'schema':SCHEMA,'input_project':relative(project,output.parent),'input_sha256':psha,'decisions':relative(decisions,output.parent),'decisions_sha256':dsha,'implementation_sha256':sha(Path(__file__)),'removed_layers_retained_in_provenance':records,'source_audio_and_duration_unchanged':True,'editorial_choice':'Remove exact repeated chapter words only; do not delete source speech.','release_ready':False}
    result['release_ready']=False;validate(result,output.parent,True)
    require(sha(project)==psha and sha(decisions)==dsha,'Input changed while deduplicating')
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=output.parent,prefix='.chapter-dedup-',suffix='.tmp',encoding='utf-8',delete=False)as f:
            tmp=f.name;json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.link(tmp,output)
    finally:
        if tmp is not None:os.unlink(tmp)
    return {'output':str(output),'sha256':sha(output),'removed_layers':len(records),'release_ready':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for k in ('project','decisions','output'):parser.add_argument('--'+k,required=True)
    a=parser.parse_args();print(json.dumps(apply(a.project,a.decisions,a.output),ensure_ascii=False))
