#!/usr/bin/env python3
"""Create separate padded black shadow assets for static opaque rounded panels.

Uses the exact Composer rectangle mask, preserves every parent, and inserts its
shadow immediately before it at the same z. New dimensions require regenerating
assets. Defaults are a measured candidate, not confirmed original AE settings.
"""
from __future__ import annotations
import argparse,copy,hashlib,io,json,math,os
from pathlib import Path
import numpy as np
from render import Composer,rgba,validate
from create_text_halo import digest,finite,relative,padded_halo,verify_inputs,publish,TEMPORAL

SCHEMA='atrass-panel-shadow-generator-v1'
UNSUPPORTED=('box','character_reveal','reveal_keys','rotation_keys','scale_keys','x_keys','y_keys','shadow')

def check_parent(e):
    if e.get('kind')!='rect':raise ValueError('Panel shadow requires a rectangle layer')
    if any(key in e for key in UNSUPPORTED) or e.get('rotation',0)!=0 or e.get('scale',1)!=1:raise ValueError('Animated/transformed geometry or existing shadow is unsupported')
    if e.get('anchor') not in (None,'top_left'):raise ValueError('Only top-left panel positioning is supported')
    size=e.get('size');position=e.get('position')
    if not isinstance(size,list) or len(size)!=2 or any(type(v)is not int or not 1<=v<=8192 for v in size):raise ValueError('Panel size must be two integers within1..8192')
    if not isinstance(position,list) or len(position)!=2 or not all(finite(v)for v in position):raise ValueError('Expected finite xy position')
    radius=e.get('radius',0)
    if not finite(radius) or not 0<=radius<=min(size)/2:raise ValueError('Invalid panel corner radius')
    if rgba(e['fill'])[3]!=255 or e.get('stroke'):raise ValueError('Opaque fill and no panel stroke required')
    if not finite(e.get('z',0)):raise ValueError('Invalid z')
    for key in TEMPORAL[:1]+TEMPORAL[2:]:
        if key in e and (not finite(e[key])or e[key]<0):raise ValueError('Invalid temporal property')
    if e.get('opacity',1)>1:raise ValueError('Invalid opacity')
    if 'opacity_keys'in e:
        keys=e['opacity_keys'];last=-math.inf
        if not isinstance(keys,list)or not keys:raise ValueError('Invalid opacity keys')
        for k in keys:
            if not isinstance(k,list)or len(k)not in (2,3)or not finite(k[0])or k[0]<0 or k[0]<=last or not finite(k[1])or not 0<=k[1]<=1 or len(k)==3 and k[2]not in ('hold','linear','smoothstep'):raise ValueError('Invalid opacity key')
            last=k[0]

