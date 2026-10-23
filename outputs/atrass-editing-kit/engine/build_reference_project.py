#!/usr/bin/env python3
"""Build a draft from measured source mappings and reviewed reference text.

The finished reference is measurement input only; it is never a rendered media
source. Unreviewed text is omitted and recorded, never silently approved.
"""
from pathlib import Path
from fractions import Fraction
import argparse,copy,hashlib,json,math,re,sys
import numpy as np
import cv2
from render import SourceReader,validate

FPS=Fraction(60000,1001)
A_REFERENCE_ID='DvDeXNPS3Wc'
A_REFERENCE_SHA256='6d00a27dbba7618cd000d70998295e6ae364c6b8a8d6e46870614ca83ffae85b'
def seconds(frame):return float(Fraction(frame,1)/FPS)
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(2**20),b''):digest.update(chunk)
    return digest.hexdigest()

def load_geometry_overrides(path,captions_path,reference_path,kit):
    """Select source-bound, finite observed geometry only through an explicit path.

    Without a reference file, identity is bound to the exact reviewed caption
    manifest and hashed packaged evidence. This does not claim that the media
    file itself was checked during this build. No override is ever auto-loaded.
    """
    caption_bytes=Path(captions_path).read_bytes()
    captions=json.loads(caption_bytes.decode('utf-8'))
    if not path:return {},None,captions
    kit=Path(kit).resolve();path=Path(path).resolve()
    manifest_bytes=path.read_bytes();data=json.loads(manifest_bytes.decode('utf-8'))
    caption_sha=hashlib.sha256(caption_bytes).hexdigest()
    if data.get('schema')!='atrass-caption-geometry-overrides-v1':raise ValueError('Unsupported geometry override schema')
    if captions.get('video_id')!=A_REFERENCE_ID:raise ValueError('Geometry overrides are restricted to reference A')
    if data.get('source_sha256')!=A_REFERENCE_SHA256 or captions.get('source_sha256')!=A_REFERENCE_SHA256:
        raise ValueError('Geometry override reference source SHA mismatch')
    if data.get('input_timeline_sha256')!=caption_sha:raise ValueError('Geometry override caption timeline SHA mismatch')
    if data.get('coordinate_reference')!=[1920,1080] or captions.get('coordinate_reference')!=[1920,1080]:
        raise ValueError('Geometry override canvas mismatch')
    if data.get('bbox_convention')!='[left,top,right_exclusive,bottom_exclusive]':raise ValueError('Unsupported geometry bbox convention')
    actual_reference_sha=None
    if reference_path:
        actual_reference_sha=file_sha256(reference_path)
        if actual_reference_sha!=A_REFERENCE_SHA256:raise ValueError('Geometry override actual reference file SHA mismatch')
    caption_events=captions.get('events',[]);event_ids=[e.get('id') for e in caption_events]
    if len(set(event_ids))!=len(event_ids):raise ValueError('Duplicate caption event ID')
    by_id={e['id']:e for e in caption_events};selected={}
    records=data.get('events')
    if not isinstance(records,list) or not records:raise ValueError('Geometry override event list is empty or invalid')
    def integer(value):return isinstance(value,int) and not isinstance(value,bool)
    def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    def bbox(value,label):
        if not isinstance(value,list) or len(value)!=4 or not all(integer(x) for x in value):raise ValueError('Invalid '+label)
        if not (0<=value[0]<value[2]<=1920 and 0<=value[1]<value[3]<=1080):raise ValueError('Out-of-canvas '+label)
        return value
    for record in records:
        eid=record.get('id')
        if eid in selected:raise ValueError('Duplicate geometry override '+str(eid))
        event=by_id.get(eid)
        if not event or event.get('layer')!='dialogue' or event.get('render_enabled') is not True:
            raise ValueError('Geometry override does not target a render-enabled dialogue '+str(eid))
        if record.get('text')!=event.get('text'):raise ValueError('Geometry override text mismatch '+eid)
        frames=record.get('event_frames')
        if frames!=[event.get('in_frame'),event.get('out_frame')] or not all(integer(f) for f in frames) or frames[1]<=frames[0]:
            raise ValueError('Geometry override event frame mismatch '+eid)
        sample=record.get('sample_frame')
        if not integer(sample) or not frames[0]<=sample<frames[1]:raise ValueError('Geometry override sample outside event '+eid)
        pts=record.get('source_pts');pts_seconds=record.get('source_pts_seconds')
        try:time_base=Fraction(record['source_time_base'])
        except (KeyError,ValueError,TypeError,ZeroDivisionError):raise ValueError('Invalid geometry override time base '+eid)
        if not integer(pts) or pts<0 or time_base<=0 or not finite(pts_seconds) or abs(float(pts*time_base)-pts_seconds)>1e-6:
            raise ValueError('Geometry override PTS mismatch '+eid)
        # Native PTS can differ slightly from CFR time, but cannot be arbitrary.
        if abs(pts_seconds-seconds(sample))>.05:raise ValueError('Geometry override PTS is inconsistent with native sample '+eid)
        box=bbox(record.get('bar_bbox_1080'),'bar bbox '+eid);glyph=bbox(record.get('glyph_bbox_1080'),'glyph bbox '+eid)
        if not (box[0]<=glyph[0]<glyph[2]<=box[2] and box[1]<=glyph[1]<glyph[3]<=box[3]):raise ValueError('Geometry override glyph outside bar '+eid)
        if record.get('glyph_width')!=glyph[2]-glyph[0] or record.get('glyph_height')!=glyph[3]-glyph[1]:raise ValueError('Geometry override glyph dimensions mismatch '+eid)
        if record.get('bar_bbox_status')!='operating_candidate_with_bounds_not_authored_exact':raise ValueError('Geometry override candidate status missing '+eid)
        uncertainty=record.get('glyph_uncertainty_px')
        if not finite(uncertainty) or uncertainty<0:raise ValueError('Geometry override glyph uncertainty missing '+eid)
        bounds=record.get('bar_edge_bounds_1080',{})
        for n,key in enumerate(('left','top','right_exclusive','bottom_exclusive')):
            b=bounds.get(key)
            if not isinstance(b,list) or len(b)!=2 or not all(finite(x) for x in b) or not 0<=b[0]<=box[n]<=b[1]<=(1920 if n%2==0 else 1080):
                raise ValueError('Geometry override edge uncertainty invalid '+eid)
        evidence=record.get('evidence')
        if not isinstance(evidence,list) or not evidence:raise ValueError('Geometry override evidence missing '+eid)
        for item in evidence:
            relative=item.get('path');expected=item.get('sha256')
            if not isinstance(relative,str) or Path(relative).is_absolute():raise ValueError('Geometry evidence must be kit-relative '+eid)
            asset=(kit/relative).resolve()
            if not asset.is_relative_to(kit):raise ValueError('Geometry evidence escapes kit '+eid)
            if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected) or not asset.is_file() or file_sha256(asset)!=expected:
                raise ValueError('Geometry evidence hash mismatch '+eid)
        selected[eid]=copy.deepcopy(record)
    binding={'selection':'explicit_geometry_overrides_cli','manifest_path':str(path),
             'manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),'schema':data['schema'],
             'reference_video_id':A_REFERENCE_ID,'reference_source_sha256':A_REFERENCE_SHA256,
             'caption_timeline_sha256':caption_sha,'caption_timeline_path':str(Path(captions_path).resolve()),
             'reference_file_hash_checked':actual_reference_sha is not None,
             'binding_method':'actual_reference_sha256_and_caption_manifest' if actual_reference_sha else 'caption_manifest_sha256_source_identity_and_packaged_evidence',
             'packaged_evidence_hashes_checked':True,'available_event_ids':list(selected),
             'limits':copy.deepcopy(data.get('limits',[])),
             'scope_note':'One native sample supplies a fixed geometry candidate for its matching event; full-event constancy remains unverified.'}
    return selected,binding,captions

