#!/usr/bin/env python3
"""Compile an agent-authored editing plan into the runnable Atrass timeline.

Known text/layout templates are executable. Unknown templates and missing assets
fail explicitly. Semantic decisions and font identity are never auto-approved.
Canvas coordinates currently use the measured 1920x1080 reference space.
"""
from pathlib import Path
from fractions import Fraction
import argparse,copy,hashlib,json,math,os,sys,unicodedata
from fontTools.ttLib import TTFont
from PIL import Image,ImageDraw
from render import Composer,validate
from audio_policy import validate_audio_policy

EMPHASIS_FIT_POLICY={
    'id':'large-emphasis-single-line-v1',
    'status':'new_template_policy_not_measured_original_settings',
    'horizontal_margin_px':96,'bottom_margin_px':96,'minimum_glyph_height_px':72,
    'position':[960,805],'anchor':'top_center',
    'method':'uniform_downscale_of_complete_natural_ink_mask_no_width_override',
    'scope':'Single line, fixed position, candidate font. Internal spaces are preserved; no wrapping or text rewriting.'
}

def complete_emphasis_mask(comp,text,layer):
    """Check the renderer's natural mask against a roomy, same-policy raster.

    Retain Composer.text_mask's draw coordinates, per-character tracking, font
    and stroke. Only add integer canvas padding, so the subpixel draw phase is
    unchanged. Glyph metrics size the padding, including negative bearings and
    overhangs; cropping the result must reproduce the natural mask byte for byte.
    This is an input compatibility check, not a replacement renderer or a font
    identity claim. Keep this draw policy aligned with Composer.text_mask.
    """
    font=comp.font(layer);tracking=layer.get('tracking',0);stroke=layer.get('stroke_width',0)
    b=font.getbbox(text,stroke_width=stroke);y=4-b[1];x=stroke+4
    draws=[]
    if tracking:
        for char in text:
            draws.append((x,y,char));x+=font.getlength(char)+tracking
    else:draws.append((x,y,text))
    bounds=[]
    for x,y,part in draws:
        a,c,d,e=font.getbbox(part,stroke_width=stroke)
        bounds.append((x+a,y+c,x+d,y+e))
    original_width=max(1,int(math.ceil(font.getlength(text)+max(0,len(text)-1)*tracking+stroke*2+12)))
    original_height=max(1,b[3]-b[1]+12)
    left=min(0,math.floor(min(q[0] for q in bounds)))-8
    top=min(0,math.floor(min(q[1] for q in bounds)))-8
    right=max(original_width,math.ceil(max(q[2] for q in bounds)))+8
    bottom=max(original_height,math.ceil(max(q[3] for q in bounds)))+8
    mask=Image.new('L',(right-left,bottom-top),0);draw=ImageDraw.Draw(mask)
    for x,y,part in draws:
        draw.text((x-left,y-top),part,font=font,fill=255,stroke_width=stroke,stroke_fill=255)
    box=mask.getbbox()
    return mask.crop(box) if box else mask

