#!/usr/bin/env python3
"""Regroup safety checks: preserve words and reject bad ownership/timing."""
import copy
import json
from regroup_plan_captions import transform, SCHEMA


def fixture():
    t = {'source_sha256': 'raw-sha', 'segments': [{'words': [
        {'word': '첫', 'start': 0, 'end': 1}, {'word': '문장입니다.', 'start': 1, 'end': 2},
        {'word': '그', 'start': 1.8, 'end': 2.2}, {'word': '다음말입니다.', 'start': 2.2, 'end': 4}]}]}
    def event(cid, text, start, end, refs):
        return {'id': cid, 'template': 'dialogue', 'text': text, 'in': start, 'out': end,
                'source_dialogue_binding': {'take_id': 'take', 'transcript_sha256': 'transcript-sha',
                    'source_range': [start, end], 'asr_refs': [[0, r] for r in refs]}}
    p = {'schema': 'atrass-edit-plan-v1', 'release_ready': False, 'fps': '30',
         'media': {'raw': {'sha256': 'raw-sha'}}, 'takes': [{'id': 'take', 'source': 'raw', 'source_in': 0, 'source_out': 10}],
         'events': [event('a', '첫 문장입니다. 그', 0, 2.2, [0, 1, 2]), event('b', '다음말입니다.', 2.2, 4, [3])]}
    s = {'schema': SCHEMA, 'input_plan_sha256': 'plan-sha', 'source_sha256': 'raw-sha', 'transcript_sha256': 'transcript-sha',
         'evidence': 'Synthetic ASR overlap', 'groups': [{'old_ids': ['a', 'b'], 'reason': 'Sentence boundary',
         'parts': [{'id': 'a', 'text': '첫 문장입니다.', 'asr_refs': [[0, 0], [0, 1]]},
                   {'id': 'b', 'text': '그 다음말입니다.', 'asr_refs': [[0, 2], [0, 3]]}]}]}
    return p, t, s


def main():
    p, t, s = fixture(); originals = copy.deepcopy((p, t, s))
    q, records = transform(p, t, s, lambda e: (600, 54))
    assert q['events'][0]['out'] == q['events'][1]['in'] == 2
    assert q['events'][1]['source_dialogue_binding']['source_range'] == [1.8, 4]
    assert abs(q['events'][1]['source_dialogue_binding']['editorial_display_timing']['offset_from_asr_envelope_seconds'][0] - .2) < 1e-8
    assert (p, t, s) == originals and q['takes'] == p['takes']
    merged = copy.deepcopy(s); merged['groups'][0]['parts'] = [{'id': 'a', 'text': '첫 문장입니다. 그 다음말입니다.', 'asr_refs': [[0, i] for i in range(4)]}]
    assert len(transform(p, t, merged, lambda e: (800, 54))[0]['events']) == 1
    def reject(name, change, measure=lambda e: (600, 54)):
        p, t, s = fixture(); change(p, t, s)
        try:
            transform(p, t, s, measure)
        except (ValueError, KeyError, IndexError):
            return name
        raise AssertionError('Accepted invalid input: ' + name)
    rejected = [
        reject('lost ASR word', lambda p,t,s: s['groups'][0]['parts'][1]['asr_refs'].pop()),
        reject('duplicate ASR word', lambda p,t,s: s['groups'][0]['parts'][1]['asr_refs'].append([0, 2])),
        reject('reordered ASR word', lambda p,t,s: s['groups'][0]['parts'][1]['asr_refs'].reverse()),
        reject('changed meaning', lambda p,t,s: s['groups'][0]['parts'][0].update(text='첫 문장이 아닙니다.')),
        reject('cross take', lambda p,t,s: p['events'][1]['source_dialogue_binding'].update(take_id='other')),
        reject('wrong source', lambda p,t,s: p['media']['raw'].update(sha256='other')),
        reject('wrong transcript', lambda p,t,s: s.update(transcript_sha256='other')),
        reject('unreviewed caption gap', lambda p,t,s: p['events'][1].update(**{'in': 2.3})),
        reject('display offset over limit', lambda p,t,s: t['segments'][0]['words'][2].update(start=1.0)),
        reject('duplicate output ID', lambda p,t,s: s['groups'][0]['parts'][1].update(id='a')),
        reject('line break', lambda p,t,s: s['groups'][0]['parts'][1].update(text='그\n다음말입니다.')),
        reject('different typography', lambda p,t,s: p['events'][1].update(font='other')),
        reject('width overflow', lambda p,t,s: None, lambda e: (1641, 54)),
        reject('height overflow', lambda p,t,s: None, lambda e: (800, 65)),
        reject('short display', lambda p,t,s: t['segments'][0]['words'][1].update(end=.7)),
    ]
    print(json.dumps({'passed': True, 'positive_cases': 2, 'rejected_cases': rejected, 'release_ready': False}, indent=2))


if __name__ == '__main__':
    main()
