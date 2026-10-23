#!/usr/bin/env python3
"""Export an Atrass renderer draft to FCP7 XML and an After Effects JSX.

python engine/export_nle.py projects/A-pilot-40s.json --output-dir exports/A-pilot-40s --ffmpeg /path/to/ffmpeg
Optional --audio-stems writes one sample-aligned full-timeline WAV per audio event.
PNG glyphs and boxes remain separate. Static text gets disabled AE text alternatives;
character reveals are explicit fixed-canvas PNG states, without editable text.
This is a structurally verified draft interchange, not a certified application import.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math, os, re, shutil, subprocess, sys, tempfile
from fractions import Fraction
from dataclasses import asdict
from pathlib import Path
import xml.etree.ElementTree as ET
import av
import numpy as np
from PIL import Image, ImageDraw
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
import render as renderer
from audio_policy import validate_audio_policy, STRICT


def atomic(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n' if not isinstance(value,str) else value, encoding='utf-8');tmp.replace(path)

def frame(t,fps):return math.ceil(float(t)*float(fps)-1e-7)
def rate_tuple(fps):
    f=Fraction(fps)
    if f.denominator==1:return f.numerator,False
    base=f*Fraction(1001,1000)
    if base.denominator==1:return base.numerator,True
    raise ValueError('FCP7 timebase cannot express FPS '+str(f))
def safe(s):return re.sub(r'[^A-Za-z0-9_.-]','_',s)
def sub(e,k,v=None,**attrs):
    z=ET.SubElement(e,k,attrs)
    if v is not None:z.text=str(v)
    return z

def rate(e,fps):
    base,ntsc=rate_tuple(fps);r=sub(e,'rate');sub(r,'timebase',base);sub(r,'ntsc','TRUE' if ntsc else 'FALSE')

def probe(path):
    c=av.open(str(path));s=c.streams.video[0];fps=Fraction(s.average_rate);duration=float(s.duration*s.time_base) if s.duration is not None else c.duration/1e6
    result={'width':s.width,'height':s.height,'fps':str(fps),'time_base':str(s.time_base),'duration':duration,'frames':s.frames or math.ceil(duration*float(fps)),'start_seconds':float((s.start_time or 0)*s.time_base)};c.close();return result

def preflight(p):
    if validate_audio_policy(p) != STRICT:
        raise ValueError('NLE does not support bounded sample_underflow_policy: AE source audio and stems cannot preserve the declared reconstruction; export is refused before files are written')
    # Fail closed instead of silently flattening animation or unsupported shapes.
    # This is also called directly by validate_nle_export, without render.validate.
    # Blend is an execution field only on layers, never an asset/source property.
    if 'blend_mode' in p:raise ValueError('NLE blend_mode is not supported on the project')
    for group in ('media','fonts'):
        for key,item in p.get(group,{}).items():
            if 'blend_mode' in item:raise ValueError('NLE blend_mode is not supported on '+group+' '+key)
    for group in ('clips','audio'):
        for item in p.get(group,[]):
            if 'blend_mode' in item:raise ValueError('NLE blend_mode is not supported on '+group+' '+item['id'])
            if isinstance(item.get('geometry'),dict) and 'blend_mode' in item['geometry']:
                raise ValueError('NLE blend_mode is not supported inside geometry '+item['id'])
    for e in p['layers']:
        if e.get('blend_mode','normal')!='normal':
            raise ValueError('NLE supports only normal layer blend_mode; Screen requires explicit future interchange support '+e['id'])
        if isinstance(e.get('geometry'),dict) and 'blend_mode' in e['geometry']:
            raise ValueError('NLE blend_mode is not supported inside layer geometry '+e['id'])
        if e['kind'] not in ('text','caption','label','rect','image'):raise ValueError('NLE unsupported layer kind '+e['id']+': '+e['kind'])
        # render.py reads scale_keys with default 1, not a top-level scale.
        # Reject this intent for both static and reveal text instead of silently
        # exporting an unscaled layer. Source clip geometry.scale is separate.
        if e.get('scale',1)!=1:raise ValueError('NLE layer scale is unsupported; renderer does not apply top-level scale '+e['id'])
        if e.get('character_reveal') is not None:
            if e['kind']!='text' or any(k in e for k in ('box','background','glyph_width','glyph_height')):
                raise ValueError('NLE character reveal requires independent fixed-canvas text '+e['id'])
            try:renderer.RevealTiming(**e['character_reveal'])
            except (TypeError,ValueError) as exc:raise ValueError('Invalid NLE character reveal '+e['id']) from exc
            if 'text' not in e or not isinstance(e['text'],str):raise ValueError('Character reveal text is required '+e['id'])
        dynamic=[k for k in e if k.endswith('_keys') and e[k]]
        dynamic += [k for k in ('fade_in','fade_out','rotation') if e.get(k,0)!=0]
        if e.get('opacity',1)!=1:dynamic.append('opacity')
        if e.get('font') and e.get('weight',p['fonts'][e['font']].get('weight'))!=p['fonts'][e['font']].get('weight'):raise ValueError('Per-layer font weight override unsupported '+e['id'])
        if dynamic:raise ValueError('NLE animated/transformed layer needs explicit support '+e['id']+': '+str(dynamic))
        if e.get('anchor') not in (None,):raise ValueError('NLE layer anchor unsupported '+e['id'])
        if e.get('shadow') or e.get('stroke_width'):raise ValueError('Native editable shadow/stroke unsupported; export explicitly in future: '+e['id'])
    clipids=[c['id'] for c in p['clips']]
    if len(clipids)!=len(set(clipids)):raise ValueError('NLE duplicate clip ID')
    video_sources={c['source'] for c in p['clips']}
    for c in p['clips']:
        if any(k.endswith('_keys') for k in c.get('geometry',{})):raise ValueError('Animated source geometry unsupported '+c['id'])
        g=c.get('geometry',{})
        if g.get('scale_x',g.get('scale'))!=g.get('scale_y',g.get('scale')):raise ValueError('Anisotropic FCP7 geometry unsupported '+c['id'])
    for a in p.get('audio',[]):
        if a['source'] not in video_sources:raise ValueError('AE audio-only source import needs explicit support '+a['id'])
        if a.get('rate',1)!=1:raise ValueError('AE raw audio rate other than 1 is not exported '+a['id'])
        if a.get('fade_in') or a.get('fade_out'):raise ValueError('AE raw audio fade export not yet implemented '+a['id'])

def visual_span(e,p):
    """Output frames whose sample times satisfy the renderer's half-open interval.

    Match render.py's `in <= t + 1e-9 < out`, including its boundary tolerance.
    Generic XML ceil uses a different tolerance and cannot replace this check.
    """
    fps=Fraction(p['fps']);n=frame(p['duration'],fps)
    def lower(t):
        i=max(0,min(n,frame(t,fps)))
        while i<n and float(Fraction(i,1)/fps)+1e-9<t:i+=1
        while i>0 and float(Fraction(i-1,1)/fps)+1e-9>=t:i-=1
        return i
    return lower(e['in']),lower(e['out'])

def reveal_states(e,p,composer):
    """Lossless raster states sampled at every affected output frame, never substrings.

    Only adjacent byte-identical full logical canvases are coalesced. Individual
    glyph fade samples remain explicit PNG states. A final static hold uses the
    SAME CharacterReveal object and canvas; it never switches text renderers.
    """
    fps=Fraction(p['fps']);lo,hi=visual_span(e,p)
    if lo>=hi:raise ValueError('Character reveal has no visible output frames '+e['id'])
    timing=renderer.RevealTiming(**e['character_reveal']);current=None
    for i in range(lo,hi):
        t=float(Fraction(i,1)/fps);im,xy=composer.image(e,t);pixels=im.tobytes()
        if current is not None and pixels==current['pixels']:
            current['end']=i+1
        else:
            if current is not None:yield current
            current={'start':i,'end':i+1,'image':im,'position':list(xy),'pixels':pixels}
        if t-e['in']>=timing.start+timing.duration:
            current['end']=hi
            break
    if current is not None:yield current

def fonts(p,root,out):
    result={}
    for key,desc in p.get('fonts',{}).items():
        src=renderer.resolve(root,desc['path']);f=TTFont(src);original=f['name'].getDebugName(6);weight=desc.get('weight')
        if 'fvar' in f:
            axes={a.axisTag:a.defaultValue for a in f['fvar'].axes}
            if weight is not None:axes['wght']=weight
            f=instantiateVariableFont(f,axes,inplace=True)
        name='AtrassCandidate-'+safe(key)+'-W'+str(weight or 400);family=name
        for nid,value in [(1,family),(2,'Regular'),(3,name),(4,name),(6,name),(16,family),(17,'Regular')]:
            f['name'].setName(value,nid,3,1,0x409);f['name'].setName(value,nid,1,0,0)
        dst=out/'fonts'/f'{name}.ttf';dst.parent.mkdir(exist_ok=True);f.save(dst);check=TTFont(dst)
        for license_file in src.parent.glob('*OFL.txt'):
            shutil.copy2(license_file,dst.parent/license_file.name)
        assert check['name'].getDebugName(6)==name and 'fvar' not in check
        result[key]={'postscript_name':name,'family':family,'path':str(dst.relative_to(out)),'source_font':str(src),'source_font_sha256':renderer.sha(src),'original_variable_postscript_name':original,'weight_instance':weight,'export_font_sha256':renderer.sha(dst),'status':'candidate_not_original_identified','installation':'Install this static candidate font before enabling AE editable text; no font is installed automatically.'}
    return result

def components(p,root,out):
    composer=renderer.Composer(p,root);rows=[];dest=out/'assets';dest.mkdir(exist_ok=True)
    paths=set()
    def save(e,kind,im,pos,cid=None,**extra):
        cid=cid or e['id']+'-'+kind;crop=dest/(safe(cid)+'.png');canvas=dest/(safe(cid)+'-canvas.png')
        if str(crop) in paths:raise ValueError('NLE asset filename collision '+cid)
        paths.add(str(crop));im.save(crop)
        full=Image.new('RGBA',(p['width'],p['height']));full.alpha_composite(im,tuple(map(round,pos)));full.save(canvas)
        row={'id':cid,'event_id':e['id'],'component':kind,'source_kind':e['kind'],'in':e['in'],'out':e['out'],'z':e.get('z',0),'position':pos,'size':list(im.size),'path':str(crop.relative_to(out)),'canvas_path':str(canvas.relative_to(out)),'rgba_sha256':hashlib.sha256(im.tobytes()).hexdigest(),'png_sha256':renderer.sha(crop),'canvas_png_sha256':renderer.sha(canvas),'editable_text':None,**extra}
        rows.append(row);return row
    for e in sorted(p['layers'],key=lambda e:e.get('z',0)):
        if e.get('character_reveal') is not None:
            layout=composer.character_layer(e);lo,hi=visual_span(e,p);fps=Fraction(p['fps'])
            for index,state in enumerate(reveal_states(e,p,composer)):
                save(e,'glyph-state',state['image'],state['position'],e['id']+'-glyph-state-'+str(index).zfill(4),
                     **{'in':float(Fraction(state['start'],1)/fps),'out':float(Fraction(state['end'],1)/fps)},
                     in_frame=state['start'],out_frame=state['end'],
                     character_reveal_state={'parent_event_id':e['id'],'state_index':index,'parent_seconds':[e['in'],e['out']],
                         'parent_sampled_frames':[lo,hi],'unit':'Unicode codepoint; whitespace reserves layout but no reveal slot',
                         'text_editing':'Change source JSON and re-export; PNG states have no native text alternative'},
                     **({'reveal_layout':{'text':e['text'],'timing':asdict(renderer.RevealTiming(**e['character_reveal'])),
                         'canvas_size':list(layout.size),'glyph_count':layout.glyph_count,
                         'placements':[asdict(g) for g in layout.placements]}} if index==0 else {}))
            continue
        parts=[];base=copy.deepcopy(e)
        if e['kind'] in ('caption','text','label') and e.get('background'):
            box=e.get('box');size=[box[2]-box[0],box[3]-box[1]] if box else composer.build_static(e).size
            boxim=Image.new('RGBA',tuple(size));ImageDraw.Draw(boxim).rounded_rectangle((0,0,size[0]-1,size[1]-1),radius=e.get('radius',0),fill=renderer.rgba(e['background']))
            parts.append(('box',boxim));base.pop('background',None)
        parts.append(('glyph' if e['kind'] in ('caption','text','label') else e['kind'],composer.build_static(base)))
        pos=e.get('box',e.get('position',[0,0]))[:2]
        for kind,im in parts:
            row=save(e,kind,im,pos)
            if kind=='glyph':
                mask=composer.text_mask(e['text'],e) if '\n' not in e['text'] else None
                if mask is None:raise ValueError('Multiline native text fitting needs explicit implementation '+e['id'])
                bbox=im.getchannel('A').getbbox();row['editable_text']={'text':e['text'],'font':e['font'],'font_size':e.get('font_size',64),'tracking_1000em':e.get('tracking',0)/e.get('font_size',64)*1000,'fill':[v/255 for v in renderer.rgb(e.get('fill','#ffffff'))[:3]],'ink_box':list(bbox),'note':'AE shaping/rasterization may differ; native text is initially disabled and PNG is the fidelity default.'}
    # Exact split-alpha recomposition test, every source layer.
    errors=[]
    for e in p['layers']:
        if e.get('character_reveal') is not None:continue # Independently replayed in audit_reveals.
        original=composer.build_static(e);rebuilt=Image.new('RGBA',original.size)
        for r in rows:
            if r['event_id']==e['id']:rebuilt.alpha_composite(Image.open(out/r['path']).convert('RGBA'))
        a=np.array(original,dtype=np.uint32);b=np.array(rebuilt,dtype=np.uint32)
        same=a.shape==b.shape and np.array_equal(a[:,:,3],b[:,:,3]) and np.array_equal(a[:,:,:3]*a[:,:,3:],b[:,:,:3]*b[:,:,3:])
        if not same:errors.append(e['id'])
    if errors:raise ValueError('Split glyph/background did not exactly reconstruct renderer layers '+str(errors))
    return rows

def audit_reveals(p,comps,out,project_root=None):
    """Partition, canvas and replay checks; per-frame rendered states, not authored NLE metadata."""
    ids=[r['id'] for r in comps]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate exported component ID')
    if {r['event_id'] for r in comps}!={e['id'] for e in p['layers']}:raise ValueError('Logical layer missing or unexpected')
    ordered=[]
    for r in comps:
        if not ordered or ordered[-1]!=r['event_id']:ordered.append(r['event_id'])
    if ordered!=[e['id'] for e in sorted(p['layers'],key=lambda e:e.get('z',0))]:raise ValueError('Exported component stacking order differs')
    composer=renderer.Composer(p,project_root) if project_root is not None else None
    count=frames=0
    for e in p['layers']:
        rows=[r for r in comps if r['event_id']==e['id']]
        if any(r['z']!=e.get('z',0) or r['source_kind']!=e['kind'] for r in rows):raise ValueError('Exported parent identity/stack differs '+e['id'])
        if e.get('character_reveal') is None:
            if any('character_reveal_state' in r for r in rows):raise ValueError('Unexpected reveal child '+e['id'])
            continue
        lo,hi=visual_span(e,p);cursor=lo;expected=list(reveal_states(e,p,composer)) if composer else None
        if expected is not None and len(rows)!=len(expected):raise ValueError('Reveal state count differs '+e['id'])
        for index,r in enumerate(rows):
            s=r.get('character_reveal_state',{});a,b=r.get('in_frame'),r.get('out_frame')
            if (type(a) is not int or type(b) is not int or a!=cursor or not a<b<=hi or
                s.get('parent_event_id')!=e['id'] or s.get('state_index')!=index or
                s.get('parent_seconds')!=[e['in'],e['out']] or s.get('parent_sampled_frames')!=[lo,hi] or
                r.get('component')!='glyph-state' or r.get('editable_text') is not None or
                r['size']!=rows[0]['size'] or r['position']!=rows[0]['position'] or
                [r['in'],r['out']]!=[float(Fraction(f,1)/Fraction(p['fps'])) for f in (a,b)]):
                raise ValueError('Invalid reveal parent/state partition '+e['id'])
            cursor=b
            if expected is not None:
                st=expected[index];im=Image.open(out/r['path']).convert('RGBA')
                if (a!=st['start'] or b!=st['end'] or r['position']!=st['position'] or r['size']!=list(im.size) or im.size!=st['image'].size or
                    im.tobytes()!=st['pixels'] or renderer.sha(out/r['path'])!=r['png_sha256'] or
                    hashlib.sha256(im.tobytes()).hexdigest()!=r['rgba_sha256']):raise ValueError('Reveal PNG replay differs '+r['id'])
                full=Image.new('RGBA',(p['width'],p['height']));full.alpha_composite(im,tuple(r['position']))
                canvas=Image.open(out/r['canvas_path']).convert('RGBA')
                if full.size!=canvas.size or full.tobytes()!=canvas.tobytes() or renderer.sha(out/r['canvas_path'])!=r['canvas_png_sha256']:raise ValueError('Reveal full-canvas placement differs '+r['id'])
        if cursor!=hi:raise ValueError('Reveal child coverage gap '+e['id'])
        if composer:
            layout=composer.character_layer(e)
            want={'text':e['text'],'timing':asdict(renderer.RevealTiming(**e['character_reveal'])),'canvas_size':list(layout.size),'glyph_count':layout.glyph_count,'placements':[asdict(g) for g in layout.placements]}
            if json.dumps(rows[0].get('reveal_layout'),sort_keys=True)!=json.dumps(want,sort_keys=True):raise ValueError('Reveal fixed glyph layout differs '+e['id'])
        count+=len(rows);frames+=hi-lo
    return {'character_reveal_state_count':count,'character_reveal_parent_sample_frames':frames,'character_reveal_partition_exact':True,'character_reveal_pixel_replay_exact':bool(composer) if count else None}

def timeline(p,root):
    fps=Fraction(p['fps']);n=frame(p['duration'],fps);info={};rows=[];ci=0;clips=sorted(p['clips'],key=lambda c:c['in'])
    for i in range(n):
        t=float(Fraction(i,1)/fps)
        while ci+1<len(clips) and clips[ci]['out']<=t+1e-9:ci+=1
        c=clips[ci];mid=c['source'];path=renderer.resolve(root,p['media'][mid]['path'])
        if p['media'][mid].get('type')=='image':raise ValueError('Base still-image clips not yet exported '+c['id'])
        if mid not in info:info[mid]={'path':str(path),**probe(path)}
        inf=info[mid];sfps=Fraction(inf['fps'])
        if inf['start_seconds']!=0:raise ValueError('Nonzero source start PTS needs explicit index adapter '+mid)
        local=t-c['in'];arr=c.get('source_frame_times');st=arr[round(local*float(fps))] if arr is not None else c['source_in']+(0 if c.get('freeze_source') else local*c.get('rate',1));ix=round(st*float(sfps))
        if not 0<=ix<inf['frames']:raise ValueError('Source frame outside media '+c['id'])
        if arr is not None and abs(ix/float(sfps)-st)>1e-6:raise ValueError('Source array is not on probed CFR grid; requires native PTS index adapter '+c['id'])
        rows.append({'frame':i,'clip_id':c['id'],'source':mid,'source_frame':ix,'source_seconds':st,'geometry':c.get('geometry',{})})
    # Only merge +1 source steps when input and sequence FPS agree exactly.
    runs=[]
    for r in rows:
        can=bool(runs and Fraction(info[r['source']]['fps'])==fps and runs[-1]['clip_id']==r['clip_id'] and runs[-1]['source']==r['source'] and runs[-1]['source_out']==r['source_frame'] and runs[-1]['end']==r['frame'])
        if can:runs[-1]['end']+=1;runs[-1]['source_out']+=1
        else:runs.append({'start':r['frame'],'end':r['frame']+1,'source_in':r['source_frame'],'source_out':r['source_frame']+1,'source':r['source'],'clip_id':r['clip_id'],'geometry':r['geometry']})
    return {'fps':str(fps),'frame_count':n,'duration':float(Fraction(n,1)/fps),'source_metadata':info,'frames':rows,'runs':runs,'policy':'Frame-addressed FCP clips; +1 runs merge only when source and sequence FPS match. AE uses HOLD time remap at every output frame.'}

def audio(p,root,out,ffmpeg,stems):
    if not p.get('audio'):return {'present':False}
    with tempfile.TemporaryDirectory(prefix='audio-',dir=out) as tmp:
        tmp=Path(tmp);raw,stat=renderer.render_audio(p,root,0,p['duration'],ffmpeg,tmp);dst=out/'audio-mix.wav'
        subprocess.run([ffmpeg,'-v','error','-f','f32le','-ar','48000','-ac','2','-i',str(raw),'-c:a','pcm_s24le',str(dst)],check=True)
        stat.update({'path':dst.name,'sha256':renderer.sha(dst),'source_events':len(p['audio']),'format':'48000Hz stereo PCM24','sample_accurate_mix':True,'stems':[]})
        if stems:
            folder=out/'audio-stems';folder.mkdir()
            for a in p['audio']:
                single={**p,'audio':[a],'audio_master':{}};b,st=renderer.render_audio(single,root,0,p['duration'],ffmpeg,tmp)
                dest=folder/(safe(a['id'])+'.wav');atten=stat['master_attenuation_db'];cmd=[ffmpeg,'-v','error','-f','f32le','-ar','48000','-ac','2','-i',str(b)]
                if atten:cmd+=['-af',f'volume={atten}dB']
                subprocess.run(cmd+['-c:a','pcm_s24le',str(dest)],check=True);stat['stems'].append({'id':a['id'],'path':str(dest.relative_to(out)),'sha256':renderer.sha(dest)})
    return stat

def fcp_xml(p,t,comps,out):
    fps=Fraction(p['fps']);root=ET.Element('xmeml',{'version':'5'});seq=sub(root,'sequence',id='atrass-sequence');sub(seq,'name',p.get('title','Atrass')+' [DRAFT NLE]');sub(seq,'duration',t['frame_count']);rate(seq,fps);tc=sub(seq,'timecode');rate(tc,fps);sub(tc,'frame',0);sub(tc,'displayformat','NDF')
    media=sub(seq,'media');video=sub(media,'video');fmt=sub(sub(video,'format'),'samplecharacteristics');rate(fmt,fps)
    for k,v in [('width',p['width']),('height',p['height']),('anamorphic','FALSE'),('pixelaspectratio','square'),('fielddominance','none')]:sub(fmt,k,v)
    used=set()
    def file(parent,ident,path,inf,kind='video',still=False):
        f=sub(parent,'file',id=ident)
        if ident in used:return
        used.add(ident);sub(f,'name',Path(path).name);sub(f,'pathurl',Path(path).resolve().as_uri());sub(f,'duration',inf.get('frames',t['frame_count']));rate(f,inf.get('fps',fps));m=sub(f,'media')
        if kind=='video':
            sc=sub(sub(m,'video'),'samplecharacteristics');rate(sc,inf.get('fps',fps));sub(sc,'width',inf['width']);sub(sc,'height',inf['height']);sub(sc,'anamorphic','FALSE');sub(sc,'pixelaspectratio','square');sub(sc,'fielddominance','none')
        else:
            a=sub(m,'audio');sc=sub(a,'samplecharacteristics');sub(sc,'depth',24);sub(sc,'samplerate',48000);sub(a,'channelcount',2)
    def clip(track,ident,name,start,end,si,so,inf):
        c=sub(track,'clipitem',id=ident);sub(c,'name',name);sub(c,'enabled','TRUE');sub(c,'duration',inf.get('frames',t['frame_count']));rate(c,inf.get('fps',fps))
        for k,v in [('start',start),('end',end),('in',si),('out',so)]:sub(c,k,v)
        return c
    def motion(c,g,inf):
        scale=g.get('scale',p['width']/inf['width']);cx=inf['width']*scale/2+g.get('tx',0)-p['width']/2;cy=inf['height']*scale/2+g.get('ty',0)-p['height']/2
        ef=sub(sub(c,'filter'),'effect')
        for k,v in [('name','Basic Motion'),('effectid','basic'),('effectcategory','motion'),('effecttype','motion'),('mediatype','video')]:sub(ef,k,v)
        par=sub(ef,'parameter');sub(par,'parameterid','scale');sub(par,'name','Scale');sub(par,'value',scale*100)
        par=sub(ef,'parameter');sub(par,'parameterid','center');sub(par,'name','Center');v=sub(par,'value');sub(v,'horiz',cx/p['width']);sub(v,'vert',cy/p['height'])
    track=sub(video,'track')
    for i,r in enumerate(t['runs']):
        inf=t['source_metadata'][r['source']];c=clip(track,'raw-'+str(i),r['clip_id']+' rawf'+str(r['source_in']),r['start'],r['end'],r['source_in'],r['source_out'],inf);file(c,'file-'+safe(r['source']),inf['path'],inf);sub(c,'anamorphic','FALSE');sub(c,'pixelaspectratio','square');motion(c,r['geometry'],inf)
    for i,r in enumerate(comps):
        track=sub(video,'track');a=frame(r['in'],fps);b=frame(r['out'],fps);inf={'fps':str(fps),'frames':t['frame_count'],'width':p['width'],'height':p['height']};c=clip(track,'layer-'+str(i),r['id'],a,b,0,b-a,inf);sub(c,'stillframe','TRUE');sub(c,'alphatype','straight');file(c,'layer-file-'+str(i),out/r['canvas_path'],inf,still=True)
    if p.get('audio'):
        au=sub(media,'audio');sub(au,'numOutputChannels',2)
        for channel in (1,2):
            tr=sub(au,'track');sub(tr,'outputchannelindex',channel);inf={'frames':t['frame_count'],'fps':str(fps)};c=clip(tr,'audio-mix-'+str(channel),'Raw-only sample-aligned mix ch'+str(channel),0,t['frame_count'],0,t['frame_count'],inf);file(c,'audio-file',out/'audio-mix.wav',inf,'audio');src=sub(c,'sourcetrack');sub(src,'mediatype','audio');sub(src,'trackindex',channel)
    ET.indent(root);xml='<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'+ET.tostring(root,encoding='unicode')+'\n';atomic(out/'premiere.xml',xml)


def ae_jsx(p,t,comps,fontmap,audioinfo,out):
    # All data embedded; assets relative to script, source media absolute.
    data={'project':p,'timeline':t,'components':comps,'fonts':fontmap,'audio':audioinfo}
    script='''#target aftereffects
// DRAFT interchange. Default visual: exact renderer PNGs; editable text candidates disabled.
// Existing projects are preserved: this script only adds a new folder/composition.
(function () {
var D = __DATA__;
var base = File($.fileName).parent;
var warnings = []; var warned={};
function warn(s){if(!warned[s]){warnings.push(s);warned[s]=true;}}
function asset(path) { return new File(base.fsName + "/" + path); }
function importFile(file) { if (!file.exists) throw new Error("Missing media: " + file.fsName); return app.project.importFile(new ImportOptions(file)); }
function hold(prop) { for (var k=1;k<=prop.numKeys;k++) prop.setInterpolationTypeAtKey(k,KeyframeInterpolationType.HOLD,KeyframeInterpolationType.HOLD); }
function at(layer,lo,hi) { layer.inPoint=lo; layer.outPoint=hi; }
app.beginUndoGroup("Atrass NLE draft import");
try {
if (!app.project) app.newProject();
var folder=app.project.items.addFolder("ATRASS DRAFT - " + D.project.title);
var comp=app.project.items.addComp(D.project.title + " [DRAFT]",D.project.width,D.project.height,1,D.timeline.duration,__FPS__);
comp.parentFolder=folder; comp.bgColor=[0,0,0];
var source={};
for (var id in D.timeline.source_metadata) { var item=importFile(new File(D.timeline.source_metadata[id].path));item.parentFolder=folder;source[id]=item; }
for (var i=0;i<D.project.clips.length;i++) {
 var c=D.project.clips[i], layer=comp.layers.add(source[c.source]);layer.name=c.id+" [HOLD native frame map]";layer.audioEnabled=false;
 layer.timeRemapEnabled=true;var remap=layer.property("ADBE Time Remapping");while(remap.numKeys)remap.removeKey(1);
 var times=[],values=[];for(var j=0;j<D.timeline.frames.length;j++){var f=D.timeline.frames[j];if(f.clip_id==c.id){times.push(f.frame/comp.frameRate);values.push(f.source_frame/D.timeline.source_metadata[c.source].fps_value);}}
 remap.setValuesAtTimes(times,values);hold(remap);at(layer,times[0],times[times.length-1]+comp.frameDuration);
 var g=c.geometry||{},sc=g.scale===undefined?D.project.width/source[c.source].width:g.scale;
 layer.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0,0]);layer.property("ADBE Transform Group").property("ADBE Position").setValue([g.tx||0,g.ty||0]);layer.property("ADBE Transform Group").property("ADBE Scale").setValue([100*sc,100*sc]);
 layer.comment="Source mapping is a measured candidate, not original project metadata.";
}
var componentCount=0,editableCount=0;
for(var i=0;i<D.components.length;i++){
 var r=D.components[i],item=importFile(asset(r.path));item.parentFolder=folder;
 try {item.mainSource.alphaMode=AlphaMode.STRAIGHT;}catch(ignore){}
 var layer=comp.layers.add(item);layer.name=r.id;layer.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0,0]);layer.property("ADBE Transform Group").property("ADBE Position").setValue(r.position);at(layer,r.in,r.out);componentCount++;
 if(r.character_reveal_state)layer.comment="Explicit raster reveal state "+r.character_reveal_state.state_index+" of "+r.event_id+"; fixed full-text canvas. Edit text/timing in source JSON and re-export. No native editable text alternative.";
 if(r.editable_text){
  var e=r.editable_text,f=D.fonts[e.font],text=comp.layers.addText(e.text);text.name=r.event_id+" [EDIT TEXT - candidate font]";at(text,r.in,r.out);
  var prop=text.property("ADBE Text Properties").property("ADBE Text Document"),doc=prop.value;doc.resetCharStyle();doc.font=f.postscript_name;doc.fontSize=e.font_size;doc.tracking=e.tracking_1000em;doc.applyFill=true;doc.fillColor=e.fill;doc.applyStroke=false;doc.justification=ParagraphJustification.LEFT_JUSTIFY;prop.setValue(doc);
  var box=text.sourceRectAtTime(r.in,false),ink=e.ink_box;if(box.width>0&&box.height>0){text.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([box.left,box.top]);text.property("ADBE Transform Group").property("ADBE Scale").setValue([100*(ink[2]-ink[0])/box.width,100*(ink[3]-ink[1])/box.height]);text.property("ADBE Transform Group").property("ADBE Position").setValue([r.position[0]+ink[0],r.position[1]+ink[1]]);}
  text.enabled=false;text.comment="Install fonts/"+f.postscript_name+".ttf; then enable this text and disable "+r.id+" PNG. Text shaping/anti-aliasing may differ. Original font unconfirmed.";editableCount++;
  try {if(app.fonts.getFontsByPostScriptName(f.postscript_name).length===0)warn("Font not installed: "+f.postscript_name);}catch(ignore){warn("Verify candidate font availability: "+f.postscript_name);}
 }
}
// Raw audio stays independently editable, using subframe layer in/out seconds.
var audioCount=0;
for(var i=0;i<D.project.audio.length;i++){
 var a=D.project.audio[i],layer=comp.layers.add(source[a.source]);layer.name=a.id+" [raw audio; subframe cut]";layer.enabled=false;layer.audioEnabled=true;layer.startTime=a.in-a.source_in;at(layer,a.in,a.out);
 var gain=(a.gain_db||0)+(D.audio.master_attenuation_db||0);layer.property("ADBE Audio Group").property("ADBE Audio Levels").setValue([gain,gain]);layer.comment="Candidate waveform mapping; source_in="+a.source_in+"; source_out="+(a.source_in+a.out-a.in);audioCount++;
}
comp.comment="DRAFT: "+D.project.clips.length+" video clips; "+componentCount+" PNG/box components; "+editableCount+" disabled editable-text alternatives; "+audioCount+" raw audio clips. Native map and fonts are candidates. Compare with reference after import.";
if(componentCount!==D.components.length||editableCount!==__EDITABLE__||audioCount!==D.project.audio.length)throw new Error("Layer completeness mismatch");
comp.openInViewer();
alert("Atrass draft imported. Components: "+componentCount+", editable text: "+editableCount+", raw audio: "+audioCount+". PNG glyphs are enabled; editable text alternatives are disabled.\n"+warnings.join("\n"));
} catch(err) { alert("Atrass import stopped: "+err.toString()); throw err; }
finally {app.endUndoGroup();}
}());
'''
    data=copy.deepcopy(data)
    for inf in data['timeline']['source_metadata'].values():inf['fps_value']=float(Fraction(inf['fps']))
    script=script.replace('__DATA__',json.dumps(data,ensure_ascii=False,separators=(',',':'))).replace('__FPS__',str(float(Fraction(p['fps'])))).replace('__EDITABLE__',str(sum(bool(r['editable_text']) for r in comps)))
    # Python triple-quoted template must preserve JavaScript newline escapes.
    script=script.replace('disabled.\n"','disabled.\\n"').replace('warnings.join("\n")','warnings.join("\\n")')
    atomic(out/'after-effects.jsx',script)


def audit(p,t,comps,out,project_root=None):
    tree=ET.parse(out/'premiere.xml');seq=tree.getroot().find('sequence');base=seq.findall('./media/video/track')[0];clips=base.findall('clipitem');expanded=[]
    for c in clips:
        a,b,si,so=[int(c.findtext(k)) for k in ('start','end','in','out')]
        if b-a!=so-si:raise ValueError('XML run source/target frame lengths differ')
        source_id=c.find('file').get('id')
        expanded += [(a+j,si+j,source_id) for j in range(b-a)]
    expected=[(r['frame'],r['source_frame'],'file-'+safe(r['source'])) for r in t['frames']]
    if expanded!=expected:raise ValueError('XML source-frame mapping differs from renderer')
    layerclips=seq.findall('./media/video/track')[1:]
    if len(layerclips)!=len(comps):raise ValueError('XML layer loss')
    for r,tr in zip(comps,layerclips):
        c=tr.find('clipitem')
        if c.findtext('name')!=r['id'] or [int(c.findtext(k)) for k in ('start','end')]!=[frame(r['in'],Fraction(p['fps'])),frame(r['out'],Fraction(p['fps']))]:raise ValueError('XML layer timing differs')
        if (c.findtext('./file/pathurl')!=(out/r['canvas_path']).resolve().as_uri() or
            c.findtext('stillframe')!='TRUE' or c.findtext('alphatype')!='straight'):
            raise ValueError('XML component asset/alpha differs '+r['id'])
    reveal_audit=audit_reveals(p,comps,out,project_root)
    for el in tree.findall('.//file/pathurl'):
        from urllib.parse import unquote,urlparse
        if not Path(unquote(urlparse(el.text).path)).is_file():raise ValueError('XML missing media '+el.text)
    node=shutil.which('node');syntax=False
    if node:
        with tempfile.TemporaryDirectory(prefix='jsx-check-') as td:
            f=Path(td)/'check.js';f.write_text((out/'after-effects.jsx').read_text(encoding='utf-8').replace('#target aftereffects',''), encoding='utf-8');subprocess.run([node,'--check',str(f)],check=True);syntax=True
    return {'structural_pass':True,'xml_well_formed':True,'xml_raw_frame_expansion_exact':True,'raw_output_frames_verified':len(expanded),'xml_base_clipitems':len(clips),'xml_component_tracks':len(comps),'logical_original_layers_accounted_for':len(set(r['event_id'] for r in comps)),'split_layer_alpha_recomposition_exact':True,**reveal_audit,'audio_events_accounted_for':len(p.get('audio',[])),'ae_editable_text_alternatives':sum(bool(r['editable_text']) for r in comps),'jsx_javascript_syntax_checked':syntax,'application_import_verified':False,'visual_match_after_import_verified':False,'complete_automation_release':False}


def export(project,output,ffmpeg,stems=False):
    project=Path(project).resolve();out=Path(output).resolve();root=project.parent;p=json.loads(project.read_text(encoding='utf-8'));p.setdefault('audio',[]);renderer.validate(p,root,True);preflight(p)
    if out.exists() and any(out.iterdir()):raise ValueError('Refusing to overwrite non-empty export folder '+str(out))
    out.mkdir(parents=True,exist_ok=True);fontmap=fonts(p,root,out);comps=components(p,root,out);t=timeline(p,root);ast=audio(p,root,out,ffmpeg,stems)
    fcp_xml(p,t,comps,out);ae_jsx(p,t,comps,fontmap,ast,out);validation=audit(p,t,comps,out,root)
    sidecar={'schema':'atrass-nle-export-v1','project_path':str(project),'project_sha256':renderer.sha(project),'exporter_sha256':renderer.sha(__file__),'source_timeline':t,'components':comps,'fonts':fontmap,'audio_events':p.get('audio',[]),'audio_mix':ast,'validation':validation,'limitations':['FCP7 Basic Motion placement and mixed-rate source frame addressing must be checked in Premiere; no actual Premiere import yet.','FCP uses frame-quantized visual intervals and separate full-canvas PNG glyph/box tracks. Text strings are editable only in AE alternatives.','AE uses HOLD remap keys for source frames, subframe raw audio clips, candidate static fonts; disabled editable text may rasterize differently.','AE raw-audio decoding may differ from FFmpeg sample-aligned WAV; XML uses the supplied raw-only mix.','Original font and edit project are unconfirmed. New footage editing decisions are not learned by reproducing this source map.','XML paths are absolute; rerun exporter after moving source media. WAV mix/stems are regenerable and may be omitted from Git.']}
    sidecar['limitations'][1]='FCP uses frame-quantized visual intervals and separate full-canvas PNG tracks. Static glyph components have disabled AE text alternatives; character reveals are explicit fixed-canvas raster states, without native editable text. Edit source JSON and re-export.'
    sidecar['limitations'].append('Character reveal support is static geometry plus sampled per-codepoint alpha only. Every output-frame change is a PNG state; fades may create one state per frame. Parent/child intervals are half-open. Rotation, dynamic opacity and other keyframes remain unsupported. This does not identify the original NLE effect or font.')
    atomic(out/'nle-manifest.json',sidecar);atomic(out/'validation.json',validation)
    txt='아트라상 NLE 초안 내보내기\n\nPremiere: premiere.xml 가져오기. 원본 이동 시 같은 CLI로 다시 생성합니다. 글자 PNG와 검정 상자는 별도 트랙이며 위치/시간 편집 가능합니다. PNG의 문장을 직접 바꾸지는 못합니다.\nAfter Effects: File > Scripts > Run Script File로 after-effects.jsx 실행. 기존 프로젝트를 닫거나 저장하지 않고 새 폴더/컴프를 추가합니다. 기본은 렌더러와 같은 투명 글자 PNG입니다. fonts/의 후보 폰트를 설치한 후 [EDIT TEXT] 레이어를 켜고 대응 glyph PNG를 끄면 문장을 수정할 수 있습니다. 폰트는 원본 확정이 아니며 AE 래스터 차이가 남습니다.\n영상: 원본 배열 반복/스킵은 XML 프레임 clipitem 및 AE HOLD time-remap으로 명시 보존했습니다.\n음성: XML은 원본만으로 만든48k PCM24 mix, AE는 원본의 각 구간을 ms in/out으로 분리합니다. --audio-stems 옵션은 추가 WAV를 재생성합니다. 믹스와 앱의 디코더 차이는 실제 청취 비교가 필요합니다.\n검증: XML 구조·전체 source-frame 전개·레이어 수·자막 시간·PNG 재합성·JS 구문검사. 실제 앱 가져오기/시각 비교 결과는 validation.json에서 별도로 확인합니다.\n\n재생성 CLI:\npython engine/export_nle.py projects/'+project.name+' --output-dir exports/'+out.name+' --ffmpeg /path/to/ffmpeg\n기존 폴더는 덮어쓰지 않으므로 새 output-dir을 지정하십시오.\n'
    txt+='\n글자별 등장(character_reveal): 전체 문장의 고정 캔버스를 유지한 상태별 PNG입니다. 각 상태의 위치·시간은 편집할 수 있지만 문장 자체는 AE 문자 레이어가 아닙니다. 소스 JSON의 text/timing을 바꾸고 새 폴더로 재생성하십시오. 투명한 초기 상태와 마지막 완성 상태도 같은 부모 프레임 범위 안에 보존합니다. 코드포인트 단위이며 회전·이동·전체 투명도 키프레임은 지원하지 않습니다. 프레임마다 알파가 바뀌면 PNG/트랙 수가 늘어납니다.\n'
    txt=txt.replace('projects/'+project.name,str(project)).replace('exports/'+out.name,str(out)+'-regenerated')
    atomic(out/'사용법.txt',txt);print(json.dumps(validation,ensure_ascii=False));return sidecar

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('project');ap.add_argument('--output-dir',required=True);ap.add_argument('--ffmpeg',default=os.environ.get('ATRASS_FFMPEG','ffmpeg'));ap.add_argument('--audio-stems',action='store_true');a=ap.parse_args()
    try:export(a.project,a.output_dir,a.ffmpeg,a.audio_stems)
    except Exception as exc:print(str(exc),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
