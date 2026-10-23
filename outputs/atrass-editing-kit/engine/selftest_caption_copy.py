from pathlib import Path
import copy,json,unicodedata
from edit_caption_copy import transform,spoken_characters
from render import Composer
K=Path(__file__).resolve().parents[1];P=K/'projects/A-source-natural-caption-v1.json';D=K/'projects/A-source-caption-copy-v1.decisions.json';p=json.loads(P.read_text());s=json.loads(D.read_text());comp=Composer(p,P.parent);measure=lambda e:comp.text_mask(e['text'],e).size
q,r=transform(p,s,measure);assert len(r)==107 and p['audio']==q['audio'] and p['clips']==q['clips'] and p['duration']==q['duration'];rows=['107explicitcopychanges preserve source video/audio and all nonpunctuation characters']
for a,b in zip(p['layers'],q['layers']):
 assert {k:v for k,v in a.items() if k not in ('text','box','text_offset')}=={k:v for k,v in b.items() if k not in ('text','box','text_offset')}
 if a.get('text'):assert spoken_characters(a['text'])==spoken_characters(b['text'])
def reject(name,fn):
 d=copy.deepcopy(s);base=copy.deepcopy(p);fn(d,base)
 try:transform(base,d,measure)
 except(ValueError,KeyError,TypeError):rows.append(name)
 else:raise AssertionError(name)
reject('lexicalchange',lambda d,b:d['edits'][0].update(new='연애가 잘 풀리는 분들 보면'))
reject('digitinjection',lambda d,b:d['edits'][0].update(new=d['edits'][0]['new']+' 3'))
reject('duplicateid',lambda d,b:d['edits'].append(copy.deepcopy(d['edits'][0])))
reject('oldtextmismatch',lambda d,b:d['edits'][0].update(old='wrong'))
reject('unknownfield',lambda d,b:d['edits'][0].update(extra=True))
reject('unreasoned',lambda d,b:d['edits'][0].update(reason=''))
reject('newline',lambda d,b:d['edits'][0].update(new=d['edits'][0]['new']+'\n'))
reject('NFD',lambda d,b:d['edits'][0].update(new=unicodedata.normalize('NFD',d['edits'][0]['new'])))
reject('empty',lambda d,b:d['edits'][0].update(new=''))
reject('repeatstage',lambda d,b:b.update(source_caption_copy={}))
reject('nostylemixing',lambda d,b:b.update(variant='B'))
reject('reorderedwords',lambda d,b:d['edits'][0].update(new='분들 보면 연애가 잘 안 풀리는'))
reject('longpunctuationoverflow',lambda d,b:d['edits'][0].update(new=d['edits'][0]['new']+'!'*100))
print(json.dumps({'pass':True,'checks':len(rows),'rows':rows,'release_ready':False},ensure_ascii=False))
