#!/usr/bin/env python3
"""Deterministic Atrass timeline renderer. Source footage and editable overlays only.

python engine/render.py projects/example.json --output render.mp4 --draft
Dependencies: av, numpy, Pillow, opencv-python-headless; FFmpeg executable.
Times are half-open seconds. Coordinates are in the declared canvas space.
A draft render is a working candidate, never a certified style match.
"""
from __future__ import annotations
import argparse, bisect, hashlib, json, math, os, subprocess, sys, tempfile, time
from collections import OrderedDict
from fractions import Fraction
from pathlib import Path
import av
import cv2
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont
from character_reveal import CharacterReveal, RevealTiming
from audio_policy import validate_audio_policy
from source_quantization import SourceClockCache, reconstruct_rate1_window
cv2.setNumThreads(2)

def rgb(c):
    if isinstance(c,str): return ImageColor.getrgb(c)
    return tuple(c)
def rgba(c):
    v=rgb(c);return v if len(v)==4 else (*v,255)
def sha(p):
    q=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(2**20),b''):q.update(b)
    return q.hexdigest()
def resolve(root,p):
    p=Path(p);return p if p.is_absolute() else (root/p).resolve()
def finite(x):return isinstance(x,(int,float)) and math.isfinite(x)
def evaluate_keys(keys,t,default):
    if not keys:return default
    if t<=keys[0][0]:return keys[0][1]
    if t>=keys[-1][0]:return keys[-1][1]
    i=bisect.bisect_right([x[0] for x in keys],t)-1
    a,b=keys[i:i+2];u=(t-a[0])/(b[0]-a[0])
    easing=b[2] if len(b)>2 else 'linear'
    if easing=='hold':u=0
    elif easing=='smoothstep':u=u*u*(3-2*u)
    elif easing!='linear':raise ValueError('Unsupported easing '+easing)
    return a[1]+(b[1]-a[1])*u

def layer_blend_mode(e):
    """Only image overlays support Screen; all existing layers default to normal."""
    mode=e.get('blend_mode','normal')
    if mode not in ('normal','screen'):raise ValueError('Unsupported layer blend_mode '+str(mode))
    if mode=='screen':
        if e.get('kind')!='image':raise ValueError('Screen blend_mode requires an image layer')
        # The existing image transform contract uses scale_keys, not static scale.
        if any(k in e for k in ('scale','scale_x','scale_y','geometry')):raise ValueError('Screen image geometry uses crop/size/scale_keys/rotation/position; static scale axes or geometry dict are unsupported')
    return mode

def screen_composite(out,im,xy):
    """Screen on the opaque RGB compositor canvas, in encoded RGB, not linear light.

    `im` already contains crop/resize/rotation and quantized opacity/fades from
    Composer.image. Keep that shared alpha contract; do not apply opacity twice.
    RGB_out = RGB_back + (255-RGB_back)*RGB_image*alpha/(255*255).
    This is a rendering candidate, not a claim about the reference's authored blend.
    """
    x,y=xy;left=max(0,x);top=max(0,y);right=min(out.width,x+im.width);bottom=min(out.height,y+im.height)
    if right<=left or bottom<=top:return
    region=(left,top,right,bottom)
    back=np.array(out.crop(region),dtype=np.uint8)
    src=np.asarray(im.crop((left-x,top-y,right-x,bottom-y)),dtype=np.float64)
    b=back[...,:3].astype(np.float64)
    back[...,:3]=np.rint(b+(255-b)*src[...,:3]*src[...,3:4]/65025).clip(0,255).astype(np.uint8)
    # compose() starts opaque; alpha stays opaque through all normal layers.
    out.paste(Image.fromarray(back),region)

