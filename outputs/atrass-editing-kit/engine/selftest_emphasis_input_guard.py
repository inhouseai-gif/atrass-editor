#!/usr/bin/env python3
"""Finite auto-fit input regressions; no existing project/render is overwritten.

Portable negative-bearing fixture is an explicitly synthetic Pillow font stub
using the bundled OFL Noto glyphs, not an original or real italic font. Optional
--local-italic checks a real local font without copying or redistributing it.
"""
from pathlib import Path
import argparse,copy,hashlib,json,tempfile,unicodedata
from PIL import Image,ImageFont
from compile_plan import compile_plan,fit_large_emphasis,complete_emphasis_mask
from render import Composer

KIT=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(condition,message):
    if not condition:raise AssertionError(message)
def fixture_plan(source,text,font=None):
    p={'schema':'atrass-edit-plan-v1','variant':'A','media':{'still':{'path':str(source),'type':'image','role':'source_footage'}},
       'duration':1,'clips':[{'id':'still','in':0,'out':1,'source':'still','source_in':0}],
       'events':[{'id':'emphasis','template':'large_emphasis','in':0,'out':1,'text':text}]}
    if font:p['fonts']={'bold':{'path':str(font)}}
    return p

class NegativeBearingFontStub(ImageFont.FreeTypeFont):
    """Shift real OFL glyphs left 40px in BOTH metrics and raster offsets."""
    def getbbox(self,*args,**kwargs):
        b=super().getbbox(*args,**kwargs);return b[0]-40,b[1],b[2]-40,b[3]
    def getmask2(self,*args,**kwargs):
        mask,offset=super().getmask2(*args,**kwargs);return mask,(offset[0]-40,offset[1])
class NegativeBearingComposerStub(Composer):
    def font(self,layer):
        f=super().font(layer)
        return NegativeBearingFontStub(f.path,f.size,index=f.index,layout_engine=f.layout_engine)

def run(local_italic=None):
    checks=[];local=None
    with tempfile.TemporaryDirectory(prefix='atrass-emphasis-input-guard-') as td:
        d=Path(td);source=d/'still.png';Image.new('RGB',(1920,1080),'#303030').save(source)
        def compile_case(name,text,font=None,expect_error=None):
            plan=d/(name+'.plan.json');out=d/(name+'.project.json')
            before=json.dumps(fixture_plan(source,text,font),ensure_ascii=False,indent=2)+'\n';plan.write_text(before,encoding='utf-8')
            try:compile_plan(plan,out)
            except ValueError as exc:
                if expect_error is None:raise
                require(expect_error in str(exc),'Unexpected rejection: '+str(exc))
                require(not out.exists(),'Rejected input produced an output project')
                require(plan.read_text(encoding='utf-8')==before,'Compiler changed the input plan')
                return {'name':name,'pass':True,'rejected':True,'error':str(exc),'input_bytes_preserved':True,'output_created':False}
            require(expect_error is None,'Expected input rejection: '+name)
            p=json.loads(out.read_text(encoding='utf-8'));e=p['layers'][0];comp=Composer(p,d)
            return p,e,comp
        for name,text,expected in [('latin','AVATAR AVATAR',[1184,114]),('mixed','“NO”라고 말하는 용기',[1402,141]),
                                  ('korean-long','관계를 바꾸는 것은 매일 반복하는 작은 행동입니다',[1720,79])]:
            p,e,comp=compile_case(name,text);m=comp.text_mask(text,e);im,xy=comp.image(e,.5)
            require(list(m.size)==expected,'Normal case dimensions changed: '+name)
            require(e['text']==text and e['layout_fit']['input_guard']['natural_mask_matches_complete_padded_raster'],'Guard record missing')
            require(xy[0]>=96 and xy[0]+im.width<=1824 and xy[1]+im.height<=984,'Normal case exceeds safe area')
            checks.append({'name':name,'pass':True,'text':text,'mask_size':list(m.size),'xy':list(xy),'mask_sha256':hashlib.sha256(m.tobytes()).hexdigest()})
        p,e,comp=compile_case('nfc-short','사랑');require(comp.text_mask(e['text'],e).size==(270,140),'NFC normal text changed')
        checks.append({'name':'nfc_short_accepted','pass':True,'mask_size':[270,140]})
        checks.append(compile_case('nfd-short',unicodedata.normalize('NFD','사랑'),expect_error='requires NFC text'))
        # This fixture exercises the same draw pipeline as Composer, not a
        # commercial font substitute or evidence of a real italic typeface.
        p,e,_=compile_case('portable-fixture','AVATAR');stub=NegativeBearingComposerStub(p,d);fresh=copy.deepcopy(e);fresh.pop('layout_fit')
        natural=stub.text_mask(fresh['text'],fresh);complete=complete_emphasis_mask(stub,fresh['text'],fresh)
        require(natural.size!=complete.size or natural.tobytes()!=complete.tobytes(),'Synthetic negative bearing did not expose clipping')
        try:fit_large_emphasis(fresh,stub)
        except ValueError as exc:
            require('complete padded raster' in str(exc),'Unexpected synthetic fixture rejection')
            require('primitive does not repair clipping' in str(exc),'Error wrongly suggests bypassing the renderer defect')
            checks.append({'name':'pillow_negative_bearing_stub_rejected','pass':True,'fixture_type':'synthetic_Pillow_FreeTypeFont_offset_stub_using_bundled_OFL_Noto',
                           'natural_size':list(natural.size),'complete_size':list(complete.size),'error':str(exc),'real_italic_font_validation':False})
        else:raise AssertionError('Synthetic preclipped natural mask was accepted')
        if local_italic:
            font=Path(local_italic).resolve();require(font.is_file(),'Local font does not exist')
            result=compile_case('local-italic','jazz life',font,expect_error='complete padded raster')
            local={**result,'font_path':str(font),'font_sha256':sha(font),'font_binary_copied':False,'scope':'Only this local font, phrase and default emphasis metrics; no general italic-font certification.'}
    return {'schema':'atrass-emphasis-input-guard-selftest-v1','pass':True,'checks':checks,'check_count':len(checks),'normal_cases':3,'local_italic_check':local,
            'bindings':{'compiler_sha256':sha(KIT/'engine/compile_plan.py'),'renderer_sha256':sha(KIT/'engine/render.py'),
                        'selftest_sha256':sha(__file__),'bundled_noto_sha256':sha(KIT/'assets/fonts/NotoSansKR[wght].ttf')},
            'reference_reconstruction_frames':0,'direct_listened_seconds':0,'release_ready':False}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output');ap.add_argument('--local-italic');a=ap.parse_args()
    if a.output and Path(a.output).exists():raise ValueError('Choose a new selftest output path')
    result=run(a.local_italic);text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if a.output:
        out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x',encoding='utf-8') as f:f.write(text)
    print(text,end='')
if __name__=='__main__':main()
