#!/usr/bin/env python3
"""Small independent source-clock/word-preservation fixture; no raw media needed."""
import copy,json
from apply_retake_cuts import transform
S='a'*64;T='b'*64;D='c'*64
transcript={'segments':[{'start':1.,'end':2.,'text':'첫 문장'},{'start':2.8,'end':4.,'text':'반복 문장'},{'start':6.,'end':8.,'text':'다음 문장'}]}
decisions={'schema':'atrass-source-dialogue-v1','title':'fixture','transcript_sha256':T,'fps':'60000/1001','takes':[{'id':'take','action':'keep','source_in':0.,'source_out':10.,'reason':'fixture','evidence':'test','rate':1}], 'captions':[{'id':'a','take_id':'take','segment_index':0},{'id':'b','take_id':'take','segment_index':1},{'id':'c','take_id':'take','segment_index':2}], 'visual_events':[{'id':'label','take_id':'take','segment_index':2,'template':'situation_label','text':'새 제목','evidence':'fixture','reason':'fixture','position':[120,150],'anchor':'top_left'}]}
spec={'schema':'atrass-source-retake-cuts-v1','input_decisions_sha256':D,'transcript_sha256':T,'source_sha256':S,'cuts':[{'id':'cut','source_range':[3.,5.],'reason':'retake','evidence':'explicit'}],'remove_captions':[{'id':'b','expected_text':'반복 문장','expected_source_range':[2.8,4.],'cut_id':'cut','retained_prefix_note':'Explicit ASR tail overlap; candidate'}]}
count=0

def run(d=decisions,s=spec):return transform(d,transcript,s,D,T,S,10.)
r,e=run();assert [(x['source_in'],x['source_out'])for x in r['takes']if x['action']=='keep']==[(0.,3.),(5.,10.)];assert r['captions']==[{'id':'a','take_id':'take','segment_index':0},{'id':'c','take_id':'take__retake_002','segment_index':2}];assert r['visual_events'][0]['take_id']=='take__retake_002';assert decisions['captions'][1]['id']=='b';count+=1

def rejects(label,mutate,which='s'):
 global count
 d,s=copy.deepcopy(decisions),copy.deepcopy(spec);mutate(s if which=='s'else d)
 try:run(d,s)
 except(ValueError,KeyError,TypeError):count+=1
 else:raise AssertionError('Accepted '+label)
rejects('badSHA',lambda s:s.update(input_decisions_sha256='d'*64))
rejects('unknownfield',lambda s:s.update(skip_checks=True))
rejects('missing removal',lambda s:s.update(remove_captions=[]))
rejects('wrong text',lambda s:s['remove_captions'][0].update(expected_text='다른 말'))
rejects('wrong span',lambda s:s['remove_captions'][0].update(expected_source_range=[2.9,4.]))
rejects('missing ASR exception',lambda s:s['remove_captions'][0].pop('retained_prefix_note'))
rejects('too long retained ASR prefix',lambda s:s['cuts'][0].update(source_range=[3.2,5.]))
rejects('truncates kept word',lambda s:s['cuts'][0].update(source_range=[1.5,5.]))
rejects('deletes answer',lambda s:s['cuts'][0].update(source_range=[3.,9.]))
rejects('overlapping cuts',lambda s:s['cuts'].append({'id':'cut2','source_range':[4.,6.],'reason':'x','evidence':'y'}))
rejects('nonfinite interval',lambda s:s['cuts'][0].update(source_range=[3.,float('inf')]))
rejects('rate change',lambda d:d['takes'][0].update(rate=.5),'d')
rejects('audio clock offset',lambda d:d['takes'][0].update(audio_source_in=.5),'d')
rejects('animated camera restart',lambda d:d['takes'][0].update(geometry={'scale_keys':[[0,.75],[5,1]]}),'d')
rejects('child ID collision',lambda d:d['takes'].append({'id':'take__retake_002','action':'drop','source_in':10.,'source_out':11.,'reason':'x','evidence':'y'}),'d')
rejects('extra visual in cut',lambda d:d['visual_events'].append({'id':'protected','take_id':'take','segment_index':1,'template':'situation_label','text':'보존','evidence':'x'}),'d')
print(json.dumps({'pass':True,'conditions':count,'raw_media_required':False,'release_ready':False}))
