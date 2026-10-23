"""Adversarial compatibility checks using real supplied-source project."""
import copy,json,unicodedata
from pathlib import Path
from restyle_source_captions import transform
P=Path(__file__).resolve().parents[1]/'projects/A-source-pacing-tail-v1.json'
p=json.loads(P.read_text(encoding='utf-8'));checks=[]
q,records=transform(p,P.parent)
assert len(records)==295 and q['audio']==p['audio'] and q['clips']==p['clips'] and q['fonts']==p['fonts']
assert [e for e in q['layers'] if e['kind']!='caption']==[e for e in p['layers'] if e['kind']!='caption']
for a,b in zip(p['layers'],q['layers']):
 assert {k:v for k,v in a.items() if k not in ('font_size','tracking','box','text_offset','weight')}=={k:v for k,v in b.items() if k not in ('font_size','tracking','box','text_offset','weight')}
checks.append('all295caption text/color/time/source bindings plus otherlayers and source/audio preserved')
def bad(name,fn):
 d=copy.deepcopy(p);e=next(x for x in d['layers'] if x['kind']=='caption');fn(d,e)
 try:transform(d,P.parent)
 except(ValueError,KeyError,TypeError):checks.append(name)
 else:raise AssertionError(name)
bad('otherstyle',lambda d,e:d.update(variant='B'))
bad('customwidth',lambda d,e:e.update(glyph_width=200))
bad('changedfont',lambda d,e:d['fonts']['body'].update(weight=400))
bad('customcaptionweight',lambda d,e:e.update(weight=500))
bad('missingbinding',lambda d,e:e.pop('source_dialogue_binding'))
bad('wrongtake',lambda d,e:e['source_dialogue_binding'].update(take_id='unknown'))
bad('repeatapplication',lambda d,e:d.update(source_caption_style={}))
bad('multiline',lambda d,e:e.update(text='첫 줄\n둘째 줄'))
bad('unicodeformat',lambda d,e:e.update(text='보이지\u200b않는 문자'))
bad('NFDpreservedandrefused',lambda d,e:e.update(text=unicodedata.normalize('NFD','이 문장')))
bad('unsupportedglyph',lambda d,e:e.update(text='재회 🛸'))
bad('overflow',lambda d,e:e.update(text='이 문장이 아주 길면 ' * 10+'끝'))
d=copy.deepcopy(p);e=next(x for x in d['layers'] if x['kind']=='caption');e['text']='새로운 대화에서도 3가지 원칙을 지켜요!';q,r=transform(d,P.parent)
assert next(x for x in q['layers'] if x['id']==e['id'])['text']==e['text'];checks.append('novel text and digits preserved with natural geometry')
print(json.dumps({'pass':True,'checks':len(checks),'rows':checks,'release_ready':False},ensure_ascii=False))
