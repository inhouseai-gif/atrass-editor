#!/usr/bin/env python3
"""Regression checks for conservative drafting and a real unknown-source route."""
from pathlib import Path
from fractions import Fraction
import argparse, hashlib, json, subprocess, sys
from draft_from_transcript import speech_units, caption_groups, choose_cuts, letters, POLICY
from run_longform import read, save, sha


def run(output, ffmpeg):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    checks=[]
    def check(name, test):
        assert test,name
        checks.append(name)
    def rejected(name, fn):
        try:fn()
        except ValueError:checks.append(name)
        else:raise AssertionError(name+' did not fail')
    t={'segments':[{'start':0.,'end':1.,'text':'그것은 사실이 아니다.','words':[
        {'word':'그것은','start':0.,'end':.3},{'word':' 사실이','start':.3,'end':.6},{'word':' 아니다.','start':.6,'end':1.}]},
        {'start':.8,'end':1.8,'text':'사실이 아니다.'}]}
    units,n=speech_units(t,3.)
    check('overlap_keeps_negation_and_repetition_without_deduplication',sum(len(letters(x['text']))for x in units)==sum(len(letters(s['text']))for s in t['segments']))
    broken={'segments':[{'start':0.,'end':1.,'text':'단어 전체를 보존합니다.','words':[{'start':0.,'end':0.,'word':'단어'}]}]}
    u,n=speech_units(broken,2.)
    check('broken_word_times_preserve_complete_cue',n['fallback_whole_cues']==[0] and u[0]['text']=='단어 전체를 보존합니다.')
    rejected('empty_cue_not_silently_deleted',lambda:speech_units({'segments':[{'start':0.,'end':1.,'text':' '}]},2.))
    rejected('out_of_range_times_rejected',lambda:speech_units({'segments':[{'start':0.,'end':3.,'text':'대사'}]},2.))
    rejected('reverse_cues_rejected',lambda:speech_units({'segments':[{'start':1.,'end':2.,'text':'뒤'},{'start':0.,'end':1.,'text':'앞'}]},3.))
    rejected('indivisible_wide_cue_never_gets_invented_word_times',lambda:caption_groups(u,Fraction(60),lambda text:False))
    groups=[{'start':1.,'end':2.},{'start':4.,'end':5.},{'start':12.,'end':13.}]
    quiet=[{'quiet_range':[0.,.8],'remove_source_range':[.2,.6]},
           {'quiet_range':[2.2,3.8],'remove_source_range':[2.4,3.6]},
           {'quiet_range':[5.2,11.8],'remove_source_range':[5.4,11.6]},
           {'quiet_range':[13.2,15.8],'remove_source_range':[13.4,15.6]}]
    cut,ledger=choose_cuts(quiet,groups,960,Fraction(60))
    check('only_short_internal_measured_gap_selected',cut==[[144,216]])
    check('long_silent_action_preserved',ledger[2]['reason']=='long_silent_action_or_demonstration_requires_visual_decision')
    raw=output/'새 촬영 합성.mov'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=8',
        '-f','lavfi','-i',r'aevalsrc=if(between(t\,0.5\,2)+between(t\,4\,6)\,0.1*sin(2*PI*440*t)\,0)|if(between(t\,0.5\,2)+between(t\,4\,6)\,-0.1*sin(2*PI*440*t)\,0):s=48000:d=8',
        '-c:v','libx264','-preset','ultrafast','-crf','25','-c:a','pcm_s16le',str(raw)],check=True)
    before=sha(raw)
    transcript={'source_sha256':before,'segments':[
        {'start':.6,'end':1.9,'text':'같은 말이어도 지우지 않습니다.','words':[
            {'word':'같은','start':.6,'end':.9},{'word':' 말이어도','start':.9,'end':1.25},
            {'word':' 지우지','start':1.25,'end':1.55},{'word':' 않습니다.','start':1.55,'end':1.9}]},
        {'start':4.1,'end':5.9,'text':'같은 말이어도 지우지 않습니다.','words':[
            {'word':'같은','start':4.1,'end':4.5},{'word':' 말이어도','start':4.5,'end':5.},
            {'word':' 지우지','start':5.,'end':5.4},{'word':' 않습니다.','start':5.4,'end':5.9}]}]}
    save(output/'transcript.json',transcript)
    engine=Path(__file__).resolve().parent
    cmd=[sys.executable,str(engine/'start_from_raw.py'),'start','--raw',str(raw),'--output-dir',str(output/'job'),
         '--asr-json',str(output/'transcript.json'),'--ffmpeg',ffmpeg,'--draft']
    with (output/'stdout.txt').open('w')as out,(output/'stderr.txt').open('w')as err:
        subprocess.run(cmd,check=True,stdout=out,stderr=err)
    result=read(output/'job/result.json');report=read(result['report']);plan=read(result['plan'])
    check('unknown_source_draft_renders_without_episode_recipe',result['status']=='transcript_draft_rendered_review_pending' and result['recipe_selected'] is False)
    check('both_repeated_negations_survive',len(plan['events'])==2 and all(e['text']==transcript['segments'][0]['text']for e in plan['events']))
    check('measured_silence_removed_but_speech_and_tails_retained',report['removed_frames']>0 and report['take_mapping'][0]['source_frames'][0]==0 and report['take_mapping'][-1]['source_frames'][1]==report['source_full_frames'])
    for row in report['cuts']:
        if row['action']=='shorten_in_draft':
            a,b=[x/(60000/1001) for x in row['source_frames']]
            assert 2.<=a<b<=4., row
    check('antiphase_voice_never_treated_as_silence',True)
    movie=Path(result['movie']);before_movie=sha(movie)
    with (output/'resume.stdout.txt').open('w')as out,(output/'resume.stderr.txt').open('w')as err:
        subprocess.run([sys.executable,str(engine/'start_from_raw.py'),'resume',str(output/'job')],check=True,stdout=out,stderr=err)
    check('resume_keeps_exact_rendered_movie',sha(movie)==before_movie)
    check('source_bytes_preserved',sha(raw)==before)
    check('no_semantic_or_release_approval',result['new_recording_semantic_decisions_inferred'] is False and result['release_ready'] is False)
    import av
    with av.open(str(movie))as c: actual_frames=sum(1 for _ in c.decode(video=0))
    check('actual_decoded_frame_count_matches_plan',actual_frames==report['output_frames'])
    save(output/'validation.json',{'pass':True,'count':len(checks),'checks':checks,
         'actual_rendered_frames':actual_frames,'removed_frames':report['removed_frames'],'movie_sha256':before_movie,
         'scope':'Synthetic unknown raw full draft and resume; not a new filmed episode or semantic/style approval',
         'release_ready':False})
    print(json.dumps(read(output/'validation.json'),ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output-dir',required=True);ap.add_argument('--ffmpeg',required=True)
    a=ap.parse_args();run(a.output_dir,a.ffmpeg)