def override_layer_provenance(record,binding,kit,project_root):
    """Preserve observations and uncertainty without upgrading candidate status."""
    import os
    provenance={'status':'explicit_native_observed_geometry_candidate',
                'binding':copy.deepcopy(binding),'measurement':copy.deepcopy(record),
                'evidence_path_base':os.path.relpath(kit,project_root),
                'evidence_path_rule':'Resolve measurement.evidence paths relative to evidence_path_base.'}
    for key in ('manifest_path','caption_timeline_path'):
        provenance['binding'][key]=os.path.relpath(binding[key],project_root)
    return provenance
def boundary(e,side):
    return seconds(e[side+'_frame']) if e.get(side+'_frame') is not None else float(e[side+'_seconds'])
def recovered_layers(e,start,end,kit):
    """Compile finite observed graphics, preserving independent measured fades."""
    layers=[];media={}
    for i,c in enumerate(e['render_hints']['recovered_components']):
        lo=max(start,seconds(c['in_frame']));hi=min(end,seconds(c['out_frame']))
        if hi<=lo:continue
        asset=kit/c['asset'];expected=c.get('sha256')
        import hashlib
        if expected and hashlib.sha256(asset.read_bytes()).hexdigest()!=expected:raise ValueError('Recovered asset hash mismatch '+str(asset))
        x,y,x2,y2=c['asset_bbox_1080'];mid=e['id']+'-recovered-'+str(i+1)
        media[mid]={'path':str(asset),'type':'image','role':'reference_derived_graphic_asset','supports_new_text':False,'source_asset_sha256':expected}
        layers.append({'id':mid,'semantic_layer_id':e['id'],'kind':'image','source':mid,'position':[x,y],'size':[x2-x,y2-y],'in':lo-start,'out':hi-start,'z':20+i,'opacity_keys':[[seconds(s['frame'])-lo,s['alpha'],'linear'] for s in c['opacity']['samples']],'status':'observed_finite_graphic_candidate_alpha','source_reference_frames':[c['in_frame'],c['out_frame']],'text':e['text'],'rotation_already_baked_into_asset':True})
    return layers,media