def fit_large_emphasis(layer,comp):
    """Preserve short text pixels; proportionally fit an entire long line.

    Renderer glyph_height applies one ratio to both mask dimensions. Setting
    glyph_width would independently squeeze letters, so it is never used here.
    Manual layout belongs to the explicit primitive template, not this policy.
    """
    eid=layer['id'];text=layer.get('text')
    if not isinstance(text,str) or not text or not text.strip():raise ValueError('Large emphasis requires nonempty text: '+eid)
    if text!=text.strip():raise ValueError('Large emphasis does not accept leading/trailing whitespace; use primitive for manual layout: '+eid)
    if len(text)>256:raise ValueError('Large emphasis text exceeds 256 characters; split by meaning: '+eid)
    if any(unicodedata.category(c) in ('Cc','Cf','Cs','Zl','Zp') for c in text):raise ValueError('Large emphasis requires one line without control/format characters: '+eid)
    if unicodedata.normalize('NFC',text)!=text:
        raise ValueError('Large emphasis requires NFC text; the original was not changed. Normalize the text to NFC in a new plan and retry: '+eid)
    conflicts=('glyph_width','glyph_height','glyph_bbox','glyph_bbox_1080','bbox','bbox_1080','bar_bbox_1080',
               'text_line_glyph_bboxes_1080','scale_x','scale_y','scale','scale_keys','rotation_keys','x_keys','y_keys',
               'box','text_offset','padding','size','crop','geometry','transform','character_reveal','reveal_keys',
               'shadow','layout_fit','fit_policy','auto_fit')
    used=[k for k in conflicts if k in layer]
    if used:raise ValueError('Large emphasis auto-fit conflicts with '+', '.join(used)+'; use primitive for manual geometry/effects: '+eid)
    if layer.get('position')!=[960,805] or layer.get('anchor')!='top_center' or layer.get('rotation',0)!=0 or layer.get('kind')!='text':
        raise ValueError('Large emphasis auto-fit uses fixed [960,805]/top_center and no rotation; use primitive for custom layout: '+eid)
    size=layer.get('font_size');tracking=layer.get('tracking',0);stroke=layer.get('stroke_width',0)
    if type(size) is not int or not 1<=size<=512:raise ValueError('Large emphasis font_size must be an integer in 1..512: '+eid)
    if type(tracking) not in (int,float) or not math.isfinite(tracking) or not -size/8<=tracking<=size/2:raise ValueError('Large emphasis tracking is nonfinite or outside supported spacing: '+eid)
    if type(stroke) is not int or not 0<=stroke<=16:raise ValueError('Large emphasis stroke_width must be an integer in 0..16: '+eid)
    descriptor=comp.p['fonts'].get(layer.get('font'))
    if descriptor is None:raise ValueError('Unknown emphasis font: '+str(layer.get('font')))
    from render import resolve
    font_path=resolve(comp.root,descriptor['path'])
    with TTFont(str(font_path),fontNumber=descriptor.get('index',0),lazy=True) as font:
        cmap=font.getBestCmap() or {}
        missing=sorted({ord(c) for c in text if not c.isspace() and ord(c) not in cmap})
    if missing:raise ValueError('Large emphasis candidate font lacks glyphs '+', '.join('U+%04X'%cp for cp in missing)+': '+eid)
    natural=comp.text_mask(text,layer)
    if natural.getbbox() is None:raise ValueError('Large emphasis has no visible glyphs: '+eid)
    complete=complete_emphasis_mask(comp,text,layer)
    if natural.size!=complete.size or natural.tobytes()!=complete.tobytes():
        raise ValueError('Large emphasis natural glyph mask differs from the complete padded raster (possible clipping from negative bearings/overhangs). Choose a supported candidate font or correct the renderer font metrics before retrying; changing to primitive does not repair clipping: '+eid)
    policy=EMPHASIS_FIT_POLICY;available_width=comp.w-2*policy['horizontal_margin_px'];available_height=comp.h-policy['bottom_margin_px']-layer['position'][1]
    factor=min(1,available_width/natural.width,available_height/natural.height)
    target_height=natural.height if factor==1 else math.floor(natural.height*factor)
    if target_height<policy['minimum_glyph_height_px']:
        raise ValueError('Large emphasis would fall below the template minimum glyph height of %dpx (needs %dpx); split/shorten the phrase: %s'%(policy['minimum_glyph_height_px'],target_height,eid))
    if factor<1:layer['glyph_height']=target_height
    fitted=comp.text_mask(text,layer)
    if fitted.width>available_width or fitted.height>available_height:raise ValueError('Large emphasis cannot fit the declared safe area: '+eid)
    layer['layout_fit']={'policy':copy.deepcopy(policy),'natural_ink_size':[natural.width,natural.height],
                         'fitted_ink_size':[fitted.width,fitted.height],'applied_scale':target_height/natural.height,
                         'resized':factor<1,'safe_area':[96,0,comp.w-96,comp.h-96],
                         'text_preserved':True,'text_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                         'input_guard':{'id':'large-emphasis-input-guard-v1','unicode_normalization':'NFC_input_verified_not_rewritten',
                                        'natural_mask_matches_complete_padded_raster':True},
                         'font_identity':'candidate_not_original_font_confirmed',
                         'rounding_note':'One uniform height-derived scale; width rounded to nearest pixel. Short fitting text receives no raster resize.'}
    return layer

