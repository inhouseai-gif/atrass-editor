#!/usr/bin/env python3
"""Screen math, transformations, scope guards and frozen normal byte regressions.

Default paths work after integration into kit/engine and baseline evidence.
Candidate copies can override --engine-dir, --kit and --baseline-render.
No raw/reference media, existing output writes or network access are required.
"""
import os
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
from pathlib import Path
import sys,json,copy,importlib.util,tempfile,hashlib,argparse
from fractions import Fraction
import numpy as np
from PIL import Image
ap=argparse.ArgumentParser(description='Screen blend regression without raw/reference media. The frozen normal baseline SHA is mandatory.')
ap.add_argument('--engine-dir',type=Path,default=Path(__file__).resolve().parent)
ap.add_argument('--kit',type=Path)
ap.add_argument('--baseline-render',type=Path)
args=ap.parse_args()
ENGINE=args.engine_dir.resolve();K=(args.kit or ENGINE.parent).resolve()
BASELINE=(args.baseline_render or K/'evidence/screen-blend-engine-v1/baseline/render.py').resolve()
BASELINE_SHA256='f358bc17bec9486723a051be35ac114abdcdb1c82877947d3b188efec21261f6'
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
if not BASELINE.is_file():raise ValueError('Missing frozen baseline render.py: '+str(BASELINE))
if digest(BASELINE)!=BASELINE_SHA256:raise ValueError('Frozen normal baseline SHA mismatch: '+str(BASELINE))
FONT=K/'assets/fonts/SUIT-Variable.ttf'
if not FONT.is_file():raise ValueError('Missing bundled candidate font: '+str(FONT))
sys.path.insert(0,str(ENGINE))
def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module
new=load_module('screen_candidate_render',ENGINE/'render.py')
old=load_module('screen_normal_baseline',BASELINE)
checks=[]
def ok(name,test):
    assert test,name;checks.append(name)
def rejected(name,fn):
    try:fn()
    except ValueError:checks.append(name)
    else:raise AssertionError(name)
def oracle(base,src,xy):
    a=base.copy();x,y=xy
    for sy in range(src.shape[0]):
        for sx in range(src.shape[1]):
            xx=x+sx;yy=y+sy
            if not(0<=xx<a.shape[1] and 0<=yy<a.shape[0]):continue
            for c in range(3):
                b=int(base[yy,xx,c]);v=int(src[sy,sx,c]);alpha=int(src[sy,sx,3]);a[yy,xx,c]=round(Fraction(b)+Fraction((255-b)*v*alpha,65025))
    return a
