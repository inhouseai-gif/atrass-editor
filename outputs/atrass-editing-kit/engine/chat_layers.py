#!/usr/bin/env python3
"""Compile measured chat components into independent, editable timeline layers.

Each layout state is discrete: existing messages retain identity while moving
when a later message arrives. Multiline text uses measured per-line geometry.
Missing assets or line geometry are errors. Shadows and fonts remain candidates.
"""
from fractions import Fraction
from pathlib import Path
import argparse,json,os
from render import validate

def validate_composite(c):
    """Reject inconsistent source evidence rather than silently clip it away."""
    fr=c['frame_rate']
    if type(fr.get('num')) is not int or type(fr.get('den')) is not int or fr['num']<=0 or fr['den']<=0:
        raise ValueError('Invalid source frame rate')
    def span(v):
        lo,hi=v.get('in_frame'),v.get('out_frame')
        if type(lo) is not int or type(hi) is not int or not 0<=lo<hi:
            raise ValueError('Invalid native frame interval: '+str(v.get('id','state')))
        return lo,hi
    lo,hi=span(c)
    def chain(states,start,end,label):
        previous=start
        if not states:raise ValueError('Missing layout states: '+label)
        for s in states:
            a,b=span(s)
            if a!=previous or b>end:raise ValueError('Layout gap/overlap/outside parent: '+label)
            previous=b
            for key,value in s.items():
                if key.endswith('bbox_1080'):
                    if len(value)!=4 or not (0<=value[0]<value[2]<=1920 and 0<=value[1]<value[3]<=1080):
                        raise ValueError('Invalid measured bounds: '+label+' '+key)
        if previous!=end:raise ValueError('Incomplete layout lifetime: '+label)
    states=c['layout_states'];chain(states,lo,hi,c['id'])
    stage_spans=[span(s) for s in states];identities={c['id']}
    for name in ('panel','sender','avatar'):
        v=c[name]
        if span(v)!=(lo,hi):raise ValueError('Component lifetime differs from parent: '+name)
        if v['id'] in identities:raise ValueError('Duplicate component ID: '+v['id'])
        identities.add(v['id']);chain(v['layout_states'],lo,hi,v['id'])
        if [span(s) for s in v['layout_states']]!=stage_spans:raise ValueError('Component stages differ from parent: '+name)
        for parent,s in zip(states,v['layout_states']):
            if span(parent[name])!=span(parent):raise ValueError('Mirrored component stage interval differs: '+name)
            for key,value in s.items():
                if key.endswith('bbox_1080') and parent[name].get(key)!=value:
                    raise ValueError('Conflicting parent/component geometry: '+v['id'])
    for m in c['messages']:
        if m['id'] in identities:raise ValueError('Duplicate component ID: '+m['id'])
        identities.add(m['id']);a,b=span(m)
        if m.get('lines') is not None and m['lines']!=m['text'].split('\n'):raise ValueError('Message lines differ from text: '+m['id'])
        if not lo<=a<b<=hi:raise ValueError('Message outside parent: '+m['id'])
        chain(m['layout_states'],a,b,m['id'])
        for s in m['layout_states']:
            bx,by,bx2,by2=s['bubble_bbox_1080'];gx,gy,gx2,gy2=s['text_glyph_bbox_1080']
            if not (bx<=gx<gx2<=bx2 and by<=gy<gy2<=by2):raise ValueError('Message glyph bounds outside bubble: '+m['id'])
            lines=m['text'].split('\n');boxes=s.get('text_line_glyph_bboxes_1080')
            if len(lines)>1 and boxes is None:raise ValueError('Multiline chat requires measured per-line glyph boxes: '+m['id'])
            if boxes is not None:
                if len(boxes)!=len(lines):raise ValueError('Line text/geometry count mismatch: '+m['id'])
                previous_y=-1
                for lx,ly,lx2,ly2 in boxes:
                    if not (bx<=lx<lx2<=bx2 and by<=ly<ly2<=by2 and ly>=previous_y):raise ValueError('Invalid per-line geometry: '+m['id'])
                    previous_y=ly2
        expected=[(max(a,x),min(b,y)) for x,y in stage_spans if max(a,x)<min(b,y)]
        if [span(s) for s in m['layout_states']]!=expected:raise ValueError('Message stages differ from parent: '+m['id'])
    for state in states:
        active={m['id']:m for m in c['messages'] if m['in_frame']<=state['in_frame']<m['out_frame']}
        rows=state['messages'];ids=[r['message_id'] for r in rows]
        if len(ids)!=len(set(ids)) or set(ids)!=set(active):raise ValueError('Parent stage message membership differs')
        for row in rows:
            msg=active[row['message_id']]
            if span(row)!=(max(msg['in_frame'],state['in_frame']),min(msg['out_frame'],state['out_frame'])):
                raise ValueError('Message row belongs to a different parent stage: '+row['message_id'])
            source=next(s for s in active[row['message_id']]['layout_states'] if span(s)==span(row))
            if any(source.get(k)!=v for k,v in row.items() if k.endswith('bbox_1080')):
                raise ValueError('Conflicting message geometry: '+row['message_id'])
            if source.get('text_line_glyph_bboxes_1080')!=row.get('text_line_glyph_bboxes_1080'):
                raise ValueError('Conflicting per-line message geometry: '+row['message_id'])
    if c.get('adjacent_photo_asset'):
        photo=c['adjacent_photo_asset'];a,b=span(photo)
        if not lo<=a<b<=hi:raise ValueError('Adjacent photo outside chat range')
        x,y,x2,y2=photo['bbox_1080']
        if not (0<=x<x2<=1920 and 0<=y<y2<=1080):raise ValueError('Invalid adjacent photo bounds')

