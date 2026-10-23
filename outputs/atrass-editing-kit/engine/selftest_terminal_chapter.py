import copy,json
from set_terminal_chapter import transform,SCHEMA
binding={'event_id':'final','take_id':'last'}
p={'variant':'A','width':1920,'height':1080,'audio':[{'id':'voice'}],'clips':[{'id':'raw'}],'duration':10,'layers':[{'id':'earlier-chapter-title','kind':'text','text':'이전','in':0,'out':1,'source_chapter_binding':{'event_id':'early'}},{'id':'final-chapter-title','kind':'text','text':'새로운 마지막 말','in':5,'out':8,'source_chapter_binding':binding},{'id':'final-chapter-heading','kind':'text','text':'CHAPTER 12','position':[850,402],'font':'candidate','glyph_height':32,'in':5,'out':8,'source_chapter_binding':binding}]}
s={'schema':SCHEMA,'project_sha256':'a'*64,'event_id':'final','expected_heading':'CHAPTER 12','expected_title':'새로운 마지막 말','reference':{}}
r,rec=transform(p,s,lambda e:(310,32));assert r['layers'][-1]['text']=='LAST CHAPTER'and r['layers'][-1]['position']==[805,402];assert r['layers'][:-1]==p['layers'][:-1];assert all(r[k]==p[k]for k in p if k!='layers');assert p['layers'][-1]['text']=='CHAPTER 12';count=1
for label,change in [('not-final',lambda a,b:a['layers'][0].update(**{'in':8,'out':9})),('title-mismatch',lambda a,b:b.update(expected_title='다른 말')),('heading-mismatch',lambda a,b:b.update(expected_heading='CHAPTER 6')),('missing-heading',lambda a,b:a['layers'].pop()),('unknown-field',lambda a,b:b.update(silence=True)),('animated-position',lambda a,b:a['layers'][-1].update(position_keys=[[0,0]])),('stretched-width',lambda a,b:a['layers'][-1].update(glyph_width=307)),('other-variant',lambda a,b:a.update(variant='B')),('duplicate-id',lambda a,b:a['layers'].append(copy.deepcopy(a['layers'][-1]))),('different-lifetime',lambda a,b:a['layers'][-1].update(out=7))]:
 a,b=copy.deepcopy(p),copy.deepcopy(s);change(a,b)
 try:transform(a,b,lambda e:(310,32))
 except(ValueError,KeyError):count+=1
 else:raise AssertionError(label)
try:transform(p,s,lambda e:(1800,32))
except ValueError:count+=1
else:raise AssertionError('oversized heading')
print(json.dumps({'pass':True,'conditions':count,'new_title_and_chapter12_tested':True,'release_ready':False}))