def glyph_measurement(arr,e):
    if e['layer']=='dialogue':
        ys=list(range(941,948))+list(range(1008,1015))
        # Median across text-free strips tolerates isolated compression/chroma
        # ringing near a bright hand. The old 5/6 test truncated f39455's bar.
        stable=np.median(np.max(np.abs(arr[ys].astype(float)-17),axis=2),axis=0)<18
        stable=cv2.morphologyEx(stable.astype('uint8')[None,:],cv2.MORPH_CLOSE,np.ones((1,3),np.uint8))[0]>0
        # Find the black bar containing canvas center, across multiple text-free rows.
        left=right=960
        while left>0 and stable[left-1]:left-=1
        while right<1919 and stable[right+1]:right+=1
        if right-left<40:raise ValueError('Caption bar could not be measured '+e['id'])
        observed=e.get('bar_bbox_1080')
        if observed and abs((right-left+1)-(observed[2]-observed[0]))>max(12,.05*(observed[2]-observed[0])):
            raise ValueError('Caption bar measurement conflicts with reviewed bounds '+e['id'])
        box=[left,938,right+1,1018]
        c=arr[938:1018,left:right+1]
        mask=(c[:,:,0]>200)&(c[:,:,1]>200)&(c[:,:,2]>150)
    else:
        box=list(map(round,e['bbox_1080']));c=arr[box[1]:box[3],box[0]:box[2]]
        mask=(c[:,:,0]>225)&(c[:,:,1]>225)&(c[:,:,2]>170)
    yy,xx=np.where(mask)
    if not len(xx):raise ValueError('Empty glyph mask '+e['id'])
    return box,[int(xx.min()),int(yy.min())],int(xx.max()-xx.min()+1),int(yy.max()-yy.min()+1)

def observed_fill(e,document,kit,project_root):
    """Explicit per-event RGB wins; only measured A semantic roles may fall back."""
    hints=e['render_hints'];value=hints.get('fill_observed_rgb')
    if value is not None:
        if not isinstance(value,list) or len(value)!=3 or any(type(v) is not int or not 0<=v<=255 for v in value):
            raise ValueError('Invalid observed RGB '+e['id'])
        return value,{'method':'event_explicit_observed_rgb','status':'decoded_output_not_authored_rgba'}
    roles={('white','handwritten_monoline'):'handwritten-white',('white','very_bold'):'bold-conclusion-white'}
    role=roles.get((hints.get('fill_role'),hints.get('weight_role')))
    if role is None:raise ValueError('Unsupported measured fill role '+e['id'])
    if document.get('video_id')!=A_REFERENCE_ID or document.get('source_sha256')!=A_REFERENCE_SHA256:
        raise ValueError('Measured fill role requires reference A source identity')
    path=Path(kit)/'evidence/A-palette-measurements.json';palette=read(path)
    matches=[(i,r) for i,r in enumerate(palette['measurements']) if r.get('video')=='A' and r.get('role')==role]
    if len(matches)!=1:raise ValueError('Ambiguous/missing measured palette role '+role)
    i,record=matches[0];value=record['rgb_mode']
    if len(value)!=3 or any(type(v) is not int or not 0<=v<=255 for v in value):raise ValueError('Invalid palette RGB '+role)
    import os
    return value,{'method':'semantic_role_observed_palette_candidate','status':'decoded_output_not_authored_rgba',
                  'source_path':os.path.relpath(path,project_root),'source_sha256':file_sha256(path),
                  'record_index':i,'record':copy.deepcopy(record),'reference_source_sha256':A_REFERENCE_SHA256,
                  'scope':'Representative role measurement reused for this event; not a fresh event color measurement.'}

def graphic_base(e,lo,hi,start,fill,color_evidence):
    hints=e['render_hints'];role='body' if hints.get('weight_role')=='regular_sans' else 'hand' if hints.get('weight_role')=='handwritten_monoline' or e['layer']=='handwritten' else 'bold'
    layer={'id':e['id'],'semantic_layer_id':e['id'],'kind':'caption' if role=='body' else 'text',
           'in':lo-start,'out':hi-start,'z':10 if role=='body' else 20,'text':e['text'],'font':role,
           'font_size':100 if role=='body' else 120,'tracking':-2 if role=='body' else -5 if role=='bold' else 0,
           'fill':fill,'fill_provenance':color_evidence,'status':'reviewed_text_candidate_font',
           'text_verification':e['text_verification'],'source_reference_frames':[e.get('in_frame'),e.get('out_frame')]}
    apply_observed_opacity(layer,hints,lo)
    return layer

def apply_observed_opacity(layer,hints,lo):
    opacity=hints.get('opacity',{})
    if not isinstance(opacity,dict):raise ValueError('Invalid opacity record '+layer['id'])
    if opacity.get('kind')=='observed_per_frame_lut':
        samples=opacity.get('samples',[])
        if not samples or any(type(s.get('frame')) is not int or not isinstance(s.get('alpha'),(int,float)) or not math.isfinite(s['alpha']) or not 0<=s['alpha']<=1 for s in samples):raise ValueError('Invalid observed opacity samples '+layer['id'])
        if any(a['frame']>=b['frame'] for a,b in zip(samples,samples[1:])):raise ValueError('Unordered observed opacity samples '+layer['id'])
        layer['opacity_keys']=[[seconds(s['frame'])-lo,s['alpha'],'linear'] for s in opacity['samples']]
        layer['opacity_evidence']='Decoded estimate; not original authored keyframes'
    elif opacity.get('kind')=='constant':
        value=opacity.get('value')
        if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:raise ValueError('Invalid constant opacity '+layer['id'])
        layer['opacity']=value
    elif opacity:raise ValueError('Unsupported opacity kind '+str(opacity.get('kind'))+' '+layer['id'])