def validate(p,root,draft):
    errors=[]
    for k in ('width','height','duration','fps','media','clips','layers'): 
        if k not in p:errors.append('Missing '+k)
    if errors:raise ValueError('; '.join(errors))
    if any(not isinstance(p[k],int) or p[k]<=0 or p[k]%2 for k in ('width','height')):errors.append('Canvas dimensions must be positive even integers')
    try:
        if Fraction(p['fps'])<=0:errors.append('FPS must be positive')
    except (ValueError,ZeroDivisionError,TypeError):errors.append('Invalid FPS')
    if not finite(p['duration']) or p['duration']<=0:errors.append('Invalid duration')
    for k,m in p['media'].items():
        if 'blend_mode' in m:errors.append('blend_mode belongs to image layers, not media')
        path=resolve(root,m['path'])
        if not path.is_file():errors.append('Missing media '+k+': '+str(path))
    for k,f in p.get('fonts',{}).items():
        if 'blend_mode' in f:errors.append('blend_mode is not supported on fonts '+k)
        if not resolve(root,f['path']).is_file():errors.append('Missing font '+k)
        if not draft and f.get('status')!='verified':errors.append('Unverified font '+k)
    if 'blend_mode' in p:errors.append('blend_mode belongs to image layers, not the project')
    validate_audio_policy(p)
    clips=sorted(p['clips'],key=lambda c:c['in'])
    prev=0
    for c in clips:
        if 'blend_mode' in c:errors.append('blend_mode is not supported on base clips')
        if 'blend_mode' in c.get('geometry',{}):errors.append('blend_mode is not supported in clip geometry')
        if not all(finite(c.get(k)) for k in ('in','out','source_in')):errors.append('Nonfinite clip times');continue
        if c['out']<=c['in']:errors.append('Reversed/empty clip '+c['id'])
        if c['source_in']<0:errors.append('Negative source time '+c['id'])
        if not finite(c.get('rate',1)) or c.get('rate',1)<=0:errors.append('Invalid clip speed '+c['id'])
        if c.get('source_frame_times') is not None:
            times=c['source_frame_times'];required=math.ceil((c['out']-c['in'])*float(Fraction(p['fps']))-1e-7)
            if len(times)!=required or not all(finite(v) and v>=0 for v in times):errors.append('Invalid native source frame map '+c['id'])
        if abs(c['in']-prev)>0.002:errors.append('Base clip gap/overlap at '+str(c['in']))
        prev=c['out']
        if c['source'] not in p['media']:errors.append('Unknown source '+c['source'])
        elif p['media'][c['source']].get('role')=='reference':errors.append('Finished reference cannot be rendered as raw source')
        if not draft and c.get('status')!='verified':errors.append('Unverified source mapping '+c['id'])
    if abs(prev-p['duration'])>0.002:errors.append('Base clips do not cover duration')
    layer_ids=set()
    for e in p['layers']:
        if e.get('id') in layer_ids:errors.append('Duplicate layer ID '+str(e.get('id')))
        layer_ids.add(e.get('id'))
        if isinstance(e.get('geometry'),dict) and 'blend_mode' in e['geometry']:errors.append('blend_mode belongs to the image layer, not its geometry')
        try:layer_blend_mode(e)
        except ValueError as exc:errors.append(str(exc))
        if not all(finite(e.get(k)) for k in ('in','out')) or e['out']<=e['in']:
            errors.append('Invalid layer interval '+e.get('id',''));continue
        if e.get('kind') not in ('text','caption','label','rect','image','gradient','chat','polyline'):errors.append('Unsupported layer kind '+str(e.get('kind')))
        if e['in']<0 or e['out']>p['duration']+.002:errors.append('Layer outside timeline '+e.get('id',''))
        if e.get('font') and e['font'] not in p.get('fonts',{}):errors.append('Unknown font '+e['font'])
        if e.get('character_reveal') is not None:
            if e.get('kind')!='text':errors.append('Character reveal requires an independent text layer')
            if any(k in e for k in ('box','background','glyph_width','glyph_height')):errors.append('Character reveal uses its fixed logical canvas; put backplates in separate layers')
            try:RevealTiming(**e['character_reveal'])
            except (TypeError,ValueError) as exc:errors.append('Invalid character reveal '+str(exc))
        if e.get('kind')=='image':
            if e.get('source') not in p['media']:errors.append('Unknown layer image source '+str(e.get('source')))
            elif p['media'][e['source']].get('role')=='reference':errors.append('Finished reference cannot be pasted as an image layer')
        if not draft and e.get('status')!='verified':errors.append('Unverified layer '+e['id'])
    for a in p.get('audio',[]):
        if 'blend_mode' in a:errors.append('blend_mode is not supported on audio')
        if isinstance(a.get('geometry'),dict) and 'blend_mode' in a['geometry']:errors.append('blend_mode is not supported inside audio geometry')
        if not all(finite(a.get(k)) for k in ('in','out','source_in')):
            errors.append('Invalid audio times '+a.get('id',''));continue
        if not 0<=a['in']<a['out']<=p['duration']+.002 or a['source_in']<0:errors.append('Audio outside timeline/source '+a.get('id',''))
        if not finite(a.get('rate',1)) or not .5<=a.get('rate',1)<=2:errors.append('Invalid audio speed '+a.get('id',''))
        if a['source'] not in p['media']:errors.append('Unknown audio source '+a['source'])
        elif p['media'][a['source']].get('role')=='reference':errors.append('Finished reference cannot be rendered as raw audio')
        if not draft and a.get('status')!='verified':errors.append('Unverified audio '+a.get('id',''))
    if not draft and p.get('release_ready') is not True:errors.append('Project is not release-ready')
    if errors:raise ValueError('\n'.join(errors))

