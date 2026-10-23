#!/usr/bin/env python3
"""Focused quiet transformation tests; no media decode or semantic approval."""
import copy, json
from apply_quiet_cuts import transform

def base():
    a={'segments':[{'start':0,'end':2,'text':' 첫 문장','words':[{'start':0.2,'end':.8,'word':' 첫'},{'start':1,'end':1.8,'word':' 문장'}]},{'start':5,'end':8,'text':' 다음 문장','words':[{'start':5.2,'end':6,'word':' 다음'},{'start':7,'end':7.8,'word':' 문장'}]}]}
    d={'schema':'atrass-source-dialogue-v1','fps':'60000/1001','transcript_sha256':'x','takes':[{'id':'t','action':'keep','source_in':0,'source_out':10.01,'reason':'keep','evidence':'source'}],'captions':[{'id':'a','take_id':'t','segment_index':0},{'id':'b','take_id':'t','segment_index':1}],'visual_events':[{'id':'v','take_id':'t','segment_index':1,'word_range':[0,1],'template':'large_emphasis'}]}
    q={'schema':'atrass-quiet-cut-candidates-v1','native_video_fps':'60','speech_guard_seconds':.1,'retained_quiet_per_edge_seconds':.15,'candidates':[{'quiet_range':[2.7,4.3],'remove_source_frames':[180,240],'remove_source_range':[3,4]}]}
    s={'schema':'atrass-quiet-application-selection-v1','policy':'explicit draft','cuts':[{'candidate_index':0,'take_id':'t','action':'apply','reason':'measured quiet'}]}
    return d,a,q,s
checks=[]
def ok(name,v):
    assert v,name;checks.append(name)
def failure(name,fn,part):
    try:fn()
    except ValueError as e:ok(name,part in str(e))
    else:raise AssertionError(name+' did not reject')
def run():
    d,a,q,s=base();r,p=transform(d,a,q,s,12)
    ok('one quiet cut accepted',p['accepted_count']==1)
    ok('frame conservation',p['original_frames']==p['output_frames']+p['removed_frames'])
    ok('inward source bounds',3<=p['cuts'][0]['applied_source_range'][0]<p['cuts'][0]['applied_source_range'][1]<=4)
    ok('every child on original CFR phase',all(abs(t['source_in']*60000/1001-round(t['source_in']*60000/1001))<1e-8 for t in r['takes']))
    ok('tail unchanged',r['takes'][-1]['source_out']==d['takes'][0]['source_out'])
    ok('caption indices unchanged',[{k:v for k,v in x.items() if k!='take_id'} for x in r['captions']]==d['captions'].__class__([{k:v for k,v in x.items() if k!='take_id'} for x in d['captions']]))
    ok('visual source words unchanged',r['visual_events'][0]['word_range']==[0,1])
    ok('different kept child IDs',len({t['id'] for t in r['takes']})==2)
    ok('input immutable',d==base()[0])
    dd=copy.deepcopy(d);dd['takes'].append({**dd['takes'][0],'id':'reuse'});dd['captions'].append({**dd['captions'][0],'id':'reused-a','take_id':'reuse'})
    rr,pp=transform(dd,a,q,s,12);ok('source reuse only chosen occurrence changes',next(t for t in rr['takes'] if t['id']=='reuse')==dd['takes'][-1])
    ss=copy.deepcopy(s);ss['protected_ranges']=[{'take_id':'t','source_range':[2.5,4.5],'reason':'chapter beat'}]
    rr,pp=transform(d,a,q,ss,12);ok('semantic protection skipped',pp['accepted_count']==0 and pp['cuts'][0]['skip_reason']=='protected_lifetime_or_semantic_boundary')
    dd=copy.deepcopy(d);dd['captions'][0].update(source_range=[1,5.8],text='Explicit long selection',evidence='test');dd['captions'][0].pop('word_range',None);aa=copy.deepcopy(a);aa['segments'][0]['end']=6
    rr,pp=transform(dd,aa,q,s,12);ok('caption lifetime crossing skipped not repeated',pp['accepted_count']==0 and len(rr['captions'])==2)
    dd=copy.deepcopy(d);dd['visual_events'][0].update(segment_index=0,source_range=[1,5.8],text='Explicit visual',evidence='test');dd['visual_events'][0].pop('word_range');rr,pp=transform(dd,aa,q,s,12);ok('visual crossing skipped',pp['accepted_count']==0)
    aa=copy.deepcopy(a);aa['segments'][0]['words'][1]['end']=3.2;aa['segments'][0]['end']=3.2
    failure('word guard reject',lambda:transform(d,aa,q,s,12),'protected ASR')
    qq=copy.deepcopy(q);qq['candidates'][0]['remove_source_range']=[3.1,4]
    failure('frame time mismatch reject',lambda:transform(d,a,qq,s,12),'frame/range mismatch')
    dd=copy.deepcopy(d);dd['takes'][0]['rate']=2;failure('rate mismatch reject',lambda:transform(dd,a,q,s,12),'rate 1')
    dd=copy.deepcopy(d);dd['takes'][0]['audio_source_in']=1;failure('audio origin mismatch reject',lambda:transform(dd,a,q,s,12),'aligned video/audio')
    ss=copy.deepcopy(s);ss['cuts'].append(copy.deepcopy(ss['cuts'][0]));failure('duplicate occurrence selection reject',lambda:transform(d,a,q,ss,12),'Duplicate candidate')
    dd=copy.deepcopy(d);dd['takes'].append(copy.deepcopy(dd['takes'][0]));failure('duplicate take ID reject',lambda:transform(dd,a,q,s,12),'Duplicate take')
    ss=copy.deepcopy(s);ss['cuts'][0]['take_id']='missing';failure('unknown occurrence reject',lambda:transform(d,a,q,ss,12),'kept occurrence')
    ss=copy.deepcopy(s);ss['cuts'][0]['action']='skip';rr,pp=transform(d,a,q,ss,12);ok('explicit skip preserves original takes',rr['takes']==d['takes'])
    qq=copy.deepcopy(q);qq['candidates'].append({'quiet_range':[4.1,5.1],'remove_source_frames':[258,288],'remove_source_range':[4.3,4.8]});ss=copy.deepcopy(s);ss['cuts'].append({'candidate_index':1,'take_id':'t','action':'apply','reason':'separate quiet run'});rr,pp=transform(d,a,qq,ss,12);ok('quiet-only child explicit omission',len(rr['takes'])==3 and 'caption_omission_reason' in rr['takes'][1])
    qq=copy.deepcopy(q);qq['candidates'][0]['quiet_range']=[2.95,4.1];failure('retained edge guard reject',lambda:transform(d,a,qq,s,12),'retained edge')
    dd=copy.deepcopy(d);dd['takes'][0]['source_out']=10.005;failure('original CFR tail beyond source rejected',lambda:transform(dd,a,q,s,10.005),'quantized take exceeds')
    print(json.dumps({'passed':len(checks),'checks':checks,'scope':'pure transformation only; no media decode, semantic or audio approval'},indent=2))
if __name__=='__main__':run()