def graphic_rotation(hints):
    if 'rotation_degrees_clockwise' in hints and 'rotation_degrees_clockwise_approx' in hints and hints['rotation_degrees_clockwise']!=hints['rotation_degrees_clockwise_approx']:
        raise ValueError('Conflicting clockwise rotation fields')
    return hints.get('rotation_degrees_clockwise',hints.get('rotation_degrees_clockwise_approx',0))

def checked_bbox(value,label):
    if not isinstance(value,list) or len(value)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in value):raise ValueError('Invalid '+label)
    x,y,x2,y2=value
    if not 0<=x<x2<=1920 or not 0<=y<y2<=1080:raise ValueError('Out-of-canvas '+label)
    return list(value)

def multiline_graphic_layers(e,lo,hi,start,fill,color_evidence):
    """Keep each reviewed row's own geometry; never stretch both rows equally."""
    hints=e['render_hints'];lines=e['text'].split('\n');spans=e.get('spans',[])
    if len(lines)!=len(spans) or [s.get('text') for s in spans]!=lines or any(not line for line in lines):
        raise ValueError('Multiline spans must exactly match reviewed text '+e['id'])
    if hints.get('background_role') or graphic_rotation(hints):
        raise ValueError('Unsupported multiline background/rotation '+e['id'])
    layers=[]
    for i,span in enumerate(spans):
        if span.get('render_hints') or span.get('fill_role') or span.get('fill_observed_rgb'):
            raise ValueError('Unsupported per-span style '+e['id'])
        box=checked_bbox(span.get('bbox_1080'),'multiline row bbox '+e['id'])
        if i and box[1]<spans[i-1]['bbox_1080'][3]:raise ValueError('Overlapping/unordered multiline rows '+e['id'])
        layer=graphic_base(e,lo,hi,start,fill,color_evidence)
        layer.update(id=e['id']+'-line-'+str(i+1),text=lines[i],position=box[:2],glyph_width=round(box[2]-box[0]),glyph_height=round(box[3]-box[1]),padding=0)
        layer['geometry_provenance']={'method':'independent_reviewed_span_bbox_candidate','line_index':i,'line_count':len(lines),
                                      'observed_bbox_1080':box,'source_bbox_status':e.get('bbox_measurement'),
                                      'limits':'OCR row bounds are approximate; fitted candidate glyph shape is not the original font.'}
        layers.append(layer)
    return layers

def load_graphic_span_overrides(path,graphics_path,kit,project_root):
    """Opt in to finite, evidence-bound mixed-row measurements for reference A.

    The original graphic document stays unchanged. These measured row boxes and
    decoded RGBs are candidates, not authored geometry or identified fonts.
    """
    document=read(graphics_path)
    if not path:return document,None
    import os
    path=Path(path).resolve();kit=Path(kit).resolve();data=read(path)
    if data.get('schema')!='atrass-graphic-span-overrides-v1':raise ValueError('Unsupported graphic span override schema')
    if document.get('video_id')!=A_REFERENCE_ID or document.get('source_sha256')!=A_REFERENCE_SHA256 or data.get('source_sha256')!=A_REFERENCE_SHA256:
        raise ValueError('Graphic span override reference source mismatch')
    if data.get('input_graphics_sha256')!=file_sha256(graphics_path):raise ValueError('Graphic span override input SHA mismatch')
    if data.get('coordinate_reference')!=[1920,1080] or data.get('bbox_convention')!='[left,top,right_exclusive,bottom_exclusive]':raise ValueError('Graphic span override coordinate convention mismatch')
    records=data.get('events');events=document.get('events',[]);ids=[e['id'] for e in events]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate input graphic ID')
    if not isinstance(records,list) or not records:raise ValueError('Empty graphic span overrides')
    by_id={e['id']:e for e in events};selected=[]
    binding={'manifest_path':os.path.relpath(path,project_root),'manifest_sha256':file_sha256(path),
             'input_graphics_path':os.path.relpath(Path(graphics_path).resolve(),project_root),
             'input_graphics_sha256':file_sha256(graphics_path),'reference_source_sha256':A_REFERENCE_SHA256,
             'binding_method':'exact_graphics_manifest_source_identity_and_evidence_hashes',
             'actual_reference_file_hash_checked_here':False,'status':'observed_candidate_not_authored_exact'}
    for record in records:
        eid=record.get('id');event=by_id.get(eid)
        if eid in selected:raise ValueError('Duplicate graphic span override '+str(eid))
        if not event or event.get('render_enabled') is not True or event.get('render_hints',{}).get('weight_role')!='mixed_per_span':raise ValueError('Graphic span override must target reviewed mixed_per_span event '+str(eid))
        if record.get('text')!=event['text'] or record.get('event_frames')!=[event.get('in_frame'),event.get('out_frame')]:raise ValueError('Graphic span override text/frame mismatch '+eid)
        sample=record.get('sample_frame');pts=record.get('source_pts')
        if record.get('source_time_base')!='1/1000' or type(sample) is not int or not event['in_frame']<=sample<event['out_frame'] or type(pts) is not int or abs(pts/1000-seconds(sample))>.0011:raise ValueError('Graphic span sample/PTS mismatch '+eid)
        if record.get('status')!='native_observed_candidate_not_authored_exact' or not record.get('limits'):raise ValueError('Graphic span uncertainty/status missing '+eid)
        spans=record.get('spans',[]);lines=event['text'].split('\n')
        if len(spans)!=len(lines) or [s.get('text') for s in spans]!=lines:raise ValueError('Graphic span text rows mismatch '+eid)
        for i,span in enumerate(spans):
            if set(span)-{'text','bbox_1080','weight_role','fill_observed_rgb','measurement_method','uncertainty'}:raise ValueError('Unsupported graphic span measurement fields '+eid)
            box=checked_bbox(span.get('bbox_1080'),'graphic span bbox '+eid)
            if i and box[1]<spans[i-1]['bbox_1080'][3]:raise ValueError('Overlapping graphic span rows '+eid)
            if span.get('weight_role') not in ('regular_sans','bold_sans','very_bold','handwritten_monoline'):raise ValueError('Unsupported graphic span weight '+eid)
            rgb=span.get('fill_observed_rgb')
            if not isinstance(rgb,list) or len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb):raise ValueError('Invalid graphic span RGB '+eid)
            if not span.get('measurement_method') or not span.get('uncertainty'):raise ValueError('Graphic span measurement limits missing '+eid)
        evidence=record.get('evidence')
        if not isinstance(evidence,list) or not evidence:raise ValueError('Missing graphic span evidence '+eid)
        for item in evidence:
            relative=item.get('path');expected=item.get('sha256')
            if not isinstance(relative,str) or Path(relative).is_absolute():raise ValueError('Graphic span evidence must be kit-relative '+eid)
            asset=(kit/relative).resolve()
            if not asset.is_relative_to(kit) or not asset.is_file() or file_sha256(asset)!=expected:raise ValueError('Graphic span evidence hash/path mismatch '+eid)
        event['span_style_override']={'binding':copy.deepcopy(binding),'measurement':copy.deepcopy(record),
                                     'evidence_path_base':os.path.relpath(kit,project_root)}
        event['spans']=[{'text':s['text'],'bbox_1080':s['bbox_1080'],'render_hints':{'weight_role':s['weight_role'],'fill_observed_rgb':s['fill_observed_rgb']}} for s in spans]
        selected.append(eid)
    binding['applied_event_ids']=selected
    return document,binding

