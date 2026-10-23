#!/usr/bin/env python3
"""Source event rebind fixture: split take, asset identity and fail-closed cases."""
from pathlib import Path
import tempfile,json,copy,hashlib
from rebind_source_events import rebind
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):p.write_text(json.dumps(d))
count=0
with tempfile.TemporaryDirectory() as td:
 root=Path(td);asset=root/'asset.png';asset.write_bytes(b'finite fixture');provenance=root/'asset.json';write(provenance,{'fixture':True})
 plan=root/'plan.json';write(plan,{'source_dialogue_assembly':{'input_sha256':{'decisions':'d'*64}}})
 project={'plan_source':str(plan),'fps':'60','media':{'raw':{'role':'source_footage','sha256':'a'*64}},'clips':[{'id':'take','in':0,'out':3,'source_in':0},{'id':'take__retake_002','in':3,'out':8,'source_in':5}]}
 receipt={'schema':'atrass-source-retake-cuts-v1','source_sha256':'a'*64,'output_decisions_sha256':'d'*64,'take_rebinding':{'take':[{'id':'take','source_range':[0,3]},{'id':'take__retake_002','source_range':[5,10]}]}}
 event={'schema':'atrass-source-assets-v1','project_sha256':'b'*64,'source_sha256':'a'*64,'events':[{'id':'art','source_range':[6,8],'take_ids':['take'],'asset':{'path':str(asset),'sha256':sha(asset),'provenance':{'path':str(provenance),'sha256':sha(provenance)}},'layout':{'fit':'cover'},'motion':{'scale_start':1,'scale_end':1.04}}]}
 def run(label,mutate=None):
  global count
  e,p,r=copy.deepcopy(event),copy.deepcopy(project),copy.deepcopy(receipt)
  if mutate:mutate(e,p,r)
  ep,pp,rp=[root/(label+'-'+s+'.json')for s in ('e','p','r')]
  for path,data in [(ep,e),(pp,p),(rp,r)]:write(path,data)
  out=root/label/'out.json'
  if mutate:
   try:rebind(ep,pp,rp,out)
   except(ValueError,KeyError,TypeError):assert not out.exists();count+=1
   else:raise AssertionError('Accepted invalid '+label)
  else:
   rebind(ep,pp,rp,out);got=json.loads(out.read_text());expected=copy.deepcopy(e);expected['project_sha256']=sha(pp);expected['events'][0]['take_ids']=['take__retake_002'];expected['events'][0]['asset']['path']='../asset.png';expected['events'][0]['asset']['provenance']['path']='../asset.json';assert got==expected;assert json.loads(ep.read_text())==event;count+=1
 run('valid')
 run('wrong-source',lambda e,p,r:e.update(source_sha256='f'*64))
 run('wrong-plan',lambda e,p,r:r.update(output_decisions_sha256='f'*64))
 run('missing-take',lambda e,p,r:p['clips'].pop())
 run('wrong-source-in',lambda e,p,r:p['clips'][1].update(source_in=4.9))
 run('wrong-duration',lambda e,p,r:p['clips'][1].update(out=9))
 run('unknown-take',lambda e,p,r:e['events'][0].update(take_ids=['missing']))
 run('fully-removed-event',lambda e,p,r:e['events'][0].update(source_range=[3.1,4.9]))
 run('asset-tamper',lambda e,p,r:e['events'][0]['asset'].update(sha256='f'*64))
 run('duplicate-take',lambda e,p,r:e['events'][0].update(take_ids=['take','take']))
print(json.dumps({'pass':True,'conditions':count,'raw_media_required':False,'release_ready':False}))
