from pathlib import Path
import copy,json,sys
from extend_source_tail import transform
R=Path(__file__).resolve().parents[3];K=R/'outputs/atrass-editing-kit';P=K/'projects/A-source-pacing-portrait-v1.json';D=K/'projects/A-source-pacing-tail-v1.decisions.json';p=json.loads(P.read_text());s=json.loads(D.read_text());t=json.loads((D.parent/s['transcript']['path']).read_text());rows=[]
q,receipt=transform(p,s,t,1099.163);assert q['duration']>p['duration']and q['layers']==p['layers']and q['clips'][:-1]==p['clips'][:-1]and q['audio'][:-1]==p['audio'][:-1];rows.append('valid live source extension')
def reject(name,fn):
 b,d,u=copy.deepcopy(p),copy.deepcopy(s),copy.deepcopy(t);fn(b,d,u)
 try:transform(b,d,u,1099.163)
 except(ValueError,KeyError,TypeError,AssertionError):rows.append(name)
 else:raise AssertionError(name)
reject('zero frames',lambda b,d,u:d.update(additional_frames=0));reject('oversized tail',lambda b,d,u:d.update(additional_frames=121));reject('fractional frames',lambda b,d,u:d.update(additional_frames=1.2));reject('unknown fields',lambda b,d,u:d.update(extra=True));reject('source boundary mismatch',lambda b,d,u:d.update(expected_source_end=1080));reject('unknown take',lambda b,d,u:d.update(take_id='unknown'));reject('repeat extension',lambda b,d,u:b.update(source_tail_extension={}));reject('other source',lambda b,d,u:u.update(source_sha256='0'*64));reject('next utterance intrusion',lambda b,d,u:u['segments'].append({'words':[{'word':'new','start':1082,'end':1083}]}));reject('audio clock drift',lambda b,d,u:b['audio'][-1].update(source_in=1066.4));reject('slow motion',lambda b,d,u:b['clips'][-1].update(rate=.5));reject('freeze',lambda b,d,u:b['clips'][-1].update(freeze_source=1081));reject('fade out',lambda b,d,u:b['audio'][-1].update(fade_out=.3));reject('overlapping audio',lambda b,d,u:b['audio'].append(copy.deepcopy(b['audio'][-1])));reject('nonterminal take',lambda b,d,u:d.update(take_id=b['clips'][0]['id']));reject('no word evidence',lambda b,d,u:u.update(segments=[]))
try:transform(p,s,t,1082)
except ValueError:rows.append('source exhaustion')
else:raise AssertionError('source exhaustion')
print(json.dumps({'pass':True,'checks':len(rows),'rows':rows,'release_ready':False},ensure_ascii=False))