def mixed_span_graphic_layers(e,lo,hi,start):
    """Compile only explicitly bound, independently measured row colors/styles."""
    proof=e.get('span_style_override');hints=e['render_hints']
    if not proof or hints.get('weight_role')!='mixed_per_span':raise ValueError('Mixed span graphics require explicit bound overrides '+e['id'])
    permitted={'font_em_size_px','exact_font','authored_rgba','color_status','anchor','text_origin_1080','transform','weight_role','glyph_height_px','opacity','background_role','rotation_degrees_clockwise','rotation_degrees_clockwise_approx'}
    if set(hints)-permitted:raise ValueError('Unsupported mixed span parent fields '+e['id'])
    if hints.get('anchor') not in (None,'observed_bbox_left'):raise ValueError('Unsupported mixed span parent anchor '+e['id'])
    if hints.get('background_role') or graphic_rotation(hints):raise ValueError('Unsupported mixed span background/rotation '+e['id'])
    lines=e['text'].split('\n');spans=e['spans']
    if len(lines)!=len(spans) or [s['text'] for s in spans]!=lines:raise ValueError('Mixed span text mismatch '+e['id'])
    layers=[]
    for i,span in enumerate(spans):
        box=checked_bbox(span['bbox_1080'],'mixed span row '+e['id'])
        if i and box[1]<spans[i-1]['bbox_1080'][3]:raise ValueError('Overlapping mixed span rows '+e['id'])
        local=copy.deepcopy(e);local['render_hints']={**hints,**span['render_hints']}
        fill=span['render_hints']['fill_observed_rgb']
        layer=graphic_base(local,lo,hi,start,fill,{'method':'explicit_hash_bound_per_span_decoded_rgb','status':'decoded_output_not_authored_rgba','span_index':i,'binding':copy.deepcopy(proof['binding'])})
        layer.update(id=e['id']+'-line-'+str(i+1),text=span['text'],position=box[:2],glyph_width=round(box[2]-box[0]),glyph_height=round(box[3]-box[1]),padding=0)
        layer['geometry_provenance']={'method':'native_observed_row_bbox_candidate','observed_bbox_1080':box,'line_index':i,'line_count':len(lines),'limits':'Candidate font fitted per measured row; original face, em size and tracking remain unknown.'}
        layer['span_style_override']=copy.deepcopy(proof);layers.append(layer)
    return layers

