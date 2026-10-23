#!/usr/bin/env python3
"""Build editable cumulative A chat panels from source-bound caption messages.

New text comes from explicit caption IDs, never a recovered whole-panel raster.
Source intersections and message additions follow the existing selected takes.
Footage, speech, duration and preexisting captions stay literal. Candidate font,
layout and palette are based on measured A panels, not confirmed author files.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, json, math, os, re, tempfile
import apply_source_assets as shared
from render import Composer, validate

SCHEMA='atrass-source-chat-v1'
STYLE={'panel_fill':'#B9CDE0','left_bubble_fill':'#FFFFFF','right_bubble_fill':'#FDE400',
       'text_fill':'#000000','panel_width':640,'left':93,'center_y':410,
       'panel_radius':26,'bubble_radius':24,'font_size':48,'glyph_height':27,
       'line_gap':12,'max_text_width':440,'bubble_padding':[21,22],
       'message_gap':13,'header_height':96,'bottom_padding':24,
       'scope':'A measured color/layout candidate; exact original font/author RGBA unknown. No triangle tails.'}


def normalized(s):return ' '.join(s.split())


def wrap_text(text,composer,template,maximum):
    """Greedy word wrap; oversized unspaced words split by Unicode characters."""
    shared.require(isinstance(text,str) and normalized(text) and len(text)<=1500,'Empty/oversized message')
    lines=[];line=''
    for word in text.split():
        trial=(line+' '+word).strip()
        if composer.text_mask(trial,template).width<=maximum:
            line=trial;continue
        if line:lines.append(line);line=''
        for char in word:
            trial=line+char
            if composer.text_mask(trial,template).width>maximum:
                shared.require(bool(line),'One character exceeds message width')
                lines.append(line);line=char
            else:line=trial
    if line:lines.append(line)
    shared.require(len(lines)<=8,'Message exceeds eight lines')
    shared.require(normalized(' '.join(lines)).replace(' ','')==normalized(text).replace(' ',''),'Wrap lost characters')
    return '\n'.join(lines)


def build(project,events,output):
    project,events,output=[Path(x).absolute()for x in (project,events,output)]
    shared.require(output.suffix=='.json' and output.parent.resolve()==project.parent.resolve(),'New output JSON must be beside input')
    shared.require(not os.path.lexists(output),'Output exists; use a new path')
    # macOS /var aliases /private/var. Relative assets must use the same real
    # parent as the renderer; preserve the output filename/create-only guard.
    project=project.resolve();events=events.resolve();output=output.parent.resolve()/output.name
    p,psha=shared.read(project);spec,esha=shared.read(events)
    shared.keys(spec,('schema','project_sha256','source_sha256','panels'),('schema','project_sha256','source_sha256','panels'),'source chat')
    shared.require(spec['schema']==SCHEMA and spec['project_sha256']==psha,'Source chat schema/project identity mismatch')
    shared.hash_literal(spec['source_sha256'],'source_sha256')
    shared.require(p.get('variant')=='A' and (p['width'],p['height'],p['fps'])==(1920,1080,'60000/1001'),'A1080p60000/1001 required')
    validate(p,project.parent,True);fps,takes=shared.schedule(p,spec['source_sha256'])
    rawpaths={shared.path_at(project.parent,m['path'])for m in p['media'].values()if m.get('role')=='source_footage' and m.get('sha256')==spec['source_sha256']}
    shared.require(len(rawpaths)==1,'Unique raw source required');raw=next(iter(rawpaths));rawstat=shared.stat_signature(raw)
    shared.require(shared.sha(raw)==spec['source_sha256'] and shared.stat_signature(raw)==rawstat,'Actual raw SHA mismatch')
    kit=Path(__file__).resolve().parents[1];font=kit/'assets/fonts/SUIT-Variable.ttf';avatar=kit/'assets/chat/avatar-profile-native-reference.png';measurement=kit/'references/A-chat-composites-measured.json'
    measured,_=shared.read(measurement);avatar_ref=measured['chat_composites'][0]['avatar']['asset_provenance']
    shared.require(shared.sha(avatar)==avatar_ref['sha256'],'Finite avatar identity mismatch')
    result=copy.deepcopy(p);font_id='source-chat-suit';avatar_id='source-chat-avatar'
    shared.require(font_id not in p['fonts'] and avatar_id not in p['media'],'Source chat resources already exist')
    result['fonts'][font_id]={'path':shared.relative(font,output.parent),'sha256':shared.sha(font),'weight':450,'status':'candidate_not_original_font_confirmed'}
    result['media'][avatar_id]={'path':shared.relative(avatar,output.parent),'type':'image','role':'finite_reference_derived_profile_avatar','sha256':shared.sha(avatar),'original_asset_available':False}
    comp=Composer(result,output.parent);font_template={'id':'measure','kind':'text','font':font_id,'font_size':STYLE['font_size'],'glyph_height':STYLE['glyph_height'],'line_gap':STYLE['line_gap'],'align':'left','padding':[0,0],'fill':STYLE['text_fill']}
    dependencies={font:shared.sha(font),avatar:shared.sha(avatar),measurement:shared.sha(measurement)}
    for group in ('media','fonts'):
        for row in p[group].values():
            path=shared.path_at(project.parent,row['path'])
            if path!=raw and 'sha256'in row:dependencies[path]=row['sha256']
    panels=spec['panels'];shared.require(isinstance(panels,list) and panels,'Nonempty panels required')
    shared.require(len({q.get('id')for q in panels})==len(panels),'Duplicate panel IDs')
    layers={e['id']:e for e in p['layers']};known=set(layers);occupied=[];records=[]
    for q in panels:
        shared.keys(q,('id','sender','source_range','take_ids','messages'),('id','sender','source_range','take_ids','messages'),'panel')
        shared.require(isinstance(q['id'],str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,80}',q['id']),'Unsafe panel ID')
        shared.require(isinstance(q['sender'],str) and 1<=len(q['sender'])<=30 and '\n'not in q['sender'],'Invalid sender')
        lo,hi=shared.pair(q['source_range'],'panel source_range');shared.require(0<=lo<hi,'Invalid source panel interval')
        ids=q['take_ids'];shared.require(isinstance(ids,list) and ids and len(set(ids))==len(ids) and all(x in takes for x in ids),'Explicit supported unique takes required')
        fragments=[]
        for tid in ids:
            m=takes[tid];a,z=m['frames'];c=m['clip'];s0,s1=max(lo,m['source_range'][0]),min(hi,m['source_range'][1]);shared.require(s0<s1,'Panel/take do not intersect')
            first=max(a,math.ceil((a/float(fps)+s0-c['source_in'])*float(fps)-1e-8));last=min(z,math.ceil((a/float(fps)+s1-c['source_in'])*float(fps)-1e-8));shared.require(first<last,'Empty panel interval')
            shared.require(not any(first<b and last>a for a,b in occupied),'Source panels overlap');occupied.append((first,last))
            shared.require(not any(e['kind']!='caption' and e['in']<last/float(fps) and e['out']>first/float(fps) for e in p['layers']),'Panel overlaps another graphic; resolve explicitly')
            fragments.append({'take_id':tid,'source_intersection':[s0,s1],'output_frames':[first,last]})
        fragments.sort(key=lambda f:f['output_frames']);messages=[];used=set()
        shared.require(isinstance(q['messages'],list) and 1<=len(q['messages'])<=6,'One to six explicit messages required')
        for message in q['messages']:
            shared.keys(message,('id','side','caption_ids'),('id','side','caption_ids'),'message')
            shared.require(isinstance(message['id'],str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,80}',message['id']),'Unsafe message ID')
            shared.require(message['side']in ('left','right'),'Invalid message side')
            caption_ids=message['caption_ids'];shared.require(isinstance(caption_ids,list) and caption_ids and len(set(caption_ids))==len(caption_ids),'Distinct caption IDs required')
            selected=[]
            for cid in caption_ids:
                shared.require(cid in layers and cid not in used,'Unknown/reused caption');used.add(cid);e=layers[cid];binding=e.get('source_dialogue_binding',{})
                shared.require(e['kind']=='caption' and binding.get('take_id')in ids,'Message must use bound captions on selected takes')
                a,b=binding['source_range'];shared.require(lo-1e-8<=a<b<=hi+1e-8,'Message words outside panel source interval')
                shared.require(any(f['output_frames'][0]<=shared.frame_boundary(e['in'],fps)<f['output_frames'][1]for f in fragments),'Message onset not selected')
                selected.append(e)
            shared.require(selected==sorted(selected,key=lambda e:e['in']),'Message captions out of order')
            text=' '.join(e['text']for e in selected);display=wrap_text(text,comp,font_template,STYLE['max_text_width']);masks=[comp.text_mask(line,font_template)for line in display.split('\n')]
            w=max(m.width for m in masks);h=sum(m.height for m in masks)+STYLE['line_gap']*(len(masks)-1)
            px,py=STYLE['bubble_padding']
            messages.append({**message,'text':text,'display_text':display,'onset_frame':shared.frame_boundary(selected[0]['in'],fps),'text_size':[w,h],'bubble_size':[w+2*px,h+2*py]})
        shared.require(len({m['id']for m in messages})==len(messages),'Duplicate message IDs')
        shared.require(all(a['onset_frame']<b['onset_frame']for a,b in zip(messages,messages[1:])),'Message onsets must increase')
        shared.require(messages[0]['onset_frame']==fragments[0]['output_frames'][0],'Panel must begin with first message')
        states=[]
        for fragment in fragments:
            first,last=fragment['output_frames'];cuts=sorted({first,last,*[m['onset_frame']for m in messages if first<m['onset_frame']<last]})
            for start,end in zip(cuts,cuts[1:]):
                visible=[m for m in messages if m['onset_frame']<=start];shared.require(visible,'Empty panel state')
                height=STYLE['header_height']+sum(m['bubble_size'][1]for m in visible)+STYLE['message_gap']*(len(visible)-1)+STYLE['bottom_padding'];x=STYLE['left'];y=round(STYLE['center_y']-height/2)
                shared.require(24<=y and y+height<=914,'Panel exceeds space above caption; shorten messages')
                local=[{'id':'panel','kind':'rect','position':[x,y],'size':[640,height],'radius':26,'fill':STYLE['panel_fill'],'z':5},
                       {'id':'avatar','kind':'image','source':avatar_id,'position':[x+24,y+24],'z':6},
                       {'id':'sender','kind':'text','font':font_id,'font_size':48,'glyph_height':21,'text':q['sender'],'fill':STYLE['text_fill'],'position':[x+94,y+42],'z':7}]
                sender_width=comp.text_mask(q['sender'],local[-1]).width;shared.require(sender_width<=520,'Sender would exceed panel')
                cursor=y+STYLE['header_height']
                for m in visible:
                    bw,bh=m['bubble_size'];left=x+640-25-bw if m['side']=='right' else x+94
                    shared.require(x+20<=left and left+bw<=x+620,'Message bubble exceeds panel')
                    local.extend([{'id':m['id']+'-bubble','kind':'rect','position':[left,cursor],'size':[bw,bh],'radius':24,'fill':STYLE['right_bubble_fill']if m['side']=='right' else STYLE['left_bubble_fill'],'z':7},
                                  {**font_template,'id':m['id']+'-text','text':m['display_text'],'position':[left+STYLE['bubble_padding'][0],cursor+STYLE['bubble_padding'][1]],'z':8}]);cursor+=bh+STYLE['message_gap']
                binding={'panel_id':q['id'],'take_id':fragment['take_id'],'source_sha256':spec['source_sha256'],'source_intersection':fragment['source_intersection'],'output_frames':[start,end],'visible_message_ids':[m['id']for m in visible],'events_sha256':esha}
                state_id=len(states)+1
                for layer in local:
                    layer['id']=f"source-chat-{q['id']}-state{state_id:03d}-{layer['id']}";shared.require(layer['id']not in known,'Chat layer collision');known.add(layer['id'])
                    layer.update({'in':float(Fraction(start,1)/fps),'out':float(Fraction(end,1)/fps),'status':'source_bound_chat_candidate','source_chat_binding':copy.deepcopy(binding)});result['layers'].append(layer)
                states.append({**binding,'panel_bbox':[x,y,x+640,y+height],'layer_ids':[e['id']for e in local]})
        records.append({'id':q['id'],'sender':q['sender'],'messages':messages,'fragments':fragments,'states':states})
    shared.require(all(e.get('z',0)>8 for e in p['layers']if e['kind']=='caption'),'Caption z would be covered')
    shared.require(result['clips']==p['clips'] and result['audio']==p['audio'] and result['layers'][:len(p['layers'])]==p['layers'],'Existing edit changed')
    shared.require(shared.sha(project)==psha and shared.sha(events)==esha and shared.stat_signature(raw)==rawstat,'Inputs changed during build')
    shared.require(all(shared.sha(path)==value for path,value in dependencies.items()),'Chat dependency changed')
    result['source_chat_application']={'schema':SCHEMA,'input_project_path':shared.relative(project,output.parent),'input_project_sha256':psha,'events_path':shared.relative(events,output.parent),'events_sha256':esha,'source_sha256':spec['source_sha256'],'implementation_sha256':shared.sha(Path(__file__)),'mapping_implementation_sha256':shared.sha(Path(shared.__file__)),'measurement':{'path':shared.relative(measurement,output.parent),'sha256':shared.sha(measurement)},'style':STYLE,'panels':records,'preserved_literal_fields':['clips','audio','audio_master','all_preexisting_layers','duration'],'direct_listening_seconds':0,'style_approved':False,'release_ready':False}
    result['release_ready']=False;validate(result,output.parent,True);return result


def apply_chat(project,events,output):
    output=Path(output).absolute();result=build(project,events,output);tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=output.parent,prefix='.source-chat-',suffix='.tmp',encoding='utf-8',delete=False)as f:
            tmp=f.name;json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.link(tmp,output)
    finally:
        if tmp is not None:os.unlink(tmp)
    return {'output':str(output),'sha256':shared.sha(output),'panels':len(result['source_chat_application']['panels']),'new_states':sum(len(x['states'])for x in result['source_chat_application']['panels']),'release_ready':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('project','events','output'):parser.add_argument('--'+key,required=True)
    args=parser.parse_args();print(json.dumps(apply_chat(args.project,args.events,args.output),ensure_ascii=False))