def build(project,layer_ids,output,asset_dir,*,sigma=6.5,opacity=.26,dx=0,dy=0):
    project,output,asset_dir=(Path(p).absolute()for p in (project,output,asset_dir))
    if output.suffix.lower()!='.json'or output.parent.resolve()!=project.parent.resolve():raise ValueError('New output JSON must be beside the input')
    if os.path.lexists(output)or os.path.lexists(asset_dir):raise FileExistsError('Output and asset directory must be absent')
    if not finite(sigma)or not 0<sigma<=64 or not finite(opacity)or not 0<opacity<=1:raise ValueError('Invalid sigma/opacity')
    if any(type(v)is not int or abs(v)>256 for v in (dx,dy)):raise ValueError('Integer offsets within256px required')
    if not layer_ids or len(set(layer_ids))!=len(layer_ids):raise ValueError('Distinct panel IDs required')
    raw=project.read_bytes();source_sha=hashlib.sha256(raw).hexdigest();p=json.loads(raw);validate(p,project.parent,True)
    if max(p['width'],p['height'])>8192:raise ValueError('Canvas exceeds8192pixels')
    if not isinstance(p.get('provenance',{}),dict):raise ValueError('Object provenance required')
    ids=[e['id']for e in p['layers']]
    if any(i not in ids for i in layer_ids):raise ValueError('Unknown panel ID')
    result=copy.deepcopy(p);result['release_ready']=False;comp=Composer(p,project.parent);pad=math.ceil(6*sigma+max(abs(dx),abs(dy)))+2
    artifacts=[];generated={};records=[]
    for number,ident in enumerate(layer_ids,1):
        parent=next(e for e in p['layers']if e['id']==ident);check_parent(parent)
        measurement=copy.deepcopy(parent)
        for key in TEMPORAL:measurement.pop(key,None)
        shape,xy=comp.image(measurement,parent['in']);mask=shape.getchannel('A')
        image=padded_halo(mask,sigma,opacity,dx,dy,pad);alpha=np.asarray(image)[:,:,3]
        if any(np.any(v)for v in (alpha[0],alpha[-1],alpha[:,0],alpha[:,-1])):raise ValueError('Shadow reaches asset edge')
        larger=padded_halo(mask,sigma,opacity,dx,dy,pad*2)
        if image.tobytes()!=larger.crop((pad,pad,pad+image.width,pad+image.height)).tobytes():raise ValueError('Shadow changes with expanded padding')
        bounds=image.getbbox();position=[xy[0]-pad,xy[1]-pad]
        if bounds is None:raise ValueError('Shadow rounds to transparent')
        world=[bounds[0]+position[0],bounds[1]+position[1],bounds[2]+position[0],bounds[3]+position[1]]
        if not (0<=world[0]<world[2]<=p['width']and 0<=world[1]<world[3]<=p['height']):raise ValueError('Visible shadow would be clipped')
        mid=f'panel-shadow-{number:03}';lid=ident+'-shadow'
        if mid in result['media']or lid in ids:raise ValueError('Generated ID collision')
        buf=io.BytesIO();image.save(buf,format='PNG');data=buf.getvalue();filename=mid+'.png';artifacts.append((filename,data))
        result['media'][mid]={'type':'image','role':'generated_panel_shadow_candidate','path':relative(asset_dir/filename,output.parent),'sha256':hashlib.sha256(data).hexdigest()}
        layer={'id':lid,'kind':'image','source':mid,'in':parent['in'],'out':parent['out'],'z':parent.get('z',0),'position':position,'source_layer_id':ident,'status':'generated_candidate_requires_visual_review'}
        for key in TEMPORAL:
            if key in parent:layer[key]=copy.deepcopy(parent[key])
        generated[ident]=layer;records.append({'parent_id':ident,'shadow_id':lid,'asset':filename,'sha256':hashlib.sha256(data).hexdigest(),'parent_size':parent['size'],'parent_position':parent['position'],'shadow_position':position,'alpha_world_bbox_exclusive':world,'edge_alpha_max':0,'expanded_padding_identical':True})
    result['layers']=[]
    for e in p['layers']:
        if e['id']in generated:result['layers'].append(generated[e['id']])
        result['layers'].append(copy.deepcopy(e))
    report={'schema':SCHEMA,'source_project':relative(project,asset_dir),'source_project_sha256':source_sha,'font_bindings':[],'renderer_sha256':digest(Path(__file__).with_name('render.py')),'generator_sha256':digest(Path(__file__)),'shared_publication_module_sha256':digest(Path(__file__).with_name('create_text_halo.py')),'effect_candidate':{'black':'#000000','sigma_px':sigma,'opacity':opacity,'offset_px':[dx,dy],'padding_px':pad},'layer_records':records,'original_layers_unchanged':True,'original_effect_settings_confirmed':False,'new_panel_sizes_supported_by_regeneration':True,'scope':'Static opaque rounded rects with unchanged opacity/fades. No motion, stroke, rotation or cropped shadows.','actual_render_completed':False,'style_match_approved':False,'release_ready':False}
    result.setdefault('provenance',{})['panel_shadow_generation']={'path':relative(asset_dir/'provenance.json',output.parent),'schema':SCHEMA}
    verify_inputs(project,report,asset_dir)
    return result,artifacts,report

def create(project,layer_ids,output,asset_dir,**kwargs):
    return publish(project,output,asset_dir,*build(project,layer_ids,output,asset_dir,**kwargs))

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('project',type=Path);ap.add_argument('--layer-id',action='append',required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--asset-dir',type=Path,required=True);ap.add_argument('--sigma',type=float,default=6.5);ap.add_argument('--opacity',type=float,default=.26);ap.add_argument('--dx',type=int,default=0);ap.add_argument('--dy',type=int,default=0);a=ap.parse_args()
    print(json.dumps(create(a.project,a.layer_id,a.output,a.asset_dir,sigma=a.sigma,opacity=a.opacity,dx=a.dx,dy=a.dy),ensure_ascii=False))

if __name__=='__main__':main()