class SourceReader:
    def __init__(self,path):
        self.container=av.open(str(path));self.stream=self.container.streams.video[0]
        self.stream.thread_type='SLICE';self.stream.codec_context.thread_count=2
        self.it=None;self.prev=None;self.next=None;self.last_request=None;self.exhausted=False
        self.end_seconds=(float((self.stream.start_time or 0)+self.stream.duration)*float(self.stream.time_base)
                          if self.stream.duration is not None else None)
    def get(self,t):
        if not finite(t) or t<0:raise ValueError('Invalid source video time')
        if self.end_seconds is not None and t>=self.end_seconds-1e-9:
            raise ValueError('Source video time is outside its declared coverage: '+str(t))
        if self.it is None or (self.last_request is not None and (t<self.last_request-.00001 or t-self.last_request>1.0)):
            self.container.seek(max(0,int(t/self.stream.time_base)),stream=self.stream,backward=True,any_frame=False)
            self.it=iter(self.container.decode(self.stream));self.prev=None;self.next=None;self.exhausted=False
        self.last_request=t
        if self.next is None and not self.exhausted:
            try:self.next=next(self.it)
            except StopIteration:self.exhausted=True
        while self.next is not None and float(self.next.pts*self.next.time_base)<t:
            self.prev=self.next
            try:self.next=next(self.it)
            except StopIteration:self.next=None;self.exhausted=True
        candidates=[v for v in (self.prev,self.next) if v is not None]
        if not candidates:raise ValueError('Empty video source')
        if self.exhausted and self.prev is not None:
            last=self.prev
            span=float(last.duration*last.time_base) if last.duration else 1/float(self.stream.average_rate)
            if t>=float(last.pts*last.time_base)+span-1e-9:
                raise ValueError('Source video time exceeds the last decoded frame coverage: '+str(t))
        chosen=min(candidates,key=lambda v:abs(float(v.pts*v.time_base)-t))
        return chosen.to_ndarray(format='rgb24'),float(chosen.pts*chosen.time_base)
    def close(self):self.container.close()