def rotated_graphic_layers(e,lo,hi,start,fill,color_evidence,arr=None,sample=None):
    """Separate quad-derived backplate and ink, each around its own center pivot.

    Composer rotates clockwise with expansion, then applies anchor=center. Using
    position (not box) avoids confusing screen bounds with an expanded canvas.
    """
    hints=e['render_hints'];angle=graphic_rotation(hints)
    if not isinstance(angle,(int,float)) or not math.isfinite(angle) or not 0<abs(angle)<10:raise ValueError('Unsupported rotation angle '+e['id'])
    opacity=hints.get('opacity',{})
    # Splitting overlapping ink/plate does not preserve a parent's group fade.
    # Existing recovered_components have independent measured alpha elsewhere.
    if opacity and opacity!={'kind':'constant','value':1}:
        raise ValueError('Rotated compound opacity requires independent component measurements '+e['id'])
    if hints.get('background_role')!='black_rectangle' or hints.get('background_observed_rgb')!=[0,0,0]:
        raise ValueError('Rotated graphic requires measured black backplate '+e['id'])
    q=np.asarray(e.get('bar_quad_1080_approx'),dtype=float)
    if q.shape!=(4,2) or not np.isfinite(q).all():raise ValueError('Rotated graphic requires measured quad '+e['id'])
    if np.any(q<0) or np.any(q[:,0]>1920) or np.any(q[:,1]>1080):raise ValueError('Out-of-canvas rotated quad '+e['id'])
    theta=math.radians(angle);u=np.array([math.cos(theta),math.sin(theta)]);v=np.array([-math.sin(theta),math.cos(theta)])
    center=q.mean(axis=0);w=float((np.linalg.norm(q[1]-q[0])+np.linalg.norm(q[2]-q[3]))/2);h=float((np.linalg.norm(q[3]-q[0])+np.linalg.norm(q[2]-q[1]))/2)
    predicted=np.array([center-u*w/2-v*h/2,center+u*w/2-v*h/2,center+u*w/2+v*h/2,center-u*w/2+v*h/2])
    error=float(np.max(np.linalg.norm(q-predicted,axis=1)))
    if min(w,h)<=0 or error>2:raise ValueError('Quad conflicts with clockwise rotation '+e['id'])
    observed=checked_bbox(e.get('bbox_1080'),'rotated ink bbox '+e['id'])
    # The existing OCR ink rectangle includes a few pixels of plate/AA. Allow
    # that recorded uncertainty, but reject a disconnected text/plate pair.
    tolerance=8
    if observed[0]<q[:,0].min()-tolerance or observed[1]<q[:,1].min()-tolerance or observed[2]>q[:,0].max()+tolerance or observed[3]>q[:,1].max()+tolerance:
        raise ValueError('Observed ink bbox conflicts with rotated quad '+e['id'])
    if arr is not None:
        if fill!=[255,255,255]:raise ValueError('Rotated native measurement supports observed white only '+e['id'])
        # Resample only the measured plate. White pixels outside it are not ink.
        ww,hh=round(w),round(h)
        matrix=np.array([[u[0],u[1],ww/2-np.dot(u,center)],[v[0],v[1],hh/2-np.dot(v,center)]])
        crop=cv2.warpAffine(arr,matrix,(ww,hh),flags=cv2.INTER_LINEAR)
        yy,xx=np.where(np.min(crop,axis=2)>225)
        if not len(xx):raise ValueError('Empty inverse-rotated white glyph mask '+e['id'])
        if xx.min()==0 or yy.min()==0 or xx.max()==ww-1 or yy.max()==hh-1:raise ValueError('Inverse-rotated glyph mask touches plate crop boundary '+e['id'])
        gw,gh=int(xx.max()-xx.min()+1),int(yy.max()-yy.min()+1)
        glyph_center=center+u*((xx.min()+xx.max()+1)/2-ww/2)+v*((yy.min()+yy.max()+1)/2-hh/2)
        method='reference_white_mask_after_inverse_rotation_inside_observed_quad'
    else:
        # This solves only the rectangle AABB, not the exact contours of glyphs.
        aw,ah=observed[2]-observed[0],observed[3]-observed[1];c=abs(u[0]);s=abs(u[1]);det=c*c-s*s
        gw,gh=round((aw*c-ah*s)/det),round((ah*c-aw*s)/det)
        glyph_center=np.array([(observed[0]+observed[2])/2,(observed[1]+observed[3])/2]);method='inverse_rotated_ocr_aabb_candidate_no_reference_measurement'
    if min(gw,gh)<=0:raise ValueError('Invalid inverse-rotated glyph size '+e['id'])
    delta=glyph_center-center
    overflow=[max(0,float(abs(np.dot(delta,u))+gw/2-w/2)),max(0,float(abs(np.dot(delta,v))+gh/2-h/2))]
    if max(overflow)>tolerance:
        raise ValueError('Unrotated ink exceeds measured plate tolerance '+e['id'])
    provenance={'method':method,'observed_quad_1080_approx':q.tolist(),'quad_fit_max_error_px':error,
                'observed_rotated_ink_bbox_1080':observed,'plate_center_1080':center.tolist(),'plate_size_unrotated':[w,h],
                'ink_center_1080':glyph_center.tolist(),'ink_size_unrotated':[gw,gh],'clockwise_degrees':angle,
                'validation_guard_tolerance_px':tolerance,'tolerance_policy':'Implementation sanity threshold, not a measured error interval.',
                'ink_box_overflow_beyond_plate_xy_px':overflow,
                'ink_box_contained_within_plate':not any(overflow),
                'geometry_warning':'OCR fallback can extend beyond plate; use --reference for native inverse-rotation measurement.' if any(overflow) else None,
                'sample':sample,'anchor_rule':'Separate center pivots; positive clockwise; expansion precedes center placement.',
                'status':'observed_geometry_candidate_not_authored_transform','limits':'Raster threshold, quad fit and candidate font introduce error; no exact authored geometry or font claim.'}
    text=graphic_base(e,lo,hi,start,fill,color_evidence)
    text.update(position=glyph_center.tolist(),anchor='center',rotation=angle,glyph_width=gw,glyph_height=gh,padding=0,z=21,geometry_provenance=provenance)
    plate={'id':e['id']+'-backplate','semantic_layer_id':e['id'],'kind':'rect','size':[w,h],
           'position':center.tolist(),'anchor':'center','rotation':angle,'fill':hints['background_observed_rgb'],
           'in':lo-start,'out':hi-start,'z':20,'status':'observed_geometry_candidate_not_authored_transform',
           'geometry_provenance':copy.deepcopy(provenance),'source_reference_frames':[e.get('in_frame'),e.get('out_frame')],
           'fill_provenance':{'method':'event_explicit_background_observed_rgb','status':'decoded_output_not_authored_rgba'}}
    apply_observed_opacity(plate,hints,lo)
    return [plate,text]

