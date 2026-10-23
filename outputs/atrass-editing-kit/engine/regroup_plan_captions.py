#!/usr/bin/env python3
"""Apply explicit caption groups while preserving every retained ASR identity.

This is editorial display timing, not word alignment. Overlapping ASR words
remain unchanged; a new display boundary uses the preceding group's word end.
No speech, footage, graphics, or original transcript is rewritten.
"""
from pathlib import Path
from fractions import Fraction
import argparse
import copy
import hashlib
import json
import math
import os
import unicodedata

from edit_caption_copy import spoken_characters
from compile_plan import complete_emphasis_mask
from render import Composer

SCHEMA = 'atrass-plan-caption-regroup-v1'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def transform(plan, transcript, spec, measure):
    require(spec['schema'] == SCHEMA and plan['schema'] == 'atrass-edit-plan-v1', 'Regroup schema')
    require(plan.get('release_ready') is False and spec['groups'], 'Draft and explicit groups required')
    require(transcript['source_sha256'] == spec['source_sha256'], 'Source identity differs')
    fps = Fraction(plan['fps']); clocks = {}; cursor = 0
    for take in plan['takes']:
        count = round((take['source_out'] - take['source_in']) / take.get('rate', 1) * float(fps))
        clocks[take['id']] = (take, cursor, cursor + count); cursor += count
    q = copy.deepcopy(plan); events = plan['events']; by = {e['id']: e for e in events}
    require(len(by) == len(events), 'Duplicate input event ID')
    used = set(); inserted = set(); replacements = {}; records = []
    all_refs = lambda rows: [r for e in rows for r in e.get('source_dialogue_binding', {}).get('asr_refs', [])]
    for group in spec['groups']:
        old_ids = group['old_ids']; parts = group['parts']
        require(old_ids and parts and group['reason'].strip(), 'Explicit group, parts and reason required')
        require(len(set(old_ids)) == len(old_ids) and not used.intersection(old_ids), 'Repeated source caption')
        old = [by[cid] for cid in old_ids]
        indices = [events.index(e) for e in old]
        require(indices == list(range(indices[0], indices[0] + len(old))), 'Old captions must be consecutive events')
        require(all(e['template'] == 'dialogue' for e in old), 'Only dialogue captions can regroup')
        binding = old[0]['source_dialogue_binding']; tid = binding['take_id']
        require(all(e['source_dialogue_binding']['take_id'] == tid and
                    e['source_dialogue_binding']['transcript_sha256'] == spec['transcript_sha256'] for e in old),
                'Do not cross a source take or transcript')
        require(all(abs(a['out'] - b['in']) < 1e-8 for a, b in zip(old, old[1:])), 'Do not fill unreviewed caption gaps')
        style = lambda e: {k: v for k, v in e.items() if k not in ('id', 'text', 'in', 'out', 'source_dialogue_binding')}
        require(all(style(e) == style(old[0]) for e in old), 'Caption styles differ')
        original_refs = all_refs(old); new_refs = [r for part in parts for r in part['asr_refs']]
        require(new_refs == original_refs and len(set(map(tuple, new_refs))) == len(new_refs), 'ASR ownership lost, reordered or duplicated')
        require(spoken_characters(''.join(e['text'] for e in old)) == spoken_characters(''.join(p['text'] for p in parts)),
                'Spoken characters changed')
        take, start_frame, end_frame = clocks[tid]; rate = take.get('rate', 1)
        require(plan['media'][take['source']]['sha256'] == spec['source_sha256'], 'Take source identity differs')
        def mapped_frame(t):
            return start_frame + math.ceil((t - take['source_in']) / rate * float(fps) - 1e-8)
        spans = []
        for part in parts:
            require(part['id'] not in inserted and (part['id'] not in by or part['id'] in old_ids), 'New caption ID collision')
            inserted.add(part['id']); require(part['asr_refs'] and part['text'].strip() == part['text'], 'Empty caption/ref or exterior space')
            require(part['text'] and unicodedata.normalize('NFC', part['text']) == part['text'] and
                    not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp') for c in part['text']),
                    'NFC single-line caption required')
            words = []
            for s, w in part['asr_refs']:
                require(type(s) is int and type(w) is int and s >= 0 and w >= 0, 'Invalid ASR identity')
                words.append(transcript['segments'][s]['words'][w])
            require(spoken_characters(part['text']) == spoken_characters(''.join(w['word'] for w in words)), 'Caption text does not match its ASR words')
            spans.append([min(w['start'] for w in words), max(w['end'] for w in words)])
        # Preserve outer display bounds, including previously reviewed frame holds.
        boundaries = [round(old[0]['in'] * float(fps))]
        boundaries += [mapped_frame(span[1]) for span in spans[:-1]]
        boundaries += [round(old[-1]['out'] * float(fps))]
        revised = []
        for n, (part, span) in enumerate(zip(parts, spans)):
            lo, hi = boundaries[n:n+2]
            require(start_frame <= lo < hi <= end_frame, 'Caption crosses its take or has reversed bounds')
            display = [take['source_in'] + (f - start_frame) / float(fps) * rate for f in (lo, hi)]
            delta = [display[i] - span[i] for i in range(2)]
            require(all(abs(x) <= .5 + 1 / float(fps) for x in delta), 'Display offset exceeds 500ms plus frame quantization')
            require(display[0] < span[1] and display[1] > span[0], 'Caption misses its entire ASR span')
            seconds = (hi - lo) / float(fps)
            require(.8 <= seconds <= 6, 'Reviewed display must last 0.8–6 seconds')
            e = copy.deepcopy(old[0]); e.update(id=part['id'], text=part['text'], **{'in': lo / float(fps), 'out': hi / float(fps)})
            b = copy.deepcopy(binding)
            b.update(source_range=span, asr_refs=part['asr_refs'], asr_text_verified=False,
                     editorial_display_timing={'source_range': display, 'offset_from_asr_envelope_seconds': delta,
                      'policy': 'preceding_group_word_end_with_outer_display_bounds_preserved',
                      'asr_word_times_changed': False, 'phoneme_alignment_verified': False})
            e['source_dialogue_binding'] = b
            width, height = measure(e)
            require(0 < width <= 1640 and 0 < height <= 64, 'Caption glyphs exceed the existing style safe area')
            revised.append(e)
        replacements[old_ids[0]] = revised; used.update(old_ids)
        records.append({'reason': group['reason'], 'before': old, 'after': revised, 'display_frames': boundaries,
                        'asr_words_preserved': len(original_refs)})
    q['events'] = [e for old in events for e in (replacements[old['id']] if old['id'] in replacements else [] if old['id'] in used else [copy.deepcopy(old)])]
    require(all_refs(q['events']) == all_refs(events), 'Global ASR ownership changed')
    require(len({e['id'] for e in q['events']}) == len(q['events']), 'Result ID collision')
    before_plain = {k: v for k, v in plan.items() if k != 'events'}
    require(before_plain == {k: v for k, v in q.items() if k != 'events'}, 'Non-caption plan data changed')
    q['caption_regroup_revision'] = {'schema': SCHEMA, 'input_plan_sha256': spec['input_plan_sha256'],
        'transcript_sha256': spec['transcript_sha256'], 'source_sha256': spec['source_sha256'],
        'decision_evidence': spec['evidence'], 'records': records, 'asr_word_times_changed': False,
        'speech_and_cut_instructions_unchanged': True, 'semantic_approval': False, 'release_ready': False}
    return q, records