class Composer:
    def __init__(self,p,root):
        self.p=p;self.root=root;self.w=p['width'];self.h=p['height'];self.cache=OrderedDict();self.fontcache={};self.revealcache={}
    def character_layer(self,e):
        if e['id'] not in self.revealcache:
            self.revealcache[e['id']]=CharacterReveal(e.get('text',''),self.font(e),fill=e.get('fill','#FFFFFF'),tracking=e.get('tracking',0),line_gap=e.get('line_gap',18),align=e.get('align','center'),padding=e.get('padding',0),stroke_width=e.get('stroke_width',0),stroke_fill=e.get('stroke_fill'))
        return self.revealcache[e['id']]
    def font(self,e):
        k=e['font'];f=self.p['fonts'][k];size=int(e.get('font_size',64));weight=e.get('weight',f.get('weight'))
        key=(k,size,weight)
        if key not in self.fontcache:
            font=ImageFont.truetype(str(resolve(self.root,f['path'])),size,index=f.get('index',0))
            if weight is not None:
                try:font.set_variation_by_axes([weight])
                except OSError:pass
            self.fontcache[key]=font
        return self.fontcache[key]
    def text_mask(self,text,e):
        f=self.font(e);tracking=e.get('tracking',0);stroke=e.get('stroke_width',0)
        b=f.getbbox(text,stroke_width=stroke);w=max(1,int(math.ceil(f.getlength(text)+max(0,len(text)-1)*tracking+stroke*2+12)))
        mask=Image.new('L',(w,max(1,b[3]-b[1]+12)),0);d=ImageDraw.Draw(mask)
        if tracking:
            x=stroke+4
            for ch in text:
                d.text((x,4-b[1]),ch,font=f,fill=255,stroke_width=stroke,stroke_fill=255);x+=f.getlength(ch)+tracking
        else:d.text((stroke+4,4-b[1]),text,font=f,fill=255,stroke_width=stroke,stroke_fill=255)
        box=mask.getbbox()
        if box:mask=mask.crop(box)
        gh=e.get('glyph_height');gw=e.get('glyph_width')
        if gh:
            r=gh/mask.height;mask=mask.resize((max(1,round(mask.width*r)),max(1,round(gh))),Image.Resampling.LANCZOS)
        if gw:mask=mask.resize((max(1,round(gw)),mask.height),Image.Resampling.LANCZOS)
        elif e.get('scale_x'):mask=mask.resize((max(1,round(mask.width*e['scale_x'])),mask.height),Image.Resampling.LANCZOS)
        return mask
    def text_image(self,e):
        lines=e.get('text','').split('\n');linegap=e.get('line_gap',18);masks=[self.text_mask(s,e) for s in lines]
        tw=max(m.width for m in masks);th=sum(m.height for m in masks)+linegap*(len(masks)-1)
        pad=e.get('padding',[0,0]);px,py=pad if isinstance(pad,list) else (pad,pad)
        box=e.get('box');bw=box[2]-box[0] if box else tw+2*px;bh=box[3]-box[1] if box else th+2*py
        out=Image.new('RGBA',(max(1,round(bw)),max(1,round(bh))),(0,0,0,0))
        if e.get('background'):
            ImageDraw.Draw(out).rounded_rectangle((0,0,out.width-1,out.height-1),radius=e.get('radius',0),fill=rgba(e['background']))
        x=e.get('text_offset',[None,None])[0];y=e.get('text_offset',[None,None])[1]
        y=round((bh-th)/2) if y is None else y
        for m in masks:
            x0=round((bw-m.width)/2) if x is None else x
            if e.get('align')=='left' and x is None:x0=px
            shadow=e.get('shadow')
            if shadow:
                sm=m.filter(ImageFilter.GaussianBlur(shadow.get('blur',2)))
                si=Image.new('RGBA',m.size,rgba(shadow.get('color','#000000')));si.putalpha(sm.point(lambda v:round(v*shadow.get('opacity',.7))))
                out.alpha_composite(si,(round(x0+shadow.get('dx',2)),round(y+shadow.get('dy',2))))
            ti=Image.new('RGBA',m.size,rgba(e.get('fill','#ffffff')));ti.putalpha(m)
            out.alpha_composite(ti,(round(x0),round(y)));y+=m.height+linegap
        return out
    def build_static(self,e):
        kind=e['kind']
        if e.get('character_reveal') is not None:return self.character_layer(e).static_image()
        if kind in ('text','caption','label'):return self.text_image(e)
        if kind=='rect':
            size=e['size'];im=Image.new('RGBA',tuple(map(round,size)),(0,0,0,0));ImageDraw.Draw(im).rounded_rectangle((0,0,im.width-1,im.height-1),radius=e.get('radius',0),fill=rgba(e['fill']),outline=rgba(e['stroke']) if e.get('stroke') else None,width=e.get('stroke_width',1));return im
        if kind=='image':
            source=self.p['media'][e['source']];im=Image.open(resolve(self.root,source['path'])).convert('RGBA')
            if e.get('crop'):im=im.crop(tuple(e['crop']))
            if e.get('size'):im=im.resize(tuple(map(round,e['size'])),Image.Resampling.LANCZOS)
            return im
        if kind=='gradient':
            w,h=map(round,e.get('size',[self.w,self.h]));stops=e['stops'];arr=np.zeros((h,w,4),dtype=np.uint8)
            yy=np.linspace(0,1,h)
            for k in range(4):arr[:,:,k]=np.interp(yy,[s[0] for s in stops],[rgba(s[1])[k] for s in stops])[:,None]
            return Image.fromarray(arr)
        if kind=='chat':
            w,h=map(round,e['size']);im=Image.new('RGBA',(w,h),rgba(e.get('background','#B9CDE0')));d=ImageDraw.Draw(im)
            for b in e['bubbles']:
                be={**e,**b,'kind':'text','background':b.get('background','#FFFFFF'),'padding':b.get('padding',[18,14]),'fill':b.get('fill','#000000'),'radius':b.get('radius',18)}
                bubble=self.text_image(be);x,y=map(round,b['position']);im.alpha_composite(bubble,(x,y))
                side=b.get('side','left');xx=x if side=='left' else x+bubble.width;points=[(xx,y+15),(xx+(-10 if side=='left' else 10),y+15),(xx,y+28)];d.polygon(points,fill=rgba(be['background']))
            return im
        if kind=='polyline':
            w,h=map(round,e['size']);im=Image.new('RGBA',(w,h),(0,0,0,0));d=ImageDraw.Draw(im);d.line([tuple(q) for q in e['points']],fill=rgba(e['fill']),width=e.get('width',10),joint='curve');return im
        raise ValueError('Unsupported layer kind '+kind)
    def image(self,e,t):
        key=e['id']
        if key not in self.cache:
            self.cache[key]=self.build_static(e)
            if len(self.cache)>40:self.cache.popitem(last=False)
        else:self.cache.move_to_end(key)
        im=self.cache[key];local=t-e['in'];opacity=evaluate_keys(e.get('opacity_keys'),local,e.get('opacity',1))
        if e.get('character_reveal') is not None:im=self.character_layer(e).render(local,timing=RevealTiming(**e['character_reveal']))
        if e.get('fade_in'):opacity*=min(1,local/e['fade_in'])
        if e.get('fade_out'):opacity*=min(1,(e['out']-t)/e['fade_out'])
        scale=evaluate_keys(e.get('scale_keys'),local,1);rotation=evaluate_keys(e.get('rotation_keys'),local,e.get('rotation',0))
        if scale!=1:im=im.resize((max(1,round(im.width*scale)),max(1,round(im.height*scale))),Image.Resampling.BICUBIC)
        if rotation:im=im.rotate(-rotation,resample=Image.Resampling.BICUBIC,expand=True)
        if opacity!=1:im=im.copy();im.putalpha(im.getchannel('A').point(lambda v:round(v*max(0,min(1,opacity)))))
        if e.get('reveal_keys'):
            fraction=evaluate_keys(e['reveal_keys'],local,1);mask=Image.new('L',im.size);ImageDraw.Draw(mask).rectangle((0,0,round(im.width*fraction),im.height),fill=255);im=im.copy();im.putalpha(Image.fromarray(np.minimum(np.array(im.getchannel('A')),np.array(mask))))
        if e.get('box'):x,y=e['box'][:2]
        else:x,y=e.get('position',[0,0])
        x=evaluate_keys(e.get('x_keys'),local,x);y=evaluate_keys(e.get('y_keys'),local,y)
        if e.get('anchor')=='center':x-=im.width/2;y-=im.height/2
        elif e.get('anchor')=='top_center':x-=im.width/2
        return im,(round(x),round(y))
    def compose(self,base,layers,t):
        out=Image.fromarray(base).convert('RGBA')
        for e in sorted(layers,key=lambda e:e.get('z',0)):
            mode=layer_blend_mode(e)
            im,xy=self.image(e,t)
            if mode=='normal':out.alpha_composite(im,xy)
            else:screen_composite(out,im,xy)
        return np.asarray(out.convert('RGB'))