def build(a):
    kit=Path(__file__).resolve().parents[1];root=Path(a.output).resolve().parent
    start,end=seconds(a.start_frame),seconds(a.end_frame)
    if end<=start:raise ValueError('Invalid frame range')
    geometry_overrides,geometry_binding,caption_data=load_geometry_overrides(getattr(a,'geometry_overrides',None),a.captions,a.reference,kit)
    maps=read(a.video_map);clips=[]
    for c in maps['clips']:
        ci=seconds(round(c['output_in_seconds']*float(FPS)));co=seconds(round(c['output_out_seconds']*float(FPS)))
        lo,hi=max(ci,start),min(co,end)
        if hi<=lo:continue
        clip={'id':c['id'],'in':lo-start,'out':hi-start,'source':'rawA','source_in':lo+c['offset_seconds'],'geometry':c['geometry'],'status':c['status'],'mapping_evidence':str(Path(a.video_map).resolve()),'native_output_start_frame':round(lo*float(FPS))}
        if c.get('output_native_frame_indices') is not None:
            tb=c['source_frame_time_base'];tb=Fraction(tb['num'],tb['den']) if isinstance(tb,dict) else Fraction(tb)
            times=c.get('render_source_frame_times')
            if times is not None:
                if len(times)!=len(c['output_native_frame_indices']):raise ValueError('Render frame map length mismatch '+c['id'])
                frame_map=dict(zip(c['output_native_frame_indices'],map(float,times)))
                clip['frame_mapping_selection']='monotonic_render_candidate'
            else:
                frame_map={i:float(Fraction(pts,1)*tb) for i,pts in zip(c['output_native_frame_indices'],c['source_frame_pts'])}
                clip['frame_mapping_selection']='independent_best_candidate'
            clip['source_frame_times']=[frame_map[i] for i in range(round(lo*float(FPS)),round(hi*float(FPS)))];clip['source_in']=clip['source_frame_times'][0];clip['frame_mapping_status']=c['frame_mapping_status']
        clips.append(clip)
    fonts={
      'body':{'path':str(kit/'assets/fonts/NotoSansKR[wght].ttf'),'weight':450,'status':'render_candidate_not_original_confirmed'},
      'bold':{'path':str(kit/'assets/fonts/SUIT-Variable.ttf'),'weight':800,'status':'render_candidate_not_original_confirmed'},
      'hand':{'path':str(Path(a.hand_font).resolve()),'status':'render_candidate_not_original_confirmed'},
    }
    graphics_data,span_binding=load_graphic_span_overrides(getattr(a,'graphic_span_overrides',None),a.graphics,kit,root)
    layers=[];omitted=[];aux_media={};reader=SourceReader(a.reference) if a.reference else None
    try:
        for document,is_caption_document in ((caption_data,True),(graphics_data,False)):
            for e in document['events']:
                lo,hi=max(boundary(e,'in'),start),min(boundary(e,'out'),end)
                if hi<=lo:continue
                if not e.get('render_enabled',False):
                    omitted.append({'id':e['id'],'reason':'not_reviewed_for_render','range':[lo,hi]});continue
                if e['render_hints'].get('recovered_components'):
                    recovered,assets=recovered_layers(e,start,end,kit);layers.extend(recovered);aux_media.update(assets);continue
                hints=e['render_hints']
                if hints.get('weight_role')=='mixed_per_span':
                    layers.extend(mixed_span_graphic_layers(e,lo,hi,start));continue
                fill,color_evidence=observed_fill(e,document,kit,root)
                if '\n' in e['text']:
                    layers.extend(multiline_graphic_layers(e,lo,hi,start,fill,color_evidence));continue
                if graphic_rotation(hints):
                    arr=chosen=None
                    if reader:arr,chosen=reader.get(min(hi-1/float(FPS),lo+.2))
                    layers.extend(rotated_graphic_layers(e,lo,hi,start,fill,color_evidence,arr,chosen));continue
                override=geometry_overrides.get(e['id']) if is_caption_document else None
                if override is not None:
                    box=list(override['bar_bbox_1080']);gx,gy,gx2,gy2=override['glyph_bbox_1080'];offset=[gx-box[0],gy-box[1]];gw=gx2-gx;gh=gy2-gy
                elif hints.get('background_role')=='black_rectangle' and e.get('bar_bbox_1080'):
                    box=list(map(round,e['bar_bbox_1080']));gx,gy,gx2,gy2=map(round,e['bbox_1080']);offset=[gx-box[0],gy-box[1]];gw=gx2-gx;gh=gy2-gy
                elif reader:
                    arr,chosen=reader.get(min(hi-1/float(FPS),lo+.2));box,offset,gw,gh=glyph_measurement(arr,e)
                else:
                    box=list(map(round,e.get('bar_bbox_1080') or e['bbox_1080']));bbox=e['bbox_1080'];gw=round(bbox[2]-bbox[0]);gh=hints.get('glyph_height_px',hints.get('glyph_height_px_observed_bbox',bbox[3]-bbox[1]));offset=[round(bbox[0]-box[0]),round(bbox[1]-box[1])]
                role='body' if e['layer']=='dialogue' or hints.get('weight_role')=='regular_sans' else 'hand' if e['layer']=='handwritten' or hints.get('weight_role')=='handwritten_monoline' else 'bold'
                layer={'id':e['id'],'kind':'caption' if role=='body' else 'text','in':lo-start,'out':hi-start,'z':10 if role=='body' else 20,'text':e['text'],'font':role,'font_size':100 if role=='body' else 120,'tracking':-2 if role=='body' else -5 if role=='bold' else 0,'glyph_width':gw,'glyph_height':gh,'box':box,'text_offset':offset,'fill':fill,'fill_provenance':color_evidence,'status':'reviewed_text_candidate_font','text_verification':e['text_verification'],'source_reference_frames':[e.get('in_frame'),e.get('out_frame')]}
                if override is not None:layer['geometry_override']=override_layer_provenance(override,geometry_binding,kit,root)
                if e['layer']=='dialogue':layer['background']='#111111'
                elif hints.get('background_role')=='black_rectangle':layer['background']=hints['background_observed_rgb']
                apply_observed_opacity(layer,hints,lo)
                layers.append(layer)
    finally:
        if reader:reader.close()
    audio=[];audio_issues=[]
    for s in read(a.audio_map)['segments']:
        lo,hi=max(s['final_in'],start),min(s['final_out'],end)
        if hi<=lo:continue
        # Retain uncertain intervals explicitly. No silent semantic edit is made.
        audio.append({'id':'audio-'+str(s['id']),'in':lo-start,'out':hi-start,'source':'rawA','source_in':lo+s['offset_seconds'],'gain_db':-.567066,'status':'waveform_mapping_candidate_needs_listening','correlation_median':s['correlation_median']})
        if s['correlation_median']<.8:audio_issues.append({'segment':s['id'],'range':[lo,hi],'correlation':s['correlation_median']})
    p={'schema':'atrass-render-v1','title':'애교이론 원본 재현 — 프레임 %d–%d'%(a.start_frame,a.end_frame),'variant':'A','width':1920,'height':1080,'fps':str(FPS),'duration':end-start,'release_ready':False,'reference_range_frames':[a.start_frame,a.end_frame],'reference_range_seconds':[start,end],'media':{'rawA':{'path':str(Path(a.raw).resolve()),'role':'source_footage'}},'fonts':fonts,'clips':clips,'layers':layers,'audio':audio,'audio_master':{'sample_peak_ceiling_dbfs':-1.5},'omitted_events':omitted,'audio_review_required':audio_issues,'limitations':['Candidate font shapes remain different from the reference.','Base footage and speech audio use raw input; any explicitly identified reference-derived graphic assets are finite recovered rasters, not original source artwork.','Audio boundaries require phoneme/listening review; low-correlation intervals are explicitly retained for audit.','Sample-peak guard is not a true-peak guarantee.']}
    # Use relative font/evidence paths where they remain inside the transferred kit.
    import os
    if span_binding is not None:p['graphic_span_override_selection']=span_binding
    if geometry_binding is not None:
        p['geometry_override_selection']=copy.deepcopy(geometry_binding)
        for key in ('manifest_path','caption_timeline_path'):p['geometry_override_selection'][key]=os.path.relpath(geometry_binding[key],root)
        p['geometry_override_selection']['applied_event_ids']=[e['id'] for e in layers if 'geometry_override' in e]
        p['limitations'].append('Explicit geometry overrides retain native edge uncertainty and are not authored coordinates or whole-event geometry certification.')
    p['media'].update(aux_media)
    for m in aux_media.values():
        m['path']=os.path.relpath(m['path'],root)
    for f in p['fonts'].values():
        if Path(f['path']).is_relative_to(kit):f['path']=os.path.relpath(f['path'],root)
    for c in p['clips']:
        if Path(c['mapping_evidence']).is_relative_to(kit):c['mapping_evidence']=os.path.relpath(c['mapping_evidence'],root)
    validate(p,root,True)
    out=Path(a.output)
    if out.exists():raise ValueError('Project exists; choose a new filename')
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output':str(out),'clips':len(clips),'layers':len(layers),'audio_segments':len(audio),'omitted_events':omitted,'audio_review_required':audio_issues},ensure_ascii=False))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for n in ['raw','video-map','audio-map','captions','graphics','hand-font','output']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--reference',help='Optional finished video, used only to measure caption geometry')
    ap.add_argument('--geometry-overrides',help='Explicit A geometry candidate manifest. Checks exact caption JSON/source identity and evidence SHA; also checks actual reference SHA when --reference is supplied. Never auto-loaded.')
    ap.add_argument('--graphic-span-overrides',help='Explicit A mixed-row geometry/color candidates bound to exact graphics JSON and packaged evidence SHA. Never auto-loaded; original typefaces remain unconfirmed.')
    ap.add_argument('--start-frame',type=int,required=True);ap.add_argument('--end-frame',type=int,required=True)
    try:build(ap.parse_args())
    except Exception as e:print(str(e),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
