#!/usr/bin/env python3
"""Attach editable A chapter cards to explicit source speech intervals.

Preserves footage, speech, captions and duration. This is a visual-only adapter:
production readouts requiring removal/silence need a separate editorial decision.
Output must be a new JSON beside the input so existing lineage remains valid.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, json, math, os, re, tempfile
import apply_source_assets as shared
import create_chapter
from render import validate

SCHEMA='atrass-source-chapters-v1'

def build(project,events,output):
    project,events,output=map(lambda x:Path(x).absolute(),(project,events,output))
    shared.require(output.suffix=='.json' and output.parent.resolve()==project.parent.resolve(),'Output JSON must be beside input')
    shared.require(not os.path.lexists(output),'Output exists; choose a new path')
    project=project.resolve();events=events.resolve();output=output.parent.resolve()/output.name
    p,psha=shared.read(project);spec,esha=shared.read(events)
    shared.keys(spec,('schema','project_sha256','source_sha256','events'),('schema','project_sha256','source_sha256','events'),'chapter events')
    shared.require(spec['schema']==SCHEMA and spec['project_sha256']==psha,'Chapter schema/project SHA mismatch')
    shared.hash_literal(spec['source_sha256'],'source_sha256')
    shared.require(p.get('variant')=='A' and (p['width'],p['height'],p['fps'])==(1920,1080,'60000/1001'),'Only A1920x1080 at60000/1001 is supported')
    validate(p,project.parent,True)
    fps,takes=shared.schedule(p,spec['source_sha256'])
    rawpaths={shared.path_at(project.parent,m['path']) for m in p['media'].values() if m.get('role')=='source_footage' and m.get('sha256')==spec['source_sha256']}
    shared.require(len(rawpaths)==1,'Unique raw source required');raw=next(iter(rawpaths));rawstat=shared.stat_signature(raw)
    shared.require(shared.sha(raw)==spec['source_sha256'] and shared.stat_signature(raw)==rawstat,'Actual raw SHA changed')
    shared.require(isinstance(spec['events'],list) and spec['events'],'Nonempty chapter events required')
    shared.require(len({e.get('id')for e in spec['events']})==len(spec['events']),'Repeated chapter IDs')
    result=copy.deepcopy(p);prepared=[];ranges=[];known={e['id']for e in p['layers']};dependencies={}
    for group in ('media','fonts'):
        for row in p.get(group,{}).values():
            path=shared.path_at(project.parent,row['path'])
            if path!=raw and 'sha256'in row:dependencies[path]=row['sha256']
    for e in spec['events']:
        shared.keys(e,('id','title','number','source_range','take_ids'),('id','title','number','source_range','take_ids'),'chapter event')
        shared.require(isinstance(e['id'],str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,95}',e['id']),'Unsafe chapter ID')
        s0,s1=shared.pair(e['source_range'],'source_range');shared.require(0<=s0<s1,'Invalid chapter source range')
        ids=e['take_ids'];shared.require(isinstance(ids,list) and ids and len(set(ids))==len(ids) and all(x in takes for x in ids),'Explicit unique supported takes required')
        fragments=[]
        for tid in ids:
            m=takes[tid];a,z=m['frames'];c=m['clip'];lo=max(s0,m['source_range'][0]);hi=min(s1,m['source_range'][1]);shared.require(lo<hi,'No chapter/take intersection')
            first=max(a,math.ceil((a/float(fps)+lo-c['source_in'])*float(fps)-1e-8));last=min(z,math.ceil((a/float(fps)+hi-c['source_in'])*float(fps)-1e-8));shared.require(first<last,'Empty CFR chapter interval')
            shared.require(not any(first<b and last>a for a,b in ranges),'Chapter intervals overlap')
            shared.require(not any(row['kind']!='caption' and row['in']<last/float(fps) and row['out']>first/float(fps) for row in p['layers']),'Chapter overlaps an existing non-dialogue layer; explicit resolution required')
            ranges.append((first,last));fragments.append({'take_id':tid,'source_intersection':[lo,hi],'output_frames':[first,last]})
        fragments.sort(key=lambda x:x['output_frames'])
        # The standalone template supplies independent editable geometry only.
        # Its duration rounding and silent base clip are never inserted.
        template=create_chapter.make_project(e['title'],e['number'],'1',output,kit=Path(__file__).resolve().parents[1])
        for group in ('media','fonts'):
            for old,item in template[group].items():
                new='source-chapter-'+old
                if new in result[group]:shared.require(result[group][new]==item,'Chapter resource ID collision')
                else:result[group][new]=copy.deepcopy(item)
                path=shared.path_at(output.parent,item['path']);dependencies[path]=item['sha256']
        provenance=template['chapter_template']['background_provenance'];dependencies[shared.path_at(output.parent,provenance['path'])]=provenance['sha256']
        components=[{'id':'gradient','kind':'image','source':'procedural-gradient','position':[0,0],'z':1},*copy.deepcopy(template['layers'])]
        for component,z in zip(components,(1,2,3,4,5)):component['z']=z
        shared.require(all(row.get('z',0)>5 for row in p['layers'] if row['kind'] in ('caption','text','label') and any(row['in']<hi/float(fps) and row['out']>lo/float(fps) for lo,hi in [f['output_frames']for f in fragments])),'Existing text would be covered')
        for i,fragment in enumerate(fragments,1):
            lo,hi=fragment['output_frames'];a,b=float(Fraction(lo,1)/fps),float(Fraction(hi,1)/fps)
            for component in components:
                layer=copy.deepcopy(component);layer['id']=f"source-chapter-{e['id']}-part{i:03d}-{component['id']}"
                shared.require(layer['id']not in known,'Chapter layer ID collision');known.add(layer['id'])
                layer['in']=a;layer['out']=b;layer['status']='source_bound_procedural_chapter_candidate'
                for key in ('font','source'):
                    if key in layer:layer[key]='source-chapter-'+layer[key]
                layer['source_chapter_binding']={'event_id':e['id'],'take_id':fragment['take_id'],'source_sha256':spec['source_sha256'],'requested_source_range':e['source_range'],'source_intersection':fragment['source_intersection'],'output_frames':[lo,hi],'events_sha256':esha}
                result['layers'].append(layer)
        prepared.append({'event':e,'fragments':fragments,'template':template['chapter_template'],'components_per_fragment':5,'visual_only':True})
    shared.require(result['clips']==p['clips'] and result['audio']==p['audio'] and result.get('audio_master')==p.get('audio_master') and result['layers'][:len(p['layers'])]==p['layers'],'Preserved input fields changed')
    shared.require(shared.sha(project)==psha and shared.sha(events)==esha and shared.stat_signature(raw)==rawstat,'Inputs changed during chapter application')
    shared.require(all(shared.sha(path)==sha for path,sha in dependencies.items()),'Chapter dependency changed')
    if 'source_chapter_application'in p:
        result['source_chapter_application_history']=[*copy.deepcopy(p.get('source_chapter_application_history',[])),copy.deepcopy(p['source_chapter_application'])]
    result['release_ready']=False;result['source_chapter_application']={'schema':SCHEMA,'input_project_path':shared.relative(project,output.parent),'input_project_sha256':psha,'events_path':shared.relative(events,output.parent),'events_sha256':esha,'source_sha256':spec['source_sha256'],'implementation_sha256':shared.sha(Path(__file__)),'template_implementation_sha256':shared.sha(Path(create_chapter.__file__)),'mapping_implementation_sha256':shared.sha(Path(shared.__file__)),'events':prepared,'preserved_literal_fields':['clips','audio','audio_master','all_preexisting_layers','duration'],'direct_listening_seconds':0,'style_approved':False,'release_ready':False}
    validate(result,output.parent,True)
    return result


def apply_chapters(project,events,output):
    output=Path(output).absolute();result=build(project,events,output);tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=output.parent,prefix='.source-chapters-',suffix='.tmp',encoding='utf-8',delete=False)as f:
            tmp=f.name;json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.link(tmp,output)
    finally:
        if tmp is not None:os.unlink(tmp)
    return {'output':str(output),'sha256':shared.sha(output),'events':len(result['source_chapter_application']['events']),'layers_added':len(result['layers'])-len(json.loads(Path(project).read_text())['layers']),'audio_unchanged':True,'release_ready':False}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('project','events','output'):ap.add_argument('--'+name,required=True)
    a=ap.parse_args();print(json.dumps(apply_chapters(a.project,a.events,a.output),ensure_ascii=False))
if __name__=='__main__':main()
