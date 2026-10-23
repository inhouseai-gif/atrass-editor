#!/usr/bin/env python3
"""Check editorial identity conservation and rejection at dangerous boundaries."""
from pathlib import Path
from fractions import Fraction
import argparse, copy, json
from draft_from_transcript import caption_groups, plan_from_evidence, speech_units
from refine_transcript_draft import transform, SCHEMA
from run_longform import save


def run(output):
    checks=[]; fps=Fraction(60000,1001); fits=lambda s:len(s)<100
    ids={'source_sha256':'a'*64,'transcript_sha256':'b'*64,'input_plan_sha256':'c'*64,'decisions_sha256':'d'*64}
    transcript={'source_sha256':ids['source_sha256'],'segments':[
        {'start':.1,'end':.6,'text':'시작할까요?'},
        {'start':1.,'end':2.,'text':'좋아합니다.','words':[{'start':1.,'end':2.,'word':'좋아합니다.'}]},
        {'start':2.2,'end':3.2,'text':'좋아합니다.','words':[{'start':2.2,'end':3.2,'word':'좋아합니다.'}]},
        {'start':3.4,'end':4.4,'text':'좋아합니다.','words':[{'start':3.4,'end':4.4,'word':'좋아합니다.'}]},
        {'start':5.,'end':6.,'text':'매력이 없는 게 아닙니다.','words':[
            {'start':5.,'end':5.3,'word':'매력이'},{'start':5.3,'end':5.6,'word':' 없는'},
            {'start':5.6,'end':5.75,'word':' 게'},{'start':5.75,'end':6.,'word':' 아닙니다.'}]},
        {'start':7.,'end':7.5,'text':'촬영 끝인가요?'}]}
    units,_=speech_units(transcript,8.);groups=caption_groups(units,fps,fits)
    plan,_=plan_from_evidence('/synthetic/raw.mov',8.,ids['source_sha256'],transcript,ids['transcript_sha256'],groups,[],fps,'/synthetic/font.ttf')
    def cut(cid,a,b):
        selected=[u for u in units if a<u['end'] and b>u['start']]
        return {'id':cid,'source_range':[a,b],'remove_asr_refs':[r for u in selected for r in u['refs']],
                'expected_text':' '.join(u['text']for u in selected),'reason':'explicit synthetic agent choice','evidence':'counterexample fixture'}
    spec={'schema':SCHEMA,**{k:v for k,v in ids.items()if k!='decisions_sha256'},'cuts':[cut('head',0,.8),cut('retake',2.1,3.3),cut('tail',6.5,8.)], 'caption_breaks_source_seconds':[]}
    def check(name,ok):assert ok,name;checks.append(name)
    def reject(name,p=plan,t=transcript,s=None):
        try:transform(p,t,s or spec,ids,8.,fits)
        except ValueError:checks.append(name)
        else:raise AssertionError(name+' accepted')
    result,report=transform(plan,transcript,spec,ids,8.,fits)
    check('head_and_tail_cuts_create_no_empty_takes',all(x['source_in']<x['source_out']for x in result['takes']))
    check('only_declared_repeat_removed',sum(e['text']=='좋아합니다.'for e in result['events'])==2)
    check('retained_negation_unchanged',any(e['text']=='매력이 없는 게 아닙니다.'for e in result['events']))
    check('complete_identity_partition',set(map(tuple,report['removed_ASR_refs'])).isdisjoint(map(tuple,report['retained_ASR_refs'])) and len(report['removed_ASR_refs'])+len(report['retained_ASR_refs'])==len([r for u in units for r in u['refs']]))
    check('all_joins_round_inward',all(0<=v<1/float(fps)+1e-8 for c in report['decisions']for v in c['inward_rounding_residual_seconds']))
    check('caption_groups_do_not_bridge_deleted_speech',all(len(e['source_dialogue_binding']['asr_refs'])==1 or e['text']=='매력이 없는 게 아닙니다.'for e in result['events']))
    for key in ('source_sha256','transcript_sha256','input_plan_sha256'):
        s=copy.deepcopy(spec);s[key]='0'*64;reject('reject_wrong_'+key,s=s)
    s=copy.deepcopy(spec);s['cuts'][1]['remove_asr_refs']=[];reject('no_unlisted_speech_deletion',s=s)
    s=copy.deepcopy(spec);s['cuts'][1]['remove_asr_refs']=[[3,0]];reject('same_text_different_identity_not_substituted',s=s)
    s=copy.deepcopy(spec);s['cuts'][1]['expected_text']='좋아하지 않습니다.';reject('wrong_negation_not_accepted',s=s)
    s=copy.deepcopy(spec);s['cuts'][1]['source_range']=[2.5,3.3];reject('partial_word_never_clipped',s=s)
    s=copy.deepcopy(spec);s['cuts'][0]['source_range']=[.2,.8];reject('fallback_whole_cue_indivisible',s=s)
    s=copy.deepcopy(spec);s['cuts'][1]['remove_asr_refs']*=2;reject('duplicate_refs_rejected',s=s)
    s=copy.deepcopy(spec);s['cuts'][1]['source_range']=[.7,3.3];reject('overlapping_cut_rejected',s=s)
    s=copy.deepcopy(spec);s['cuts'][0]['source_range']=[0,float('nan')];reject('nonfinite_cut_rejected',s=s)
    s=copy.deepcopy(spec);s['cuts'][1]['reason']='';reject('reason_required',s=s)
    s=copy.deepcopy(spec);s['caption_breaks_source_seconds']=[5.4];reject('caption_break_cannot_split_word',s=s)
    s=copy.deepcopy(spec);s['caption_breaks_source_seconds']=[5.6,5.3];reject('unordered_caption_breaks_rejected',s=s)
    s=copy.deepcopy(spec);s['caption_breaks_source_seconds']=[5.3]
    q,_=transform(plan,transcript,s,ids,8.,fits)
    check('explicit_caption_break_preserves_words',any(e['text']=='매력이'for e in q['events']) and any(e['text']=='없는 게 아닙니다.'for e in q['events']))
    p=copy.deepcopy(plan);p['events'][0]['text']='바꾼 자막';reject('preedited_caption_not_silently_reset',p=p)
    p=copy.deepcopy(plan);p['events'].append({'template':'large_emphasis','in':1,'out':2,'text':'강조'});reject('existing_graphics_not_silently_dropped',p=p)
    p=copy.deepcopy(plan);p['takes'][0]['audio_source_in']=.1;reject('separate_audio_clock_rejected',p=p)
    p=copy.deepcopy(plan);p['takes'][0]['rate']=1.2;reject('speed_changes_rejected',p=p)
    s=copy.deepcopy(spec);s['cuts']=[cut('all',0,8)];reject('empty_programme_rejected',s=s)
    # An overlapping alignment is one indivisible display unit. Naming only one
    # of its records must not permit deletion of the other spoken claim.
    t=copy.deepcopy(transcript);t['segments'].insert(2,{'start':1.8,'end':2.3,'text':'아닙니다.'})
    us,_=speech_units(t,8.);gs=caption_groups(us,fps,fits)
    p,_=plan_from_evidence('/synthetic/raw.mov',8.,ids['source_sha256'],t,ids['transcript_sha256'],gs,[],fps,'/synthetic/font.ttf')
    s=copy.deepcopy(spec);s['cuts']=[dict(id='overlap',source_range=[1.,3.2],remove_asr_refs=[[1,0]],expected_text='좋아합니다.',reason='test',evidence='test')]
    reject('overlap_group_requires_all_owners',p=p,t=t,s=s)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    save(output/'validation.json',{'pass':True,'count':len(checks),'checks':checks,'release_ready':False})
    print(json.dumps({'pass':True,'count':len(checks)}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True);a=p.parse_args();run(a.output_dir)
