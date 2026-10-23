#!/usr/bin/env python3
"""Propose balanced Korean captions after explicit source editorial decisions.

This changes grouping only: ASR words, timing, ownership and all cut decisions
stay intact. Sentence endings are a heuristic, never a linguistic approval.
The original conservative draft remains immutable for reproducible replays.
"""
from pathlib import Path
from fractions import Fraction
import argparse
import bisect
import copy
import json
import math
import re

from draft_from_transcript import POLICY, speech_units
from refine_transcript_draft import build as refine, refs
from render import Composer
from review_movie import require
from run_longform import binding, read, save, verify_bindings

SCHEMA = 'atrass-caption-balance-proposal-v1'
ENDING = re.compile(r'(?:[.!?。！？]$|(?:니다|니까|요|죠)$)')
ENDING_EXCEPTIONS = {'필요', '주요', '수요', '그러니까'}
CLAUSE_ENDING = re.compile(r'(?:고|면|는데|지만|때|처럼|거나|서|보다)[,]?$')


def propose(plan, transcript, removed_refs, required_breaks, duration, fits):
    """Return source-bound groups without moving or inventing a word boundary."""
    fps = Fraction(plan['fps'])
    units, normalization = speech_units(transcript, duration)
    removed = refs(removed_refs)
    all_refs = {tuple(x) for u in units for x in u['refs']}
    require(removed <= all_refs, 'Unknown removed ASR identity')
    for unit in units:
        identities = set(map(tuple, unit['refs']))
        require(not identities & removed or identities <= removed,
                'Cannot partly remove an indivisible ASR window')
    kept = [u for u in units if not set(map(tuple, u['refs'])) & removed]
    require(kept, 'No retained speech')
    require(isinstance(required_breaks, list) and all(type(t) in (int, float)
            and math.isfinite(t) and 0 < t < duration for t in required_breaks)
            and required_breaks == sorted(set(required_breaks)), 'Invalid required caption boundaries')
    require(all(not u['start'] + 1e-8 < t < u['end'] - 1e-8
                for u in kept for t in required_breaks), 'Caption boundary splits an indivisible ASR window')
    groups, sentences = [], []
    for take in plan['takes']:
        selected = [u for u in kept if take['source_in'] - 1e-8 <= u['start']
                    and u['end'] <= take['source_out'] + 1e-8]
        chunks = []
        for unit in selected:
            split = not chunks
            if chunks:
                prev = chunks[-1][-1]
                split = (unit['start'] - prev['end'] > POLICY['caption_split_gap_seconds'] + 1e-8
                         or bool(ENDING.search(prev['text'])) and prev['text'] not in ENDING_EXCEPTIONS
                         or bisect.bisect_right(required_breaks, unit['start'] + 1e-8)
                         != bisect.bisect_right(required_breaks, prev['start'] + 1e-8))
            if split:
                chunks.append([])
            chunks[-1].append(unit)
        for chunk in chunks:
            n = len(chunk)
            best = [None] * (n + 1)
            best[n] = (0, [])
            for i in reversed(range(n)):
                choices = []
                for j in range(i + 1, n + 1):
                    text = ' '.join(u['text'] for u in chunk[i:j])
                    seconds = chunk[j - 1]['end'] - chunk[i]['start']
                    if j > i + 1 and (len(text) > POLICY['max_caption_characters']
                                      or seconds > POLICY['max_caption_seconds']):
                        break
                    if not fits(text) or best[j] is None:
                        continue
                    if math.ceil(chunk[j - 1]['end'] * float(fps) - 1e-8) <= math.ceil(chunk[i]['start'] * float(fps) - 1e-8):
                        continue
                    size = len(re.sub(r'\s', '', text))
                    cost = 100 + ((size - 19) / 8) ** 2 + max(0, 9 - size) ** 2 * 5 + max(0, 1 - seconds) ** 2 * 30
                    if j < n and not CLAUSE_ENDING.search(chunk[j - 1]['text']):
                        cost += 3
                    choices.append((cost + best[j][0], [(i, j)] + best[j][1]))
                require(bool(choices), 'Cannot fit retained ASR window; correct timing or typography before splitting: ' + str(chunk[i]['refs']))
                best[i] = min(choices, key=lambda choice: choice[0])
            sentences.append({'source_range': [chunk[0]['start'], chunk[-1]['end']],
                              'text': ' '.join(u['text'] for u in chunk), 'groups': len(best[0][1])})
            for a, b in best[0][1]:
                part = chunk[a:b]
                groups.append({'source_range': [part[0]['start'], part[-1]['end']],
                               'text': ' '.join(u['text'] for u in part),
                               'asr_refs': [r for u in part for r in u['refs']]})
    retained_refs = [tuple(x) for group in groups for x in group['asr_refs']]
    require(len(retained_refs) == len(set(retained_refs)) and set(retained_refs) == all_refs - removed,
            'Retained ASR ownership lost or duplicated across takes')
    warnings = []
    for index, group in enumerate(groups):
        seconds = group['source_range'][1] - group['source_range'][0]
        if seconds < .8:
            warnings.append({'group_index': index, 'reason': 'short_display_window', 'seconds': seconds})
        if seconds > POLICY['max_caption_seconds'] or len(group['text']) > POLICY['max_caption_characters']:
            warnings.append({'group_index': index, 'reason': 'indivisible_window_exceeds_target_limits'})
        if re.search(r'[.!?。！？]\s+\S', group['text']):
            warnings.append({'group_index': index, 'reason': 'sentence_boundary_inside_indivisible_window'})
    return {'schema': SCHEMA, 'groups': groups, 'sentences': sentences, 'warnings': warnings,
            'normalization': normalization, 'policy': copy.deepcopy(POLICY),
            'word_ownership_and_timing_unchanged': True, 'automatic_semantic_approval': False,
            'direct_listening_seconds': 0, 'release_ready': False}


