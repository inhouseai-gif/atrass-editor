#!/usr/bin/env python3
"""Portable new-source graph tests; real encoded movie tests are separate."""
from pathlib import Path
import argparse
import copy
import json
import subprocess
from production import prepare, build, read, save, sha, speech_units, select_units, ffmpeg_path, apply_framing, balanced_groups


def run(output):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, value):
        assert value, name
        checks.append(name)
    def reject(name, function):
        try:
            function()
        except ValueError:
            checks.append(name)
        else:
            raise AssertionError(name+' did not reject')
    ff = ffmpeg_path()
    raw = output/'unseen source.mov'
    subprocess.run([ff, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=48',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=48', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-crf', '30', '-c:a', 'pcm_s16le', str(raw)], check=True)
    identity = sha(raw)
    transcript = {'source_sha256': identity, 'segments': [
        {'start': i*2+.2, 'end': i*2+1.8, 'text': '같은 말도 지우지 않습니다.', 'words': [
            {'word': '같은 말도', 'start': i*2+.2, 'end': i*2+.9},
            {'word': ' 지우지 않습니다.', 'start': i*2+.9, 'end': i*2+1.8}]} for i in range(24)]}
    asr = output/'transcript.json'; save(asr, transcript)
    prepare(raw, 'shorts', output/'input', asr, count=3)
    job = output/'input/job.json'; units = read(job.parent/'units.json')['units']
    cs = [{'id': 'pick-'+str(i), 'title': '후보 '+str(i), 'keep': [[i*2, i*2+21]],
        'hook': '같은 말도', 'turn': '부정어를 보존', 'ending': '지우지 않습니다.', 'reason': '검사 용도이며 콘텐츠 추천 아님', 'score': 90-i} for i in range(10)]
    clips = [{'id': c['id'], 'title': c['title'], 'keep': c['keep'], 'reason': c['reason'], 'events': [],
              'headline': ['새로운 원본에서도', '부정어를 보존'], 'caption_breaks': [c['keep'][0][0]+8]} for c in cs[:3]]
    d = {'schema': 'atrass-agent-production-v1', 'job_sha256': sha(job), 'candidates': cs, 'clips': clips}
    path = output/'decisions.json'; save(path, d)
    result = build(job, path, output/'build')
    check('requested_three_shorts_are_all_compiled', len(result['projects']) == 3)
    projects = [read(p['project']) for p in result['projects']]
    check('unknown_source_hash_bound_without_episode_catalog', all(p['media']['raw']['sha256'] == identity for p in projects))
    check('portrait_canvas_and_candidate_specific_new_headline', all((p['width'], p['height']) == (1080, 1920) and any(l.get('text') == '새로운 원본에서도' for l in p['layers']) for p in projects))
    check('original_voice_only', all(a['source'] == 'raw' and a['rate'] == 1 for p in projects for a in p['audio']))
    check('semantic_and_audio_approval_not_fabricated', all(not p['release_ready'] and p['production_evidence']['direct_listening_seconds'] == 0 for p in projects))
    check('all_retained_negations_preserved', all(sum(l.get('text', '').count('않습니다.') for l in p['layers']) == 11 for p in projects))
    def bad(name, change):
        value = copy.deepcopy(d); change(value); f = output/(name+'.json'); save(f, value)
        reject(name, lambda: build(job, f, output/name))
    bad('wrong_job_identity', lambda x: x.update(job_sha256='0'*64))
    bad('requested_count_cannot_be_dropped', lambda x: x['clips'].pop())
    bad('nine_candidates_rejected', lambda x: x['candidates'].pop())
    bad('renamed_duplicate_candidate_rejected', lambda x: x['candidates'][9].update(keep=x['candidates'][0]['keep']))
    bad('unsupported_event_not_ignored', lambda x: x['clips'][0].update(events=[{'kind': 'unknown'}]))
    bad('source_mismatch_in_selection', lambda x: x['clips'][0].update(keep=[[1, 20]]))
    bad('unknown_extra_directive_rejected', lambda x: x.update(execute='untrusted data'))
    bad('outside_unit_rejected', lambda x: x['candidates'][9].update(keep=[[0, 500]]))
    bad('missing_glyph_rejected', lambda x: x['clips'][0].update(headline=['새 문구', '\U0010ffff']))
    bad('bad_correction_not_applied', lambda x: x['clips'][0].update(corrections=[{'unit': 0, 'before': '없는 말', 'after': '정정', 'reason': 'test'}]))
    reject('duplicate_retained_units_rejected', lambda: select_units(units, [[0, 5], [5, 10]]))
    reject('reversed_meaning_order_rejected', lambda: select_units(units, [[9, 10], [0, 3]]))
    zero = {'segments': [{'start': 0, 'end': 1, 'text': '절대 삭제하지 않습니다.', 'words': [
        {'word': '절대', 'start': 0, 'end': 0}, {'word': ' 삭제하지', 'start': 0, 'end': .5}, {'word': ' 않습니다.', 'start': .5, 'end': 1}]}]}
    u, repairs = speech_units(zero, 2)
    check('zero_word_timing_keeps_every_negation_without_interpolation', ' '.join(x['text'] for x in u) == '절대 삭제하지 않습니다.' and u[0]['start'] == 0 and u[0]['end'] == .5 and bool(repairs))
    early = {'segments': [{'start': .5, 'end': 1.4, 'text': '절대 안 돼요.', 'words': [
        {'word': '절대', 'start': .2, 'end': .6}, {'word': ' 안 돼요.', 'start': .6, 'end': 1.4}]}]}
    u, r = speech_units(early, 2)
    check('observed_word_start_before_coarse_segment_is_not_truncated', u[0]['start'] == .2 and u[0]['refs'] == [[0, 0]] and r[0]['method'] == 'preserve_observed_word_envelope')
    late = copy.deepcopy(early); late['segments'][0]['end'] = 1.1
    u, r = speech_units(late, 2)
    check('observed_word_tail_after_segment_is_not_truncated', u[-1]['end'] == 1.4 and r[0]['word_range'] == [.2, 1.4])
    invalid = copy.deepcopy(early); invalid['segments'][0]['words'][0]['start'] = -.1
    u, r = speech_units(invalid, 2)
    check('negative_word_time_never_makes_a_source_clip', len(u) == 1 and u[0]['start'] == .5 and u[0]['refs'] == [[0, None]])
    parts = [{'start': 0, 'end': 3.9, 'text': '상황을 정확히 이해하시길 추천', 'refs': [[0, 0]], 'cue_indices': [0]},
             {'start': 3.9, 'end': 4.3, 'text': '드립니다.', 'refs': [[0, 1]], 'cue_indices': [0]}]
    g = balanced_groups(parts, lambda text: True)
    check('automatic_short_tail_rejoins_without_losing_text_or_source_refs', len(g) == 1 and g[0]['text'] == '상황을 정확히 이해하시길 추천 드립니다.' and g[0]['refs'] == [[0, 0], [0, 1]] and (g[0]['start'], g[0]['end']) == (0, 4.3))
    check('agent_caption_boundary_not_merged', len(balanced_groups(parts[:1], lambda text: True)+balanced_groups(parts[1:], lambda text: True)) == 2)
    parts[0]['text'] += '.'
    check('completed_sentence_not_merged_with_next_short_statement', len(balanced_groups(parts, lambda text: True)) == 2)
    broken = copy.deepcopy(transcript); broken['source_sha256'] = 'f'*64; save(output/'foreign-transcript.json', broken)
    reject('foreign_transcript_cannot_bind_to_new_source', lambda: prepare(raw, 'longform', output/'foreign-input', output/'foreign-transcript.json'))
    prepare(raw, 'longform', output/'long-input', asr)
    long_job = output/'long-input/job.json'
    ld = {'schema': d['schema'], 'job_sha256': sha(long_job), 'clips': [{'id': 'new-episode', 'title': '새 원본 검사', 'reason': '테스트', 'keep': [[0, 21]], 'events': [
        {'kind': 'chapter', 'source_range': [0.2, 2], 'text': '새로운 챕터', 'number': 1, 'reason': 'test'},
        {'kind': 'chat', 'source_range': [3, 5], 'sender': '새 발신자', 'text': '새 원본의 문장입니다', 'reason': 'test', 'left': 1190},
        {'kind': 'illustration', 'source_range': [5, 7], 'asset': 'illustr-man-alone-teal', 'reason': 'test'},
        {'kind': 'emphasis', 'source_range': [8, 10], 'text': '같은 말도 지우지 않습니다', 'reason': 'test'}]}]}
    save(output/'long-decisions.json', ld); lr = build(long_job, output/'long-decisions.json', output/'long-build')
    lp = read(lr['projects'][0]['project'])
    check('landscape_graph_contains_all_requested_effect_families', all(any(l['id'].startswith(prefix) for l in lp['layers']) for prefix in ['chapter-', 'chat-', 'illustration-', 'emphasis-']))
    check('template_assets_resolve_after_job_path_change', all(Path(x['path']).is_absolute() and Path(x['path']).is_file() for g in ['media', 'fonts'] for x in lp[g].values()))
    check('reviewed_right_side_chat_position_fits_canvas', next(l for l in lp['layers'] if l['id'].startswith('chat-') and l['kind'] == 'rect')['position'][0] == 1190)
    overflow = copy.deepcopy(ld); overflow['clips'][0]['events'][1]['left'] = 1281
    save(output/'chat-overflow.json', overflow)
    reject('chat_outside_canvas_rejected', lambda: build(long_job, output/'chat-overflow.json', output/'chat-overflow'))
    framed = copy.deepcopy(lp)
    maps = read(output/'long-build/new-episode/edit-ledger.json')['take_mapping']
    meta = read(long_job)['metadata']
    event = {'source_range': [1.2, 3.2], 'zoom': 1.045, 'reason': 'test explicit source framing'}
    apply_framing(framed, [event], maps, meta)
    check('punch_in_splits_video_without_touching_audio_or_captions', len(framed['clips']) > len(lp['clips']) and framed['audio'] == lp['audio'] and framed['layers'] == lp['layers'])
    def source_at(clips, n):
        c = next(c for c in clips if round(c['in']*30) <= n < round(c['out']*30))
        return c['source_in']+(n/30-c['in'])*c.get('rate', 1)
    check('every_output_frame_retains_original_source_clock_after_framing', all(abs(source_at(lp['clips'], n)-source_at(framed['clips'], n)) < 1e-7 for n in range(round(lp['duration']*30))))
    reject('overlapping_framing_rejected', lambda: apply_framing(copy.deepcopy(lp), [event, event], maps, meta))
    reject('extreme_framing_rejected', lambda: apply_framing(copy.deepcopy(lp), [{**event, 'zoom': 2}], maps, meta))
    bad('portrait_face_position_not_overridden_by_landscape_zoom', lambda x: x['clips'][0].update(framing=[event]))
    check('source_not_modified', sha(raw) == identity)
    save(output/'validation.json', {'pass': True, 'count': len(checks), 'checks': checks, 'actual_rendered_frames': 0,
        'scope': 'Synthetic unseen input and rejection tests. Actual filmed MP4 renders recorded separately.', 'release_ready': False})
    print(json.dumps(read(output/'validation.json'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--output-dir', required=True); a = ap.parse_args(); run(a.output_dir)
