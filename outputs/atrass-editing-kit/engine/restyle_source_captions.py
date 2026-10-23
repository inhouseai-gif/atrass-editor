#!/usr/bin/env python3
"""Apply the measured A natural-caption candidate to source-bound dialogue.

Preserves words, colors, timing, media, other layers and audio. Natural glyph
bounds determine the bar width; no reference sentence geometry is copied.
"""
import argparse, copy, json, os, unicodedata
from pathlib import Path
from fontTools.ttLib import TTFont
from apply_source_assets import read, sha, require, relative
from render import Composer, validate, resolve
from compile_plan import complete_emphasis_mask

PROFILE={
    'id':'A-natural-suit450-54-0-v1',
    'font_sha256':'12e1fea375d27869a686d7432c1e6a38154d394994f5d82ace24f36a7db68333',
    'font_size':54,'weight':450,'tracking':0,
    'status':'measured_candidate_not_original_font_identified',
    'evidence':'references/source-natural-caption-validation.json',
}

def transform(p,root):
    require(p.get('variant')=='A' and p.get('width')==1920 and p.get('height')==1080,'A 1080p required')
    require('source_caption_style' not in p,'Already restyled; use the earlier project as input')
    desc=p['fonts'].get('body',{})
    require(desc.get('weight')==350 and desc.get('index',0)==0,'Expected original natural SUIT350 body descriptor')
    fp=resolve(root,desc['path']);require(sha(fp)==PROFILE['font_sha256'],'Font identity mismatch')
    with TTFont(str(fp),lazy=True) as ft: cmap=ft.getBestCmap()
    result=copy.deepcopy(p);comp=Composer(result,root);records=[]
    captions=[e for e in result['layers'] if e['kind']=='caption']
    require(captions,'No source captions')
    allowed={'id','in','out','status','z','kind','font','font_size','tracking','text','fill','background','padding','source_dialogue_binding','box','text_offset'}
    for e in captions:
        require(not set(e)-allowed,'Custom caption geometry/effects need a separate policy: '+e['id'])
        require(e.get('font')=='body' and e.get('font_size')==54 and e.get('tracking')==-.5 and e.get('padding')==[16,16],'Unsupported input caption style: '+e['id'])
        require(e.get('source_dialogue_binding') and e['source_dialogue_binding'].get('take_id') in {c['id'] for c in p['clips']},'Missing source binding')
        text=e['text']
        require(isinstance(text,str) and text and text==text.strip() and len(text)<=256,'Caption text must be nonempty, trimmed and at most256 characters')
        require(unicodedata.normalize('NFC',text)==text and not any(unicodedata.category(c) in ('Cc','Cf','Cs','Zl','Zp') for c in text),'Single NFC line required; text was not changed')
        require(all(c.isspace() or ord(c) in cmap for c in text),'Missing font glyph')
        before=copy.deepcopy(e)
        e.update(font_size=PROFILE['font_size'],weight=PROFILE['weight'],tracking=PROFILE['tracking'])
        mask=comp.text_mask(text,e);complete=complete_emphasis_mask(comp,text,e)
        require(mask.size==complete.size and mask.tobytes()==complete.tobytes(),'Incomplete glyph raster')
        require(0<mask.width<=1640 and 0<mask.height<=64,'Caption does not fit; split by meaning instead of shrinking or clipping')
        width=mask.width+32;left=round(960-width/2)
        e['box']=[left,938,left+width,1018];e['text_offset']=[16,round((80-mask.height)/2)]
        records.append({'id':e['id'],'old_style':{k:before[k] for k in ('font_size','tracking','box','text_offset')},'new_style':{k:e[k] for k in ('font_size','weight','tracking','box','text_offset')},'natural_ink_size':list(mask.size)})
    return result,records

def apply(project,output,expected_sha256):
    project=Path(project).absolute();output=Path(output).absolute()
    require(output.suffix=='.json' and output.parent.resolve()==project.parent.resolve(),'New JSON must be beside input')
    require(not os.path.lexists(output),'Output exists')
    p,h=read(project);require(h==expected_sha256,'Input project SHA mismatch')
    validate(p,project.parent,True);q,records=transform(p,project.parent)
    q['source_caption_style']={'schema':'atrass-source-caption-style-v1','profile':PROFILE,'input_project':relative(project,output.parent),'input_sha256':h,'implementation_sha256':sha(Path(__file__)),'records':records,'all_text_source_timing_colors_audio_preserved':True,'release_ready':False}
    q['release_ready']=False;validate(q,output.parent,True)
    require(sha(project)==h,'Input changed during restyle')
    with output.open('x',encoding='utf-8') as f:json.dump(q,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    return {'output':str(output),'sha256':sha(output),'captions':len(records),'profile':PROFILE['id'],'audio_unchanged':q['audio']==p['audio'],'release_ready':False}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for n in ('project','output','expected-sha256'):ap.add_argument('--'+n,required=True)
    a=ap.parse_args();print(json.dumps(apply(a.project,a.output,a.expected_sha256),ensure_ascii=False))