def build(plan_path, transcript_path, decisions_path, output):
    output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink(), 'Choose a new output directory')
    inputs = [binding(p) for p in (plan_path, transcript_path, decisions_path)]
    # Existing refinement validates real source bytes, exact draft reconstruction,
    # complete cut identities, glyphs, common coverage and required boundaries.
    initial = refine(plan_path, transcript_path, decisions_path, output / '01-existing-decisions')
    plan, report, transcript, decisions = map(read, (initial['plan'], initial['report'], transcript_path, decisions_path))
    duration = max(t['source_out'] for t in plan['takes'])
    # Retained tail may end before the raw transcript; use the real coverage.
    import av
    with av.open(plan['media']['raw']['path']) as container:
        video, audio = container.streams.video[0], container.streams.audio[0]
        duration = min(float(video.duration * video.time_base), float(audio.duration * audio.time_base))
    composer = Composer({'width': 1920, 'height': 1080, 'fonts': plan['fonts']}, output)
    fit_cache = {}
    def fits(text):
        if text not in fit_cache:
            mask = composer.text_mask(text, {'font': 'body', 'font_size': 54, 'tracking': 0})
            fit_cache[text] = 0 < mask.width <= POLICY['max_caption_ink_width'] and 0 < mask.height <= 64
        return fit_cache[text]
    proposal = propose(plan, transcript, report['removed_ASR_refs'], decisions['caption_breaks_source_seconds'], duration, fits)
    balanced = copy.deepcopy(decisions)
    balanced['caption_breaks_source_seconds'] = sorted(set(decisions['caption_breaks_source_seconds']
        + [g['source_range'][0] for g in proposal['groups'] if g['source_range'][0] > 0]))
    verify_bindings(inputs)
    save(output / 'decisions.json', balanced)
    final = refine(plan_path, transcript_path, output / 'decisions.json', output / '02-balanced')
    actual = read(final['plan'])
    require(len(actual['events']) == len(proposal['groups']), 'Caption grouping differs from the validated renderer adapter')
    for event, group in zip(actual['events'], proposal['groups']):
        require(event['text'] == group['text'] and event['source_dialogue_binding']['asr_refs'] == group['asr_refs'],
                'Caption content or ownership changed in adapter')
    require(actual['takes'] == plan['takes'], 'Caption grouping changed source cuts')
    verify_bindings(inputs)
    proposal.update(inputs=inputs, source_sha256=decisions['source_sha256'],
                    transcript_sha256=decisions['transcript_sha256'], source_cuts_unchanged=True)
    save(output / 'proposal.json', proposal)
    result = {**final, 'decisions': str(output / 'decisions.json'), 'proposal': str(output / 'proposal.json'),
              'warning_count': len(proposal['warnings']), 'source_cuts_unchanged': True}
    save(output / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'transcript', 'decisions', 'output-dir'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.plan, args.transcript, args.decisions, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
