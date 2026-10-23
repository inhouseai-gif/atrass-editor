import copy,json
from extend_source_caption import transform,SCHEMA
from audit_caption_coverage import audit
h='a'*64;t={'segments':[{'start':1,'end':3,'text':'문장이 끝납니다','words':[{'start':1,'end':2,'word':'문장이'},{'start':2,'end':3,'word':' 끝납니다'}]}]}
p={'variant':'A','width':1920,'height':1080,'fps':'60','duration':5,'clips':[{'id':'take','in':0,'out':5,'source_in':0,'source':'raw'}],'audio':[{'in':0,'out':5,'source':'raw','source_in':0}],'layers':[{'id':'cap','kind':'caption','text':'문장이','in':1,'out':2,'font':'body','source_dialogue_binding':{'segment_index':0,'take_id':'take','transcript_sha256':h,'source_range':[1,2]},'box':[900,938,1020,1018],'text_offset':[16,16]}]}
s={'schema':SCHEMA,'project_sha256':'b'*64,'transcript':{'sha256':h},'caption_id':'cap','expected_text':'문장이','expected_source_range':[1,2],'new_word_range':[0,2],'evidence':'complete missingword'}
r,rec=transform(p,s,t,lambda e:(300,48));assert r['layers'][0]['text']=='문장이 끝납니다'and r['layers'][0]['out']==3;assert r['layers'][0]['box']==[794,938,1126,1018];assert r['audio']==p['audio']and r['clips']==p['clips'];assert p['layers'][0]['text']=='문장이';count=1
for label,mutate in [('wrong-old-text',lambda a,b,c:b.update(expected_text='다른말')),('wrong-old-span',lambda a,b,c:b.update(expected_source_range=[1,1.9])),('wrong-transcript',lambda a,b,c:b['transcript'].update(sha256='c'*64)),('no-extension',lambda a,b,c:b.update(new_word_range=[0,1])),('not-prefix',lambda a,b,c:b.update(new_word_range=[1,2])),('outside-source',lambda a,b,c:a['clips'][0].update(out=2.5)),('audio-offset',lambda a,b,c:a['audio'][0].update(source_in=.1)),('caption-overlap',lambda a,b,c:a['layers'].append({'id':'next','kind':'caption','in':2.9,'out':4})),('word-overlap',lambda a,b,c:c['segments'][0]['words'][1].update(start=1.9)),('empty-evidence',lambda a,b,c:b.update(evidence='')),('unknown-field',lambda a,b,c:b.update(change_audio=True))]:
 a,b,c=copy.deepcopy(p),copy.deepcopy(s),copy.deepcopy(t);mutate(a,b,c)
 try:transform(a,b,c,lambda e:(300,48))
 except(ValueError,KeyError):count+=1
 else:raise AssertionError(label)
try:transform(p,s,t,lambda e:(1700,48))
except ValueError:count+=1
else:raise AssertionError('overflow')
d={'takes':[{'id':'take','action':'keep','source_in':0,'source_out':4}],'captions':[{'id':'cap','take_id':'take','segment_index':0,'word_range':[0,1]}]};a=audit(d,t);assert a['uncovered_retained_words'][0]['word']==' 끝납니다';count+=1
d['captions'][0]['word_range']=[0,2];assert not audit(d,t)['uncovered_retained_words'];count+=1
# Overlapping ASR chunk must not demand a duplicate caption.
d['captions'][0]['word_range']=[0,2];u=copy.deepcopy(t);u['segments'].append(copy.deepcopy(u['segments'][0]));a=audit(d,u);assert not a['uncovered_retained_words']and len(a['timed_literal_matches_for_overlapping_or_explicit_cues'])==2;count+=1
u['segments'][1]['words'][1]['word']=' 다른말';assert len(audit(d,u)['uncovered_retained_words'])==1;count+=1
u['segments'][1]['words'][1].update(word=' 끝납니다',start=3.1,end=3.5);assert len(audit(d,u)['uncovered_retained_words'])==1;count+=1
print(json.dumps({'pass':True,'conditions':count,'checks_missing_word_duplicate_ASR_wrong_word_and_wrong_time':True,'release_ready':False}))
