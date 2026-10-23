#!/usr/bin/env python3
"""Extend a static chapter into a reviewed, caption-free preparation interval.

Only the five explicit card layers change their in-time. Audio and all source
cuts stay identical. The previous chapter binding remains historical evidence;
the additional source-relative cover is recorded separately, never called mute.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, json, os
from apply_source_assets import read, sha, require, relative, keys
from render import validate


def transform(p, decision):
    keys(decision, ('schema','project_sha256','event_id','start_frame','reason','evidence'),
         ('schema','project_sha256','event_id','start_frame','reason','evidence'), 'chapter cover decision')
    require(decision['schema'] == 'atrass-chapter-cover-v1', 'Invalid cover schema')
    require(all(isinstance(decision[k], str) and decision[k].strip() for k in ('event_id','reason','evidence')),
            'Explicit event, reason and visual evidence required')
    require('source_chapter_cover' not in p, 'Use the original unextended project')
    fps = Fraction(p['fps']); start_frame = decision['start_frame']
    require(type(start_frame) is int and start_frame >= 0, 'Nonnegative integer start_frame required')
    selected = [e for e in p['layers'] if e.get('source_chapter_binding',{}).get('event_id') == decision['event_id']]
    require(len(selected) == 5 and sorted(e['kind'] for e in selected) == ['image','image','rect','text','text'],
            'One complete five-layer chapter required')
    first = selected[0]; old_in = first['in']; out = first['out']; binding = first['source_chapter_binding']
    require(all((e['in'],e['out'],e['source_chapter_binding']) == (old_in,out,binding) for e in selected),
            'Chapter layers must share exactly one source interval')
    require(all(not any(k.endswith('_keys') or k in ('fade_in','fade_out','character_reveal') for k in e) for e in selected),
            'Animated/fading chapter needs a separate extension policy')
    start = float(Fraction(start_frame,1)/fps)
    require(0 < old_in - start <= 2, 'Extend before the existing chapter by at most two seconds')
    clips = [c for c in p['clips'] if c['id'] == binding['take_id']]
    require(len(clips) == 1, 'One existing take required')
    clip = clips[0]
    require(clip.get('rate',1) == 1 and not clip.get('freeze_source') and 'source_frame_times' not in clip,
            'Rate1 contiguous source clock required')
    require(clip['in'] <= start < old_in < out <= clip['out'] + 1e-8, 'Cover must stay within the same take')
    ids = {e['id'] for e in selected}
    # Keep at least 100 ms after the previous caption/graphic to avoid obscuring
    # its final reading time. This is an explicit candidate policy, not ASR proof.
    for e in p['layers']:
        if e['id'] not in ids and e['in'] < old_in and e['out'] > start - .1 + 1e-8:
            require(False, 'Another layer or its 100ms reading margin occupies the added cover: ' + e['id'])
    q = copy.deepcopy(p)
    for e in q['layers']:
        if e['id'] in ids: e['in'] = start
    record = {'event_id': decision['event_id'], 'layer_ids': sorted(ids), 'take_id': clip['id'],
              'old_in': old_in, 'new_in': start, 'out': out, 'start_frame': start_frame,
              'added_frames': round(old_in*fps)-start_frame,
              'added_source_range': [clip['source_in']+start-clip['in'], clip['source_in']+old_in-clip['in']],
              'historical_chapter_binding_preserved': True, 'audio_unchanged': q['audio'] == p['audio'],
              'additional_audio_muted': False, 'reason': decision['reason'], 'evidence': decision['evidence']}
    return q, record


def apply(project, decision, output):
    project, decision, output = [Path(x).absolute() for x in (project,decision,output)]
    require(output.suffix == '.json' and output.parent.resolve() == project.parent.resolve(), 'New JSON must be beside input')
    require(not os.path.lexists(output), 'Output exists')
    p, ph = read(project); d, dh = read(decision)
    require(ph == d['project_sha256'], 'Input project SHA differs')
    validate(p, project.parent, True); q, record = transform(p,d)
    q['source_chapter_cover'] = {'schema':'atrass-chapter-cover-v1','input_project':relative(project,output.parent),
        'input_sha256':ph,'decision':relative(decision,output.parent),'decision_sha256':dh,
        'implementation_sha256':sha(Path(__file__)),'record':record,'release_ready':False}
    q['release_ready'] = False; validate(q,output.parent,True)
    require(sha(project) == ph and sha(decision) == dh, 'Input changed during cover extension')
    with output.open('x',encoding='utf-8')as f: json.dump(q,f,ensure_ascii=False,indent=2,allow_nan=False); f.write('\n')
    return {'project':str(output),'sha256':sha(output),'added_frames':record['added_frames'],
            'audio_unchanged':True,'source_cuts_unchanged':q['clips']==p['clips'],'release_ready':False}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('project','decision','output'): parser.add_argument('--'+name,required=True)
    args=parser.parse_args(); print(json.dumps(apply(args.project,args.decision,args.output),ensure_ascii=False))
