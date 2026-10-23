#!/usr/bin/env python3
"""Counterexamples for caption ownership, negation and indivisible timing."""
import json
from balance_transcript_captions import propose


def transcript(words):
    return {'segments': [{'start': i * .4, 'end': i * .4 + .35, 'text': text,
                         'words': [{'start': i * .4, 'end': i * .4 + .35, 'word': text}]}
                        for i, text in enumerate(words)]}


def main():
    checks = []
    def check(ok, name):
        assert ok, name
        checks.append(name)
    def reject(name, callback):
        try:
            callback()
        except (ValueError, AssertionError):
            checks.append(name)
        else:
            raise AssertionError('Accepted: ' + name)
    words = ['그러니까', '필요', '없는', '행동은', '하지', '마세요.',
             '아니요', '아니요', '의도한', '반복입니다.']
    t = transcript(words)
    plan = {'fps': '60000/1001', 'takes': [{'source_in': 0, 'source_out': 10}]}
    fits = lambda text: len(text) <= 28
    r = propose(plan, t, [], [], 10, fits)
    check([ref for g in r['groups'] for ref in g['asr_refs']] == [[i, 0] for i in range(len(words))], 'every original identity exactly once and in order')
    check(' '.join(g['text'] for g in r['groups']) == ' '.join(words), 'negation and deliberate repetition unchanged')
    check(r['sentences'][0]['text'] == ' '.join(words[:6]), 'conjunction and noun ending yo do not force false sentence boundary')
    check(r['sentences'][1]['text'] == '아니요' and r['sentences'][2]['text'] == '아니요', 'repeated answers retained separately')
    explicit = propose(plan, t, [], [.8], 10, fits)
    check(any(g['source_range'][0] == .8 for g in explicit['groups']), 'agent-required boundary honored')
    reject('boundary inside a timed word rejected', lambda: propose(plan, t, [], [.9], 10, fits))
    reject('unknown removed identity rejected', lambda: propose(plan, t, [[99, 0]], [], 10, fits))
    removed = propose(plan, t, [[6, 0]], [], 10, fits)
    check(' '.join(g['text'] for g in removed['groups']).count('아니요') == 1, 'only explicitly named repetition removed')
    duplicate = {'fps': plan['fps'], 'takes': plan['takes'] * 2}
    reject('overlapping takes cannot duplicate speech', lambda: propose(duplicate, t, [], [], 10, fits))
    missing = {'fps': plan['fps'], 'takes': [{'source_in': 0, 'source_out': 1}]}
    reject('take cannot silently omit retained speech', lambda: propose(missing, t, [], [], 10, fits))
    reject('too-wide indivisible unit requires correction', lambda: propose(plan, transcript(['이 문구는 쪼갤 수 없습니다']), [], [], 10, lambda text: False))
    overlap = {'segments': [{'start': 0, 'end': 2, 'text': '않습니다.'},
                            {'start': 1, 'end': 3, 'text': '다음 문장입니다.'}]}
    merged = propose(plan, overlap, [], [], 10, fits)
    check(len(merged['groups']) == 1 and merged['groups'][0]['asr_refs'] == [[0, None], [1, None]], 'overlapping cues stay indivisible with both identities')
    check(merged['groups'][0]['source_range'] == [0, 3], 'overlap timing preserved without interpolation')
    check(any(w['reason'] == 'sentence_boundary_inside_indivisible_window' for w in merged['warnings']), 'unresolved internal sentence boundary exposed')
    reject('partial overlap deletion rejected', lambda: propose(plan, overlap, [[0, None]], [], 10, fits))
    reject('break inside overlap rejected', lambda: propose(plan, overlap, [], [2], 10, fits))
    long = {'segments': [{'start': 0, 'end': 5, 'text': '개별 시간 정보가 없는 긴 구절'}]}
    long_result = propose(plan, long, [], [], 10, fits)
    check(long_result['groups'][0]['source_range'] == [0, 5], 'long indivisible phrase retained')
    check(any(w['reason'] == 'indivisible_window_exceeds_target_limits' for w in long_result['warnings']), 'long window warning requires timing review')
    check(r['release_ready'] is False and r['automatic_semantic_approval'] is False, 'proposal never asserts final review')
    print(json.dumps({'pass': True, 'checks': len(checks), 'checked': checks}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
