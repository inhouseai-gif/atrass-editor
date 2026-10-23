#!/usr/bin/env python3
"""Meaningful join invariants, actual Composer pixel comparisons and rejections.

Uses small generated fixture images and the bundled OFL Noto font. Does not
certify reference reconstruction, perform listening or import into Adobe apps.
"""
from pathlib import Path
import copy, json, tempfile, hashlib, math, wave
from fractions import Fraction
import numpy as np
import cv2
from PIL import Image
from render import Composer, evaluate_keys, resolve
import join_reference_projects as j


def run():
    kit=Path(__file__).resolve().parents[1];checks=[];frame_differences=[]
    def check(n,ok=True):
        if not ok:raise AssertionError(n)
        checks.append(n)
    def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='atrass-join-test-')as td:
        root=Path(td);d1=root/'part1';d2=root/'nested/part2';outdir=root/'joined';d1.mkdir();d2.mkdir(parents=True);outdir.mkdir()
        paths=[];refsha='a'*64
        yy,xx=np.indices((96,160));raster=np.stack([xx%256,yy%256,(xx+yy)%256],axis=2).astype(np.uint8)
        for index,folder in enumerate((d1,d2)):
            Image.fromarray(raster).save(folder/'background.png');(folder/'license.txt').write_text('Fixture license identity, not redistribution approval.',encoding='utf-8')
            with wave.open(str(folder/'voice.wav'),'wb')as w:
                w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000);w.writeframes(np.zeros((48000,2),np.int16).tobytes())
            p={'schema':'atrass-render-v1','variant':'A','width':160,'height':96,'fps':'30','duration':.2,'release_ready':False,'reference_range_frames':[100+6*index,106+6*index],'reference_identity':{'sha256':refsha},'media':{'base':{'path':'background.png','type':'image','role':'generated_replacement_illustration'},'voice':{'path':'voice.wav','role':'source_footage'}},'fonts':{'body':{'path':str(kit/'assets/fonts/NotoSansKR[wght].ttf'),'weight':450,'license':'license.txt'}},'clips':[{'id':'base','in':0,'out':.2,'source':'base','source_in':.45,'source_frame_times':[.45+i/60 for i in range(6)],'geometry':{'policy':'known geometry annotation','scale_keys':[[0,1,'linear'],[.2,1.1,'linear']],'tx_keys':[[0,0],[.2,-8]],'ty_keys':[[0,0],[.2,-5]]},'status':'candidate','source_native_indices':list(range(27,33))}],'layers':[{'id':'title','kind':'text','in':0,'out':.2,'font':'body','font_size':18,'text':'합치기 '+str(index),'fill':'#FFFFFF','position':[8,5],'character_reveal':{'duration':.15,'glyph_fade_duration':.02,'start':.01},'opacity_keys':[[0,.5],[.2,1]],'x_keys':[[0,8],[.2,10]],'semantic_layer_id':'shared-semantic'},{'id':'plate','kind':'rect','in':1/30,'out':5/30,'position':[10,50],'size':[60,15],'fill':'#cc4444','z':-1}],'audio':[{'id':'voice','in':.013,'out':.192,'source':'voice','source_in':.1,'gain_db':-2,'fade_in':.01,'fade_out':.02}],'audio_master':{'sample_peak_ceiling_dbfs':-1.0-index*.5},'historical_note':{'path':'relative/evidence.txt'}}
            p['layers'][0]['z_evidence']={'reason':'Observed overlap annotation only','native_frames':[103,104],'z':999,'nested':{'opacity':0}}
            p['layers'][0].update(reference_derived_background=False,project_local_geometry_candidate={'bbox':[8,5,150,30],'sample_frame':103,'authored_geometry_confirmed':False},progressive_role='identical_prefix_mask_retained')
            path=folder/'project.json';write(path,p);paths.append(path)
        manifest=root/'join.json'
        def make_manifest():
            return {'schema':'atrass-reference-join-v1','reference_sha256':refsha,'parts':[{'project':str(p.relative_to(root)),'sha256':j.digest(p),'reference_evidence':{'path':str(p.relative_to(root)),'sha256':j.digest(p),'pointer':'/reference_identity/sha256'}}for p in paths]}
        write(manifest,make_manifest());before={p:j.digest(p)for p in paths};output=outdir/'result.json'
        result=j.join(manifest,output,audio_policy=j.POLICY,sample_peak_ceiling_dbfs=-1.5);merged=j.read(output)
        check('12 frames and adjacent reference range',result['native_frames']==12 and merged['reference_range_frames']==[100,112])
        check('executable IDs namespaced across identical original IDs',len({x['id']for x in merged['layers']})==4 and len(merged['media'])==4 and len(merged['fonts'])==2)
        check('original semantic identity preserved',[x['semantic_layer_id']for x in merged['layers']if x.get('semantic_layer_id')]==['shared-semantic']*2)
        check('explicit new global master',merged['audio_master']=={'sample_peak_ceiling_dbfs':-1.5} and merged['join_provenance']['audio_policy']==j.POLICY)
        check('z_evidence dictionary preserved only under source_metadata',all('z_evidence' not in e and e['source_metadata']['z_evidence']==j.read(paths[i])['layers'][0]['z_evidence'] for i,e in enumerate([x for x in merged['layers'] if x['id'].endswith('__title')])))
        check('known body20 annotations preserved as data only',all(k not in e and e['source_metadata'][k]==j.read(paths[i])['layers'][0][k] for i,e in enumerate([x for x in merged['layers'] if x['id'].endswith('__title')]) for k in ('reference_derived_background','project_local_geometry_candidate','progressive_role')))
        for index,path in enumerate(paths):
            original=j.read(path);c=merged['clips'][index];source=original['clips'][0]
            check('source arrays, source origin and local geometry preserved '+str(index),c['source_frame_times']==source['source_frame_times']and c['source_in']==source['source_in']and c['geometry']=={k:v for k,v in source['geometry'].items()if k in j.GEOMETRY}and c['source_metadata']['geometry_annotations']['policy']=='known geometry annotation'and c['source_metadata']['source_native_indices']==source['source_native_indices'])
            ml=next(e for e in merged['layers']if e['id']=='p%03d__title'%index)
            check('reveal/local layer keys unchanged '+str(index),all(ml[k]==original['layers'][0][k]for k in ('character_reveal','opacity_keys','x_keys')))
            ma=merged['audio'][index];oa=original['audio'][0]
            check('audio source/gain/fades unchanged '+str(index),all(ma[k]==oa[k]for k in ('source_in','gain_db','fade_in','fade_out')) and abs(ma['in']-index*.2-oa['in'])<1e-12)
            check('relative resources point to original bytes '+str(index),j.digest(resolve(outdir,merged['media']['p%03d__base'%index]['path']))==j.digest(path.parent/'background.png'))
        # Exercise actual compositor and clip affine on every synthetic native frame.
        comp=Composer(merged,outdir)
        def scene(p,folder,composer,t):
            clip=next(c for c in p['clips']if c['in']<=t+1e-9<c['out']);arr=np.asarray(Image.open(resolve(folder,p['media'][clip['source']]['path'])).convert('RGB'));loc=t-clip['in'];g=clip['geometry'];s=evaluate_keys(g.get('scale_keys'),loc,g.get('scale',1));tx=evaluate_keys(g.get('tx_keys'),loc,g.get('tx',0));ty=evaluate_keys(g.get('ty_keys'),loc,g.get('ty',0));base=cv2.warpAffine(arr,np.array([[s,0,tx],[0,s,ty]],np.float32),(p['width'],p['height']),flags=cv2.INTER_AREA)
            return composer.compose(base,[e for e in p['layers']if e['in']<=t+1e-9<e['out']],t)
        for index,path in enumerate(paths):
            p=j.read(path);old=Composer(p,path.parent)
            if index==0:
                without=copy.deepcopy(p)
                for k in ('z_evidence','reference_derived_background','project_local_geometry_candidate','progressive_role'):without['layers'][0].pop(k)
                check('known annotations have no Composer pixel or ordering effect',np.array_equal(scene(p,path.parent,old,.1),scene(without,path.parent,Composer(without,path.parent),.1)))
            diffs=[]
            for f in range(6):
                a=scene(p,path.parent,old,f/30);b=scene(merged,outdir,comp,(index*6+f)/30);diffs.append(int(np.max(np.abs(a.astype(int)-b.astype(int)))))
            frame_differences.extend(diffs);check('actual Composer+affine six frames within one RGB code value part '+str(index),max(diffs)<=1)
        check('input files byte-identical after join',all(j.digest(p)==v for p,v in before.items()))
        # Every negative case starts from the valid original files and rebinds their
        # digests deliberately so semantic validation, not stale SHA, is exercised.
        original_bytes=[p.read_bytes()for p in paths];serial=0
        def reject(name,mutate=None,manifest_mutate=None,policy=j.POLICY):
            nonlocal serial
            for p,b in zip(paths,original_bytes):p.write_bytes(b)
            if mutate:
                p=j.read(paths[1]);mutate(p);write(paths[1],p)
            m=make_manifest()
            if manifest_mutate:manifest_mutate(m)
            write(manifest,m);serial+=1;target=outdir/('negative-%d.json'%serial)
            try:j.join(manifest,target,audio_policy=policy,sample_peak_ceiling_dbfs=-1.5)
            except (ValueError,KeyError,TypeError):check('reject '+name,not target.exists());return
            raise AssertionError('Accepted '+name)
        reject('missing reference range',lambda p:p.pop('reference_range_frames'))
        reject('reference gap',lambda p:p.update(reference_range_frames=[107,113]))
        reject('reference overlap',lambda p:p.update(reference_range_frames=[105,111]))
        reject('different reference source',lambda p:p['reference_identity'].update(sha256='b'*64))
        reject('changed project bytes',manifest_mutate=lambda m:m['parts'][1].update(sha256='0'*64))
        reject('changed evidence bytes',manifest_mutate=lambda m:m['parts'][1]['reference_evidence'].update(sha256='0'*64))
        reject('mismatched FPS',lambda p:p.update(fps='60'))
        reject('mismatched canvas',lambda p:p.update(width=162))
        reject('wrong reference duration',lambda p:p.update(reference_range_frames=[106,113]))
        reject('non-native layer boundary',lambda p:p['layers'][0].update(**{'in':.001}))
        reject('unknown global motion keys',lambda p:p['layers'][0].update(global_x_keys=[[0,0],[1,2]]))
        reject('unrecognized z_evidence-like field',lambda p:p['layers'][0].update(z_evidence_runtime={'z':999}))
        reject('unsupported clip rotation',lambda p:p['clips'][0]['geometry'].update(rotation=1))
        reject('unsupported static scale ignored by renderer',lambda p:p['layers'][0].update(scale=2))
        reject('nested chat group',lambda p:p['layers'][1].update(kind='chat'))
        reject('duplicate local cache ID',lambda p:p['layers'][1].update(id='title'))
        reject('resource SHA mismatch',lambda p:p['media']['base'].update(sha256='0'*64))
        reject('font license SHA mismatch',lambda p:p['fonts']['body'].update(license_sha256='0'*64))
        reject('implicit audio policy',policy=None)
        reject('array length mismatch',lambda p:p['clips'][0]['source_frame_times'].pop())
        for p,b in zip(paths,original_bytes):p.write_bytes(b)
        write(manifest,make_manifest())
        original_digest=j.digest;mutated=[False]
        def racing_digest(path):
            value=original_digest(path)
            if Path(path).name=='background.png' and not mutated[0]:
                mutated[0]=True;changed=j.read(manifest);changed['parts'].reverse();write(manifest,changed)
            return value
        j.digest=racing_digest
        try:
            try:j.join(manifest,outdir/'raced.json',audio_policy=j.POLICY,sample_peak_ceiling_dbfs=-1.5)
            except ValueError as exc:check('manifest mutation during join rejected before publish',mutated[0]and 'manifest changed' in str(exc)and not (outdir/'raced.json').exists())
            else:raise AssertionError('Changed manifest accepted')
        finally:j.digest=original_digest
        write(manifest,make_manifest());old=j.digest(output)
        try:j.join(manifest,output,audio_policy=j.POLICY,sample_peak_ceiling_dbfs=-1.5)
        except ValueError:check('existing output preserved',j.digest(output)==old)
        else:raise AssertionError('Overwrite accepted')
    return {'schema':'atrass-reference-join-selftest-v1','status':'passed','checks':len(checks),'check_names':checks,'compositor_compared_frames':12,'maximum_preencode_pixel_error':max(frame_differences),'frame_pixel_errors':frame_differences,'float_local_time_limit':'Global timeline subtraction can change rounding at opacity ties by one RGB code value in this fixture; no universal byte-identity claim.','actual_video_encode':False,'manual_visual_approval':False,'direct_listening_seconds':0}
if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False,indent=2))