def render_audio(p,root,start,end,ffmpeg,tmp):
    sr=48000;n=round((end-start)*sr);mix=np.zeros((n,2),dtype=np.float32);used=False;sample_windows=[];source_clock_cache=SourceClockCache(ffmpeg,tmp)
    # FFmpeg seeks each clip directly. This avoids decoding/caching a multi-GB whole source.
    for k,a in enumerate(p.get('audio',[])):
        lo=max(start,a['in']);hi=min(end,a['out'])
        if hi<=lo:continue
        src=resolve(root,p['media'][a['source']]['path']);rate=a.get('rate',1)
        source_start=a['source_in']+(lo-a['in'])*rate;source_duration=(hi-lo)*rate
        filt=[]
        if abs(rate-1)>.0000001:
            if not .5<=rate<=2:raise ValueError('Audio rate outside supported atempo range')
            filt.append('atempo='+str(rate))
        # Input duration is measured before atempo; an output -t would truncate slow audio.
        cmd=[ffmpeg,'-v','error','-threads','2','-ss',f'{source_start:.9f}','-t',f'{source_duration:.9f}','-i',str(src),'-vn','-ac','2','-ar',str(sr)]
        if filt:cmd+=['-af',','.join(filt)]
        cmd+=['-f','f32le','pipe:1'];b=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
        arr=np.frombuffer(b,np.float32).reshape(-1,2).copy()
        # Shared output sample grid: adjacent half-open intervals share one boundary.
        # Do not replace += mixing: deliberately overlapping tracks still add.
        begin=max(0,min(n,round((lo-start)*sr)));finish=max(begin,min(n,round((hi-start)*sr)));count=finish-begin
        decoded_count=len(arr)
        reconstruction=None
        if decoded_count<count:
            if p.get('audio_master',{}).get('sample_underflow_policy','strict')!='bounded_linear_rate1_one_sample' or rate!=1 or count-decoded_count!=1:
                raise ValueError('Decoded audio shorter than half-open sample window for '+str(a.get('id',k))+': need '+str(count)+', got '+str(decoded_count)+'; no implicit silence padding')
            arr,reconstruction=reconstruct_rate1_window(source_clock_cache.path_for(src,p['media'][a['source']].get('sha256')),source_start=source_start,source_duration=source_duration,output_lo=lo,output_hi=hi,render_start=start,rate=rate,decoded_samples=decoded_count,target_samples=count,sample_rate=sr)
        sample_windows.append({'id':a.get('id',str(k)),'begin':begin,'end':finish,'target_samples':count,'decoded_samples':decoded_count,'trimmed_tail_samples':max(0,decoded_count-count),'source_start':source_start,'source_duration':source_duration,'rate':rate,'zero_sample_window':count==0})
        if reconstruction is not None:
            reconstruction['source_cache_evidence']=source_clock_cache.evidence_for(src)
            sample_windows[-1]['bounded_source_reconstruction']=reconstruction
        arr=arr[:count];gain=10**(a.get('gain_db',0)/20);arr*=gain
        fadein=a.get('fade_in',0);fadeout=a.get('fade_out',0);times=np.arange(count)/sr+lo
        if fadein:arr*=np.clip((times-a['in'])/fadein,0,1)[:,None]
        if fadeout:arr*=np.clip((a['out']-times)/fadeout,0,1)[:,None]
        mix[begin:finish]+=arr;used=True
    if not used:return None,{'present':False}
    peak=float(np.max(np.abs(mix)));clipped=int(np.count_nonzero(np.abs(mix)>1));ceiling=p.get('audio_master',{}).get('sample_peak_ceiling_dbfs');attenuation=1.0
    if ceiling is not None and peak>10**(ceiling/20):attenuation=10**(ceiling/20)/peak;mix*=attenuation
    audio=tmp/'mixed.f32';mix.tofile(audio)
    return audio,{'present':True,'sample_grid_policy':'shared-output-relative-endpoints-v1','piece_sample_windows':sample_windows,'implicit_silence_padding':False,'sample_rate':sr,'channels':2,'samples':n,'peak_linear':peak,'samples_over_full_scale_before_master':clipped,'peak_linear_after_master':float(np.max(np.abs(mix))),'master_attenuation_db':20*math.log10(attenuation),'limiter_applied':False,'sample_peak_ceiling_is_not_true_peak_guarantee':True}