with tempfile.TemporaryDirectory(prefix='screen-candidate-') as d:
    d=Path(d);rng=np.random.default_rng(71);rgba=rng.integers(0,256,(7,11,4),dtype=np.uint8);rgba[0,:,3]=0;rgba[1,:,3]=255
    asset=d/'asset.png';Image.fromarray(rgba).save(asset)
    font={'path':str(FONT),'weight':400,'status':'candidate'}
    p={'width':32,'height':24,'duration':1,'fps':'60','media':{'image':{'path':str(asset),'type':'image'}},'clips':[{'id':'base','source':'image','source_in':0,'in':0,'out':1}],'layers':[],'fonts':{'font':font}}
    base=rng.integers(0,256,(24,32,3),dtype=np.uint8)
    e={'id':'test','kind':'image','source':'image','in':0,'out':1,'position':[4,5],'blend_mode':'screen'}
    comp=new.Composer(p,d)
    ok('Screen exact rational scalar oracle with alpha0/255/random',np.array_equal(comp.compose(base,[e],0),oracle(base,rgba,(4,5))))
    for name,color in [('black',(0,0,0,255)),('white',(255,255,255,255)),('transparent',(255,90,10,0))]:
        out=Image.fromarray(base).convert('RGBA');im=Image.new('RGBA',(32,24),color);new.screen_composite(out,im,(0,0));actual=np.array(out.convert('RGB'))
        ok(name+' identity or white endpoint',np.array_equal(actual,np.full_like(base,255) if name=='white' else base))
    for xy in [(-5,-3),(29,20),(-12,0),(40,30),(0,-10)]:
        q={**e,'position':list(xy)};ok('Clipped placement '+str(xy),np.array_equal(comp.compose(base,[q],0),oracle(base,rgba,xy)))
    transform={**e,'id':'transform','crop':[2,1,10,6],'size':[12,8],'scale_keys':[[0,1.5],[.5,.75]],'rotation':11,'anchor':'center','position':[16,12],'opacity':.5,'fade_in':.25,'fade_out':.25,'opacity_keys':[[0,.25],[.5,.8]],'x_keys':[[0,4],[.5,16]],'y_keys':[[0,3],[.5,12]]}
    for t in [0,.125,.5,.875]:
        image,xy=comp.image(transform,t);expected=oracle(base,np.asarray(image),xy)
        ok('crop/size/scale/rotation/anchor/opacity/fades t='+str(t),np.array_equal(comp.compose(base,[transform],t),expected))
        bimage,bxy=old.Composer(p,d).image(transform,t);ok('shared transformed RGBA byte identity t='+str(t),bxy==xy and np.array_equal(image,bimage))
    for op in [0,.5,1]:
        q={**e,'id':'opacity'+str(op),'opacity':op};im,xy=comp.image(q,0);ok('opacity applied once '+str(op),np.array_equal(comp.compose(base,[q],0),oracle(base,np.array(im),xy)))
    rect={'id':'back','kind':'rect','in':0,'out':1,'size':[32,24],'fill':'#7347AA','position':[0,0],'z':0}
    top={'id':'top','kind':'rect','in':0,'out':1,'size':[10,4],'fill':'#101010','position':[2,3],'z':30}
    q={**e,'z':25};a=comp.compose(base,[top,q,rect],0);b=old.Composer(p,d).compose(base,[rect],0);b=oracle(b,rgba,(4,5));b=old.Composer(p,d).compose(b,[top],0)
    ok('stable z order with opaque bar above Screen',np.array_equal(a,b))
    fontbase={'font':'font','font_size':10,'text':'Ab 12','padding':[3,3],'fill':'#F9ECC9','position':[2,2]}
    layers=[{**fontbase,'kind':kind} for kind in ['text','caption','label']]+[rect,{'kind':'image','source':'image','crop':[1,1,10,7],'size':[16,12],'opacity':.47,'scale_keys':[[0,.8],[1,1.4]],'rotation_keys':[[0,0],[1,8]],'position':[-2,3]}, {'kind':'gradient','size':[32,24],'stops':[[0,'#00000000'],[1,'#AACCFF99']]},{**fontbase,'kind':'chat','size':[32,24],'bubbles':[{'text':'A','position':[2,2],'side':'left'}]}, {'kind':'polyline','size':[32,24],'points':[[1,1],[15,8],[30,22]],'fill':'#00FFFF','width':2}]
    for i,q in enumerate(layers):
        q={**q,'id':'normal'+str(i),'in':0,'out':1};nc=new.Composer(p,d);oc=old.Composer(p,d)
        for explicit in [False,True]:
            v={**q,**({'blend_mode':'normal'} if explicit else {})};ok('normal '+q['kind']+' explicit='+str(explicit),all(np.array_equal(nc.compose(base,[v],t),oc.compose(base,[q],t)) for t in [0,.4,.99]))
    reveal={**fontbase,'padding':3,'id':'normal-reveal','kind':'text','in':0,'out':1,'character_reveal':{'duration':.8,'start':0}}
    for t in [0,.3,.9]:
        ok('normal character reveal t='+str(t),np.array_equal(new.Composer(p,d).compose(base,[reveal],t),old.Composer(p,d).compose(base,[reveal],t)))
    for mode in ['add','multiply',None,1,{},['screen']]:
        q={**e,'blend_mode':mode};pp={**p,'layers':[q]};rejected('unsupported mode '+str(mode),lambda: new.validate(pp,d,True));rejected('compose rejects mode '+str(mode),lambda:comp.compose(base,[q],0))
    rejected('screen rejects nonimage',lambda:new.validate({**p,'layers':[{**rect,'blend_mode':'screen'}]},d,True))
    rejected('screen rejects ignored static scale',lambda:new.validate({**p,'layers':[{**e,'scale':1.2}]},d,True))
    for location in ['project','clip','audio']:
        pp=copy.deepcopy(p)
        if location=='project':pp['blend_mode']='screen'
        elif location=='clip':pp['clips'][0]['blend_mode']='screen'
        else:pp['audio']=[{'id':'a','source':'image','in':0,'out':1,'source_in':0,'blend_mode':'screen'}]
        rejected('blend forbidden on '+location,lambda:new.validate(pp,d,True))
    for key,value in [('scale_x',2),('scale_y',2),('geometry',{'scale':2})]:
        rejected('screen rejects ignored '+key,lambda:new.validate({**p,'layers':[{**e,key:value}]},d,True))
    for location in ['media','clip_geometry','layer_geometry','font','audio_geometry']:
        pp=copy.deepcopy(p)
        if location=='media':pp['media']['image']['blend_mode']='screen'
        elif location=='clip_geometry':pp['clips'][0]['geometry']={'blend_mode':'screen'}
        elif location=='layer_geometry':pp['layers']=[{**e,'blend_mode':'normal','geometry':{'blend_mode':'screen'}}]
        elif location=='font':pp['fonts']['font']['blend_mode']='screen'
        else:pp['audio']=[{'id':'a','source':'image','in':0,'out':1,'source_in':0,'geometry':{'blend_mode':'screen'}}]
        rejected('blend forbidden on '+location,lambda:new.validate(pp,d,True))
    new.validate({**p,'layers':[e]},d,True);checks.append('valid image screen accepted')
print(json.dumps({'status':'passed','count':len(checks),'checks':checks,'candidate_sha256':digest(ENGINE/'render.py'),'baseline_sha256':digest(BASELINE),'font_sha256':digest(FONT),'raw_or_reference_media_required':False},indent=2))
