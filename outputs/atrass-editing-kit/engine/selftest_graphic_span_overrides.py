#!/usr/bin/env python3
"""Finite mixed-row evidence binding and actual Composer regression checks."""
from pathlib import Path
from types import SimpleNamespace
import copy,contextlib,io,json,tempfile,hashlib
import numpy as np
from PIL import Image
import build_reference_project as b
from render import Composer

def run():
 kit=Path(__file__).resolve().parents[1];original=kit/'references/A-layer-events.json';checks=[]
 def check(n,v=True):
  if not v:raise AssertionError(n)
  checks.append(n)
 def reject(n,fn):
  try:fn()
  except (ValueError,KeyError,TypeError):checks.append(n);return
  raise AssertionError('Accepted '+n)
 with tempfile.TemporaryDirectory()as td:
  k=Path(td);root=k/'projects';root.mkdir();ev=k/'evidence';ev.mkdir();proof=ev/'native.txt';proof.write_text('Unit evidence fixture, not manual approval.')
  graphics=k/'graphics.json';graphics.write_bytes(original.read_bytes());source=b.read(graphics);e=next(e for e in source['events']if e['id']=='A-left_heading-0027')
  record={'id':e['id'],'text':e['text'],'event_frames':[34493,34708],'sample_frame':34495,'source_pts':575492,'source_time_base':'1/1000','status':'native_observed_candidate_not_authored_exact','limits':['Finite sample, candidate font not confirmed.'],'spans':[{'text':'원칙 3','bbox_1080':[101,442,322,520],'weight_role':'regular_sans','fill_observed_rgb':[255,255,255],'measurement_method':'test of observed threshold bounds','uncertainty':'Fixture carries observed candidate box, not authored coordinates.'},{'text':'채찍은 명분이 있을 때만','bbox_1080':[102,551,963,629],'weight_role':'bold_sans','fill_observed_rgb':[255,251,218],'measurement_method':'test of observed threshold bounds','uncertainty':'Top edge differs by one pixel between samples.'}],'evidence':[{'path':'evidence/native.txt','sha256':b.file_sha256(proof)}]}
  data={'schema':'atrass-graphic-span-overrides-v1','source_sha256':b.A_REFERENCE_SHA256,'input_graphics_sha256':b.file_sha256(graphics),'coordinate_reference':[1920,1080],'bbox_convention':'[left,top,right_exclusive,bottom_exclusive]','events':[record]};manifest=k/'overrides.json'
  def load(d=data):
   manifest.write_text(json.dumps(d,ensure_ascii=False));return b.load_graphic_span_overrides(manifest,graphics,k,root)
  doc,binding=load();me=next(x for x in doc['events']if x['id']==e['id']);lo=b.seconds(34493);hi=b.seconds(34708);layers=b.mixed_span_graphic_layers(me,lo,hi,lo)
  check('original graphics document stays byte-identical',b.file_sha256(graphics)==b.file_sha256(original));check('two rows preserve exact reviewed text',[l['text']for l in layers]==e['text'].split('\n'));check('two rows retain one semantic event',all(l['semantic_layer_id']==e['id']for l in layers));check('native lifetime same for both rows',all(l['in']==0 and l['out']==hi-lo for l in layers));check('distinct role/decoded RGB',[l['font']for l in layers]==['body','bold']and[l['fill']for l in layers]==[[255]*3,[255,251,218]])
  p={'width':1920,'height':1080,'media':{},'fonts':{'body':{'path':str(kit/'assets/fonts/SUIT-Variable.ttf'),'weight':400},'bold':{'path':str(kit/'assets/fonts/NotoSansKR[wght].ttf'),'weight':750}}};comp=Composer(p,root)
  for i,l in enumerate(layers):
   image,pos=comp.image(l,0);arr=np.asarray(image);mask=arr[:,:,3]>127;yy,xx=np.where(mask);actual=[int(xx.min()+pos[0]),int(yy.min()+pos[1]),int(xx.max()+pos[0]+1),int(yy.max()+pos[1]+1)];target=record['spans'][i]['bbox_1080'];check('Composer native anchor row '+str(i),max(abs(a-z)for a,z in zip(actual,target))<=1);check('Composer full-opacity RGB row '+str(i),np.all(arr[:,:,:3][arr[:,:,3]==255]==l['fill']));check('provenance carried row '+str(i),l['span_style_override']['measurement']==record)
  mutations=[('source SHA',lambda d:d.update(source_sha256='0'*64)),('input graphics SHA',lambda d:d.update(input_graphics_sha256='0'*64)),('schema',lambda d:d.update(schema='unknown')),('canvas',lambda d:d.update(coordinate_reference=[3840,2160])),('bbox convention',lambda d:d.update(bbox_convention='inclusive')),('duplicate event',lambda d:d['events'].append(copy.deepcopy(d['events'][0]))),('event ID',lambda d:d['events'][0].update(id='missing')),('event text',lambda d:d['events'][0].update(text='different')),('native lifetime',lambda d:d['events'][0].update(event_frames=[34494,34708])),('sample outside',lambda d:d['events'][0].update(sample_frame=34708)),('PTS',lambda d:d['events'][0].update(source_pts=1)),('PTS minus two frames',lambda d:d['events'][0].update(source_pts=575459)),('PTS plus two frames',lambda d:d['events'][0].update(source_pts=575525)),('wrong PTS time base',lambda d:d['events'][0].update(source_time_base='1/60000')),('candidate status',lambda d:d['events'][0].update(status='exact')),('missing limits',lambda d:d['events'][0].update(limits=[])),('span text',lambda d:d['events'][0]['spans'][0].update(text='no')),('unknown weight',lambda d:d['events'][0]['spans'][0].update(weight_role='mystery')),('invalid RGB',lambda d:d['events'][0]['spans'][1].update(fill_observed_rgb=[255,251,999])),('missing uncertainty',lambda d:d['events'][0]['spans'][0].pop('uncertainty')),('overlapping rows',lambda d:d['events'][0]['spans'][1].update(bbox_1080=[102,500,963,629])),('outside canvas',lambda d:d['events'][0]['spans'][0].update(bbox_1080=[-1,442,322,520])),('evidence SHA',lambda d:d['events'][0]['evidence'][0].update(sha256='0'*64)),('evidence traversal',lambda d:d['events'][0]['evidence'][0].update(path='../outside.txt')),('missing evidence',lambda d:d['events'][0].update(evidence=[])),('unsupported shadow',lambda d:d['events'][0]['spans'][0].update(shadow={'dx':2}))]
  for name,mut in mutations:
   changed=copy.deepcopy(data);mut(changed);reject('reject '+name,lambda:load(changed))
  reject('mixed span without explicit evidence opt-in',lambda:b.mixed_span_graphic_layers(e,lo,hi,lo));doc,binding=load();me=next(x for x in doc['events']if x['id']==e['id']);bad=copy.deepcopy(me);bad['render_hints']['rotation_degrees_clockwise']=1;reject('mixed span rotated parent',lambda:b.mixed_span_graphic_layers(bad,lo,hi,lo))
  for name,value in [('shadow',{'dx':2}),('scale_keys',[[0,1],[1,2]])]:
   bad=copy.deepcopy(me);bad['render_hints'][name]=value;reject('unsupported parent '+name,lambda:b.mixed_span_graphic_layers(bad,lo,hi,lo))
  check('normal no-override read does not apply candidates',b.load_graphic_span_overrides(None,graphics,k,root)[1] is None)
 return {'status':'passed','checks':len(checks),'check_names':checks,'manual_visual_review':False,'direct_listening_seconds':0}
if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False,indent=2))
