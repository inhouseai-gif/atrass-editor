#!/usr/bin/env python3
"""Synthetic Screen propagation and fail-closed interchange regression.

Run beside the candidate renderer/compiler/joiner/exporter. --baseline-engine
optionally compares normal NLE payload bytes with frozen earlier code. All data
is synthetic and temporary; no reference media, listening or Adobe app checks.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, contextlib, hashlib, importlib.util, io, json, shutil, tempfile
import av
import numpy as np
from PIL import Image
import render as renderer
import compile_plan as compiler
import join_reference_projects as joiner
import export_nle as nle
import validate_nle_export as nle_validator


def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def run(baseline_engine=None):
    checks=[];compared_frames=0;normal_payload_files=0
    def check(name,condition=True):
        if not condition:raise AssertionError(name)
        checks.append(name)
    def reject(name,fn):
        try:fn()
        except ValueError as exc:
            check(name);return str(exc)
        raise AssertionError('Not rejected: '+name)
    kit=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='atrass-screen-integration-') as tmp:
        d=Path(tmp);Image.new('RGBA',(4,4),(128,64,32,128)).save(d/'overlay.png')
        video=d/'base.mkv';container=av.open(str(video),'w');stream=container.add_stream('ffv1',rate=30)
        stream.width=8;stream.height=8;stream.pix_fmt='bgr0'
        for i in range(2):
            frame=av.VideoFrame.from_ndarray(np.full((8,8,3),64,np.uint8),format='rgb24');frame.pts=i;frame.time_base=Fraction(1,30)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
        container.close()
        media={'base':{'path':str(video),'role':'source_footage'},'overlay':{'path':str(d/'overlay.png'),'type':'image','role':'generated_effect'}}
        layer={'id':'light','kind':'image','source':'overlay','in':0,'out':2/30,'position':[1,1]}
        base={'schema':'atrass-render-v1','title':'Synthetic Screen integration','variant':'A','width':8,'height':8,'fps':'30','duration':2/30,'release_ready':False,'fonts':{},'media':media,'clips':[{'id':'base','source':'base','source_in':0,'in':0,'out':2/30,'geometry':{'scale':1}}],'layers':[layer],'audio':[]}
        source_hashes={p:digest(p) for p in (video,d/'overlay.png')}
        # Exercise both accepted explicit-plan entry points, not hand-built layers.
        for template in ('image_asset','primitive'):
            for mode in (None,'normal','screen'):
                label=template+'-'+str(mode);folder=d/label;folder.mkdir();parts=[]
                for i in range(2):
                    event={k:copy.deepcopy(v) for k,v in layer.items() if k!='kind'};event['template']=template
                    if template=='primitive':event['kind']='image'
                    if mode is not None:event['blend_mode']=mode
                    plan={'schema':'atrass-edit-plan-v1','variant':'A','fps':'30','duration':2/30,'media':media,'clips':base['clips'],'audio':[],'events':[event]}
                    planfile=folder/f'plan{i}.json';project=folder/f'part{i}.json';write(planfile,plan);compiler.compile_plan(planfile,project)
                    p=json.loads(project.read_text());p.update(reference_range_frames=[100+2*i,102+2*i],reference_identity={'sha256':'a'*64});write(project,p);parts.append(project)
                    check('compiler preserves mode '+label+' part'+str(i),p['layers'][0].get('blend_mode')==mode)
                manifest={'schema':'atrass-reference-join-v1','reference_sha256':'a'*64,'parts':[{'project':str(p),'sha256':digest(p),'reference_evidence':{'path':str(p),'sha256':digest(p),'pointer':'/reference_identity/sha256'}}for p in parts]}
                mf=folder/'join.json';write(mf,manifest);dest=folder/'joined.json';before={p:digest(p)for p in parts}
                joiner.join(mf,dest,audio_policy=joiner.POLICY,sample_peak_ceiling_dbfs=-1.5);merged=json.loads(dest.read_text())
                check('join retains execution field not metadata '+label,all(e.get('blend_mode')==mode and 'blend_mode' not in e.get('source_metadata',{}) for e in merged['layers']))
                comp=renderer.Composer(merged,folder);background=np.full((1080,1920,3),64,np.uint8)
                for partnum,path in enumerate(parts):
                    p=json.loads(path.read_text());original=renderer.Composer(p,folder)
                    for f in range(2):
                        t=(partnum*2+f)/30;active=[e for e in merged['layers']if e['in']<=t+1e-9<e['out']]
                        a=original.compose(background,p['layers'],f/30);b=comp.compose(background,active,t)
                        check('Compiler→join→Composer frame identity '+label+' f'+str(partnum*2+f),np.array_equal(a,b));compared_frames+=1
                        expected=[112,88,76] if mode=='screen' else [96,64,48]
                        check('declared blend pixel '+label+' f'+str(partnum*2+f),b[1,1].tolist()==expected)
                check('join input bytes unchanged '+label,all(digest(p)==h for p,h in before.items()))
                if mode is None and baseline_engine:
                    previous=load_module('previous_join',Path(baseline_engine)/'join_reference_projects.py');old_dest=folder/'previous-joined.json'
                    previous.join(mf,old_dest,audio_policy=previous.POLICY,sample_peak_ceiling_dbfs=-1.5);old=json.loads(old_dest.read_text())
                    merged_compare=copy.deepcopy(merged)
                    for field,filename in (('joiner_sha256','join_reference_projects.py'),('renderer_sha256','render.py')):
                        check('join code identity bound '+label+' '+field,
                              old['join_provenance'][field]==digest(Path(baseline_engine)/filename)
                              and merged_compare['join_provenance'][field]==digest(kit/'engine'/filename))
                        old['join_provenance'].pop(field);merged_compare['join_provenance'].pop(field)
                    check('absent mode old/new join plan equality except bound code hashes '+label,old==merged_compare)
        # Bad modes must be refused by compiler and actual join validation, not
        # just hidden as evidence metadata. Fresh SHA binding reaches semantics.
        folder=d/'reject';folder.mkdir()
        for index,edit in enumerate(({'blend_mode':'multiply'},{'blend_mode':'screen','kind':'rect'},{'blend_mode':'screen','scale':2},{'blend_mode':'screen','scale_x':2})):
            q=copy.deepcopy(base);q['layers'][0].update(edit);parts=[]
            for i in range(2):
                p=copy.deepcopy(q);p.update(reference_range_frames=[i*2,i*2+2],reference_identity={'sha256':'a'*64});path=folder/f'bad-{index}-{i}.json';write(path,p);parts.append(path)
            mf=folder/f'join-{index}.json';write(mf,{'schema':'atrass-reference-join-v1','reference_sha256':'a'*64,'parts':[{'project':str(p),'sha256':digest(p),'reference_evidence':{'path':str(p),'sha256':digest(p),'pointer':'/reference_identity/sha256'}}for p in parts]})
            out=folder/f'bad-out-{index}.json';reject('join renderer semantic rejection '+str(index),lambda:joiner.join(mf,out,audio_policy=joiner.POLICY,sample_peak_ceiling_dbfs=-1.5));check('bad join creates no output '+str(index),not out.exists())
            ev={**copy.deepcopy(q['layers'][0]),'template':'primitive'};plan={'schema':'atrass-edit-plan-v1','variant':'A','fps':'30','duration':2/30,'media':media,'clips':base['clips'],'events':[ev]};pp=folder/f'bad-plan-{index}.json';write(pp,plan)
            co=folder/f'bad-compiled-{index}.json';reject('compiler rejects unsupported blend intent '+str(index),lambda:compiler.compile_plan(pp,co));check('bad compile creates no output '+str(index),not co.exists())
        # Semantic NLE validation invokes preflight directly, without render.validate.
        for mode in ('screen','multiply',None,42,[],{}):
            q=copy.deepcopy(base);q['layers'][0]['blend_mode']=mode;reject('NLE preflight rejects mode '+repr(mode),lambda:nle.preflight(q))
        for scope in ('project','media','fonts','clip','clip_geometry','layer_geometry','audio','audio_geometry'):
            q=copy.deepcopy(base)
            if scope=='project':q['blend_mode']='normal'
            elif scope=='media':q['media']['overlay']['blend_mode']='normal'
            elif scope=='fonts':q['fonts']['unused']={'blend_mode':'normal'}
            elif scope=='clip':q['clips'][0]['blend_mode']='normal'
            elif scope=='clip_geometry':q['clips'][0]['geometry']['blend_mode']='normal'
            elif scope=='layer_geometry':q['layers'][0]['geometry']={'blend_mode':'normal'}
            else:q['audio']=[{'id':'audio',**({'blend_mode':'normal'} if scope=='audio' else {'geometry':{'blend_mode':'normal'}})}]
            reject('NLE standalone preflight rejects wrong scope '+scope,lambda:nle.preflight(q))
        q=copy.deepcopy(base);q['layers'][0]['blend_mode']='screen';project=d/'screen.json';write(project,q)
        for existing in (False,True):
            out=d/('existing-export' if existing else 'new-export')
            if existing:out.mkdir();(out/'user-file.txt').write_bytes(b'preserve-user-data')
            before={p.relative_to(out).as_posix():digest(p)for p in out.rglob('*')if p.is_file()}if existing else {}
            reject('Screen full export before any writes existing='+str(existing),lambda:nle.export(project,out,'unused-no-audio'))
            after={p.relative_to(out).as_posix():digest(p)for p in out.rglob('*')if p.is_file()}if out.exists()else{}
            check('Screen export preserves filesystem existing='+str(existing),before==after and (existing or not out.exists()))
        # Same absolute synthetic project and destination for old/new exports.
        # Only this temporary fixture directory is removed between runs.
        if baseline_engine:
            previous=load_module('previous_nle',Path(baseline_engine)/'export_nle.py')
            for mode in (None,'normal'):
                q=copy.deepcopy(base)
                if mode is not None:q['layers'][0]['blend_mode']=mode
                project=d/'normal.json';write(project,q);out=d/('normal-export-'+str(mode))
                with contextlib.redirect_stdout(io.StringIO()):previous.export(project,out,'unused-no-audio')
                oldfiles={p.relative_to(out).as_posix():p.read_bytes()for p in out.rglob('*')if p.is_file()}
                shutil.rmtree(out)
                with contextlib.redirect_stdout(io.StringIO()):nle.export(project,out,'unused-no-audio')
                newfiles={p.relative_to(out).as_posix():p.read_bytes()for p in out.rglob('*')if p.is_file()}
                check('normal NLE file inventory preserved '+str(mode),set(oldfiles)==set(newfiles))
                for name,data in oldfiles.items():
                    if name=='nle-manifest.json':
                        a=json.loads(data);b=json.loads(newfiles[name])
                        check('NLE exporter code identity bound '+str(mode),
                              a['exporter_sha256']==digest(Path(baseline_engine)/'export_nle.py')
                              and b['exporter_sha256']==digest(kit/'engine/export_nle.py'))
                        a.pop('exporter_sha256');b.pop('exporter_sha256');same=a==b
                    else:same=data==newfiles[name]
                    check('normal NLE payload unchanged '+str(mode)+' '+name,same);normal_payload_files+=1
                # Rebind the changed project SHA deliberately: the semantic validator
                # must still refuse Screen, not merely report stale input hashing.
                q['layers'][0]['blend_mode']='screen';write(project,q);manifest=json.loads((out/'nle-manifest.json').read_text());manifest['project_sha256']=digest(project);write(out/'nle-manifest.json',manifest)
                frozen={p:digest(p)for p in out.rglob('*')if p.is_file()}
                reject('NLE semantic validator refuses SHA-rebound screen '+str(mode),lambda:nle_validator.validate(out))
                check('NLE semantic refusal leaves export unchanged '+str(mode),all(digest(p)==h for p,h in frozen.items()))
        check('synthetic media preserved',all(digest(p)==h for p,h in source_hashes.items()))
    return {'schema':'atrass-screen-integration-selftest-v1','status':'passed','check_count':len(checks),'checks':checks,'compiler_join_composer_frames':compared_frames,'normal_nle_payload_files_compared':normal_payload_files,'baseline_comparison_performed':baseline_engine is not None,'baseline_comparison_renderer':'Both exporters use the candidate renderer; separate renderer regression establishes normal pixel preservation.','engine_sha256':{name:digest(kit/'engine'/name)for name in ('render.py','compile_plan.py','join_reference_projects.py','export_nle.py','validate_nle_export.py','selftest_screen_integration.py')},'synthetic_video_frames':2,'successful_normal_nle_export_executed':baseline_engine is not None,'screen_export_rejection_executed':True,'actual_Adobe_execution':False,'manual_visual_approval':False,'direct_listening_seconds':0}


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--baseline-engine',type=Path);args=ap.parse_args();print(json.dumps(run(args.baseline_engine),ensure_ascii=False,indent=2))