def compile_chat(composite,origin_frame,end_frame,avatar_source,font='chat',photo_source=None):
    c=composite
    if c.get('coordinate_space')!=[1920,1080]:raise ValueError('Expected measured 1920x1080 coordinates')
    if not c.get('render_ready_structure'):raise ValueError('Chat structure has not been reviewed')
    if not avatar_source:raise ValueError('Avatar asset required; do not silently omit it')
    if type(origin_frame) is not int or type(end_frame) is not int or not 0<=origin_frame<end_frame:raise ValueError('Invalid target native frame range')
    validate_composite(c)
    fps=Fraction(c['frame_rate']['num'],c['frame_rate']['den']);layers=[];review=[]
    def common(component,state,index,z):
        lo=max(origin_frame,c['in_frame'],state['in_frame'],component['in_frame']);hi=min(end_frame,c['out_frame'],state['out_frame'],component['out_frame'])
        if hi<=lo:return None
        return {'id':component['id']+'-layout-'+str(index+1),'semantic_layer_id':component['id'],'in':float(Fraction(lo-origin_frame,1)/fps),'out':float(Fraction(hi-origin_frame,1)/fps),'z':z,'status':'measured_layout_candidate_font_and_asset','source_reference_frames':[lo,hi]}
    def rect(component,state,index,z,box,fill,radius):
        e=common(component,state,index,z)
        if e is not None:
            x,y,x2,y2=box;layers.append({**e,'kind':'rect','position':[x,y],'size':[x2-x,y2-y],'fill':fill,'radius':radius})
    def text(component,state,index,z,box,fill,text,line_number=None):
        e=common(component,state,index,z)
        if e is not None:
            if '\n' in text:raise ValueError('Multiline chat requires measured per-line glyph boxes: '+component['id'])
            x,y,x2,y2=box;suffix='' if line_number is None else '-line-'+str(line_number+1)
            layers.append({**e,'id':e['id']+'-text'+suffix,'kind':'text','font':font,'font_size':48,'tracking':0,'position':[x,y],'text':text,'fill':fill,'glyph_width':x2-x,'glyph_height':y2-y})
    panel=c['panel']
    for i,s in enumerate(panel['layout_states']):rect(panel,s,i,30,s['bbox_1080'],panel['fill_observed_decoded_rgb'],s['corner_radius_px_approx'])
    if panel.get('shadow',{}).get('observed'):review.append({'component':panel['id'],'unimplemented_property':'shadow','reason':'Observed but blur/opacity unmeasured; no invented setting used'})
    sender=c['sender']
    for i,s in enumerate(sender['layout_states']):text(sender,s,i,33,s['glyph_bbox_1080'],sender['fill_observed_decoded_rgb'],sender['text'])
    avatar=c['avatar']
    for i,s in enumerate(avatar['layout_states']):
        e=common(avatar,s,i,32)
        if e is not None:
            x,y,x2,y2=s.get('asset_bbox_1080',s['bbox_1080']);layers.append({**e,'kind':'image','source':avatar_source,'position':[x,y],'size':[x2-x,y2-y]})
    for m in c['messages']:
        if not m.get('timing_verified') or not m.get('text_verified'):raise ValueError('Message not verified '+m['id'])
        if m['bubble']['tail'].get('present'):raise ValueError('Measured tail geometry required '+m['id'])
        for i,s in enumerate(m['layout_states']):
            rect(m,s,i,34,s['bubble_bbox_1080'],m['bubble']['fill_observed_decoded_rgb'],s['bubble_corner_radius_px_approx'])
            lines=m['text'].split('\n')
            if len(lines)==1:text(m,s,i,35,s['text_glyph_bbox_1080'],m['text_style']['fill_observed_decoded_rgb'],m['text'])
            else:
                for j,(line,box) in enumerate(zip(lines,s['text_line_glyph_bboxes_1080'])):
                    text(m,s,i,35,box,m['text_style']['fill_observed_decoded_rgb'],line,j)
    if c.get('adjacent_photo_asset'):
        if not photo_source:raise ValueError('Adjacent photo asset required: '+c['id'])
        photo=c['adjacent_photo_asset'];photo={**photo,'id':c['id']+'-photo'}
        if not c['in_frame']<=photo['in_frame']<photo['out_frame']<=c['out_frame']:raise ValueError('Adjacent photo outside chat range')
        e=common(photo,photo,0,36)
        if e is not None:
            x,y,x2,y2=photo['bbox_1080'];layers.append({**e,'kind':'image','source':photo_source,'position':[x,y],'size':[x2-x,y2-y]})
    return layers,review

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--composites',required=True);ap.add_argument('--id',required=True);ap.add_argument('--project',required=True);ap.add_argument('--avatar',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    out=Path(a.output).resolve()
    if out.exists():raise ValueError('Choose a new output path')
    src=Path(a.project).resolve();p=json.loads(src.read_text(encoding='utf-8'));data=json.loads(Path(a.composites).read_text(encoding='utf-8'));items=data.get('chat_composites',data.get('composites',[data.get('example')]))
    c=next((v for v in items if v and v['id']==a.id),None)
    if c is None:raise ValueError('Composite not found '+a.id)
    # Rebase paths so a different destination directory never breaks sources.
    for group in ('media','fonts'):
        for v in p[group].values():
            path=Path(v['path']);v['path']=str(path if path.is_absolute() else (src.parent/path).resolve())
    p['media']['chat-avatar']={'path':str(Path(a.avatar).resolve()),'type':'image','role':'reference_derived_graphic_asset','supports_new_text':False,'provenance':c['avatar'].get('asset_provenance')}
    kit=Path(__file__).resolve().parents[1];p['fonts']['chat']={'path':str(kit/'assets/fonts/SUIT-Variable.ttf'),'weight':450,'status':'candidate_not_original_font_confirmed'}
    photo_source=None
    if c.get('adjacent_photo_asset'):
        photo_source='chat-photo';photo=c['adjacent_photo_asset'];p['media'][photo_source]={'path':str(kit/photo['asset']),'type':'image','role':'reference_derived_graphic_asset','provenance':photo.get('provenance')}
    lo,hi=p['reference_range_frames'];layers,review=compile_chat(c,lo,hi,'chat-avatar',photo_source=photo_source);p['layers']+=layers;p.setdefault('unresolved_properties',[]).extend(review)
    p['limitations']+=['Chat messages and avatar are independent editable layers. Candidate font and unmeasured panel shadow remain different from original.']
    p['chat_evidence']=str(Path(a.composites).resolve());p['release_ready']=False;validate(p,out.parent,True)
    for group in ('media','fonts'):
        for v in p[group].values():
            if Path(v['path']).is_relative_to(kit):v['path']=os.path.relpath(v['path'],out.parent)
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output':str(out),'chat_layers':len(layers),'unresolved_properties':review},ensure_ascii=False))
if __name__=='__main__':main()