def compile_plan(path,out):
    path=Path(path).resolve();out=Path(out).resolve();root=path.parent;plan=json.loads(path.read_text(encoding='utf-8'));kit=Path(__file__).resolve().parents[1]
    if out.exists():raise ValueError('Choose a new output project path')
    if plan.get('schema')!='atrass-edit-plan-v1':raise ValueError('Expected atrass-edit-plan-v1')
    validate_audio_policy(plan)
    variant=plan['variant']
    if variant not in ['A','B']:raise ValueError('Unknown reference variant')
    media=copy.deepcopy(plan['media'])
    for m in media.values():
        f=Path(m['path']);m['path']=str(f if f.is_absolute() else (root/f).resolve())
    fonts={
      'body':{'path':str(kit/'assets/fonts/SUIT-Variable.ttf'),'weight':350,'status':'candidate_not_original_font_confirmed'},
      'bold':{'path':str(kit/'assets/fonts/NotoSansKR[wght].ttf'),'weight':750,'status':'candidate_not_original_font_confirmed'}
    }
    for k,v in plan.get('fonts',{}).items():
        fonts[k]=copy.deepcopy(v);q=Path(v['path']);fonts[k]['path']=str(q if q.is_absolute() else (root/q).resolve())
    fps=Fraction(plan.get('fps','60000/1001' if variant=='A' else '60'));clips=[];audio=[];cursor=0
    if 'takes' in plan:
        for n,t in enumerate(plan['takes']):
            rate=t.get('rate',1)
            if rate<=0 or t['source_out']<=t['source_in']:raise ValueError('Invalid take interval/speed')
            count=round((t['source_out']-t['source_in'])/rate*float(fps))
            if count<1:raise ValueError('Take shorter than one output frame')
            lo=float(Fraction(cursor,1)/fps);cursor+=count;hi=float(Fraction(cursor,1)/fps)
            c={'id':t.get('id','take-'+str(n+1)),'in':lo,'out':hi,'source':t['source'],'source_in':t['source_in'],'rate':rate,'geometry':t.get('geometry',{}),'status':t.get('status','agent_edit_decision_candidate')};clips.append(c)
            if plan.get('audio_follow_takes'):
                audio.append({'id':c['id']+'-voice','in':lo,'out':hi,'source':t.get('audio_source',t['source']),'source_in':t.get('audio_source_in',t['source_in']),'rate':rate,'gain_db':t.get('voice_gain_db',-.567066),'status':'new_edit_audio_follows_selected_take_not_reference_mapping'})
        duration=float(Fraction(cursor,1)/fps)
    else:
        clips=copy.deepcopy(plan['clips']);duration=plan['duration']
    if 'audio' in plan:audio=copy.deepcopy(plan['audio'])
    p={'schema':'atrass-render-v1','title':plan.get('title','Atrass editing draft'),'variant':variant,'width':1920,'height':1080,'fps':str(fps),'duration':duration,'release_ready':False,'media':media,'fonts':fonts,'clips':clips,'audio':audio,'audio_master':plan.get('audio_master',{'sample_peak_ceiling_dbfs':-1.5}),'layers':[],'limitations':['This compiler executes an explicit edit plan; it does not certify semantic choices or ASR accuracy.','Body/bold fonts are reconstruction candidates; arbitrary handwriting requires a separately specified font.','The full template inventory is not implemented; unsupported events fail rather than disappear.'],'plan_source':str(path),'coverage_checklist':plan.get('coverage_checklist',None)}
    comp=Composer(p,out.parent);dialogue_suppression=[]
    for n,event in enumerate(plan.get('events',[])):
        e=copy.deepcopy(event);template=e.pop('template');eid=e.pop('id','event-'+str(n+1));lo=e.pop('in');hi=e.pop('out');common={'id':eid,'in':lo,'out':hi,'status':'template_render_candidate','z':e.pop('z',10)}
        if e.pop('replace_dialogue',False):dialogue_suppression.append((lo,hi))
        if template=='dialogue':
            text=e.pop('text');layer={**common,'kind':'caption','font':'body','font_size':54,'tracking':-.5,'text':text,'fill':'#F9FCDE','background':'#111111','padding':[16,16],**e}
            mask=comp.text_mask(text,layer)
            if mask.width>1640:raise ValueError('Caption too long; split by meaning before rendering: '+eid)
            width=mask.width+32;layer['box']=[round(960-width/2),938,round(960-width/2)+width,1018]
            layer['text_offset']=[16,round((80-mask.height)/2)];p['layers'].append(layer)
        elif template=='large_emphasis':
            layer={**common,'kind':'text','font':'bold','font_size':150,'tracking':-1,'fill':'#FFFFFF','text':e.pop('text'),'position':[960,805],'anchor':'top_center','z':20,**e}
            p['layers'].append(fit_large_emphasis(layer,comp))
            dialogue_suppression.append((lo,hi))
        elif template=='left_heading':
            if 'font' not in e:raise ValueError('Left-heading original font is unconfirmed; explicitly choose a candidate font')
            p['layers'].append({**common,'kind':'text','font_size':92,'fill':'#FFFBDA','position':[96,497],**e})
        elif template=='question':
            if 'font' not in e:raise ValueError('Question requires an explicit handwriting font; recovered phrase is not a general font')
            p['layers'].append({**common,'kind':'text','font_size':105,'fill':'#FFFFFF','position':[210,478],**e})
        elif template=='quote_reveal':
            if 'font' not in e:raise ValueError('Quote requires an explicitly selected font; original serif identity is unconfirmed')
            p['layers'].append({**common,'kind':'text','font_size':64,'fill':'#FFFFFF','position':[960,430],'anchor':'top_center','character_reveal':{'duration':.8,'glyph_fade_duration':1/30},**e})
        elif template=='image_asset':
            if 'source' not in e:raise ValueError('Image asset source required')
            p['layers'].append({**common,'kind':'image',**e})
        elif template=='chat_panel':
            if 'bubbles' not in e:raise ValueError('Explicit chat bubble text/positions required')
            p['layers'].append({**common,'kind':'chat','font':'body','font_size':54,'tracking':-.5,'background':'#B9CDE0' if variant=='A' else '#A8BDCF','position':[120,180],'size':[650,620],**e})
        elif template=='situation_label':
            p['layers'].append({**common,'kind':'label','font':'body','font_size':54,'fill':'#F9FCDE','background':'#111111','padding':[16,14],**e})
        elif template=='primitive':
            if 'kind' not in e:raise ValueError('Primitive requires explicit renderer kind')
            p['layers'].append({**common,**e})
        else:raise ValueError('Unimplemented template '+template+'; supply explicit primitive/assets and verification instead of omitting it')
    # Large emphasis replaces only the basic dialogue bar; independent labels remain.
    cuts=dialogue_suppression
    result=[]
    for e in p['layers']:
        ranges=[(e['in'],e['out'])]
        if e['kind']=='caption':
            for lo,hi in cuts:
                ranges=[r for a,b in ranges for r in ([(a,b)] if hi<=a or lo>=b else ([(a,lo)] if a<lo else [])+([(hi,b)] if hi<b else []))]
        for k,(a,b) in enumerate(ranges):
            q=copy.deepcopy(e);q['in']=a;q['out']=b
            if len(ranges)>1:q['id']+='-part'+str(k+1)
            result.append(q)
    p['layers']=result
    if variant=='B' and not plan.get('logo_decision'):
        raise ValueError('Variant B requires an explicit logo_decision and asset/omission reason')
    validate(p,out.parent,True);out.parent.mkdir(parents=True,exist_ok=True)
    for f in p['fonts'].values():
        if Path(f['path']).is_relative_to(kit):f['path']=os.path.relpath(f['path'],out.parent)
    # Exclusive creation also protects a destination created after the initial check.
    with out.open('x',encoding='utf-8') as stream:stream.write(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
    return {'output':str(out),'duration':duration,'clips':len(clips),'layers':len(result),'release_ready':False}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('plan');ap.add_argument('--output',required=True);a=ap.parse_args()
    try:print(json.dumps(compile_plan(a.plan,a.output),ensure_ascii=False))
    except Exception as e:print(str(e),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