def apply(plan_path, transcript_path, decision_path, output):
    paths = list(map(lambda p: Path(p).resolve(), (plan_path, transcript_path, decision_path)))
    output = Path(output).absolute(); require(output.parent.resolve() == paths[0].parent, 'Output must be beside the input plan')
    require(not os.path.lexists(output), 'Choose a new output path')
    hashes = list(map(sha, paths)); plan, transcript, spec = [json.loads(p.read_text()) for p in paths]
    require(hashes[:2] == [spec['input_plan_sha256'], spec['transcript_sha256']], 'Input hashes differ')
    require(spec.get('evidence', '').strip(), 'Evidence required')
    comp = Composer({'width': 1920, 'height': 1080, 'fonts': plan['fonts']}, paths[0].parent)
    def measure(e):
        layer = {'font': 'body', 'font_size': 54, 'tracking': -.5, **e}
        mask = comp.text_mask(e['text'], layer); full = complete_emphasis_mask(comp, e['text'], layer)
        require(mask.size == full.size and mask.tobytes() == full.tobytes(), 'Incomplete glyph raster')
        return mask.size
    q, records = transform(plan, transcript, spec, measure)
    require(list(map(sha, paths)) == hashes, 'Inputs changed during regroup')
    q['caption_regroup_revision']['decision_sha256'] = hashes[2]
    q['caption_regroup_revision']['implementation_sha256'] = sha(__file__)
    with output.open('x', encoding='utf-8') as f:
        json.dump(q, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')
    return {'output': str(output), 'sha256': sha(output), 'groups': len(records),
            'captions_before': sum(e['template'] == 'dialogue' for e in plan['events']),
            'captions_after': sum(e['template'] == 'dialogue' for e in q['events']), 'release_ready': False}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'transcript', 'decision', 'output'):
        ap.add_argument('--' + name, required=True)
    a = ap.parse_args()
    print(json.dumps(apply(a.plan, a.transcript, a.decision, a.output), ensure_ascii=False))