def render(project,output,draft=False,start=0,end=None,width=None,fps_override=None,ffmpeg='ffmpeg'):
    project=Path(project).resolve();root=project.parent;p=json.loads(project.read_text(encoding='utf-8'));validate(p,root,draft)
    start=float(start);end=min(p['duration'],float(end) if end is not None else p['duration'])
    if not 0<=start<end<=p['duration']:raise ValueError('Invalid render range')
    output=Path(output).resolve()
    if output.exists():raise ValueError('Output exists; use a new path: '+str(output))
    output.parent.mkdir(parents=True,exist_ok=True)
    fps=Fraction(fps_override or p['fps']);w=width or p['width'];h=round(p['height']*w/p['width']/2)*2
    if w%2:raise ValueError('Output width must be even')
    readers={};still_images={};comp=Composer(p,root);clips=sorted(p['clips'],key=lambda e:e['in']);idx=0
    total=math.ceil((end-start)*float(fps)-1e-9);log=[];begin=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='atrass-render-',dir=output.parent) as folder:
        tmp=Path(folder)
        # Resolve and validate the complete audio first. A source-window error
        # must fail before an expensive video pass is encoded and discarded.
        audio,audiostat=render_audio(p,root,start,end,ffmpeg,tmp)
        video=tmp/'video.mp4';out=av.open(str(video),'w');stream=out.add_stream('libx264',rate=fps);stream.width=w;stream.height=h;stream.pix_fmt='yuv420p';stream.options={'crf':'18','preset':'fast','threads':'2'}
        stream.codec_context.color_primaries=1;stream.codec_context.color_trc=1;stream.codec_context.colorspace=1;stream.codec_context.color_range=1
        try:
            for i in range(total):
                t=start+float(Fraction(i,1)/fps)
                while idx+1<len(clips) and clips[idx]['out']<=t+1e-9:idx+=1
                c=clips[idx]
                if not c['in']-1e-8<=t<c['out']+1e-8:raise ValueError('No source clip at '+str(t))
                mid=c['source'];src=resolve(root,p['media'][mid]['path']);local=t-c['in']
                st=c['source_in']+(0 if c.get('freeze_source') else local*c.get('rate',1))
                if p['media'][mid].get('type')=='image':
                    if mid not in still_images:still_images[mid]=np.asarray(Image.open(src).convert('RGB'))
                    arr=still_images[mid];chosen=None
                else:
                    if mid not in readers:readers[mid]=SourceReader(src)
                    # Optional native mapping preserves measured holds/skips and phase.
                    if c.get('source_frame_times') is not None:
                        native_index=round(local*float(Fraction(p['fps'])))
                        if not 0<=native_index<len(c['source_frame_times']):raise ValueError('Source frame map does not cover clip '+c['id'])
                        st=c['source_frame_times'][native_index]
                    arr,chosen=readers[mid].get(st)
                geo=c.get('geometry',{});scale=evaluate_keys(geo.get('scale_keys'),local,geo.get('scale',p['width']/arr.shape[1]));sx=geo.get('scale_x',scale);sy=geo.get('scale_y',scale)
                tx=evaluate_keys(geo.get('tx_keys'),local,geo.get('tx',0));ty=evaluate_keys(geo.get('ty_keys'),local,geo.get('ty',0))
                transform=np.array([[sx,0,tx],[0,sy,ty]],np.float32)
                base=cv2.warpAffine(arr,transform,(p['width'],p['height']),flags=cv2.INTER_AREA,borderMode=cv2.BORDER_CONSTANT)
                active=[e for e in p['layers'] if e['in']<=t+1e-9<e['out']]
                if active:base=comp.compose(base,active,t)
                if (w,h)!=(p['width'],p['height']):base=cv2.resize(base,(w,h),interpolation=cv2.INTER_AREA)
                frame=av.VideoFrame.from_ndarray(base,format='rgb24').reformat(format='yuv420p',src_colorspace=1,dst_colorspace=1);frame.pts=i;frame.time_base=1/fps
                for packet in stream.encode(frame):out.mux(packet)
                if i==0 or i%round(float(fps))==0:log.append({'output_frame':i,'timeline_seconds':t,'source':mid,'requested_source_seconds':st,'decoded_source_seconds':chosen,'clip_id':c['id'],'active_layers':[e['id'] for e in active]})
                if i%max(1,round(float(fps)*5))==0:print(json.dumps({'rendered':i,'total':total,'seconds_elapsed':round(time.monotonic()-begin,1)}),flush=True)
            for packet in stream.encode():out.mux(packet)
        finally:
            out.close()
            for r in readers.values():r.close()
        cmd=[ffmpeg,'-v','error','-i',str(video)]
        if audio:cmd+=['-f','f32le','-ar','48000','-ac','2','-i',str(audio),'-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','256k']
        else:cmd+=['-c','copy']
        # A 1kHz movie clock can round an AAC edit-list endpoint below the last
        # intended sample. 240kHz exactly represents the 48kHz audio sample grid.
        cmd+=['-movie_timescale','240000','-movflags','+faststart','-t',f'{end-start:.9f}',str(output)];subprocess.run(cmd,check=True)
    report={'project':str(project),'project_sha256':sha(project),'output':str(output),'output_sha256':sha(output),'draft':draft,'release_ready':not draft and p.get('release_ready',False),'range':[start,end],'fps':str(fps),'size':[w,h],'frame_count':total,'seconds_elapsed':round(time.monotonic()-begin,2),'audio':audiostat,'source_trace_1hz':log,'source_reference_passthrough':False,'limitations':p.get('limitations',[])}
    output.with_suffix('.render.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n', encoding='utf-8');return report

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('project');ap.add_argument('--output',required=True);ap.add_argument('--draft',action='store_true');ap.add_argument('--start',type=float,default=0);ap.add_argument('--end',type=float);ap.add_argument('--width',type=int);ap.add_argument('--fps');ap.add_argument('--ffmpeg',default=os.environ.get('ATRASS_FFMPEG','ffmpeg'));a=ap.parse_args()
    try:r=render(a.project,a.output,a.draft,a.start,a.end,a.width,a.fps,a.ffmpeg)
    except Exception as e:print(str(e),file=sys.stderr);return 1
    print(json.dumps({k:r[k] for k in ('output','draft','frame_count','seconds_elapsed','audio')},ensure_ascii=False));return 0
if __name__=='__main__':sys.exit(main())
