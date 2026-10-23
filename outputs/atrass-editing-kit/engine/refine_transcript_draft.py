#!/usr/bin/env python3
"""Apply source-specific agent decisions to a conservative transcript draft.

Decisions name every removed ASR identity and retain an audit of its text. This
adapter does not guess that repeated speech is a retake. Caption groups are
rebuilt on each kept interval; no caption, word, or graphic is silently clipped.
"""
from pathlib import Path
from fractions import Fraction
import argparse
import bisect
import copy
import json
import math
import re

from draft_from_transcript import (POLICY, caption_groups, plan_from_evidence,
                                   speech_units)
from render import Composer
from review_movie import require
from run_longform import binding, read, save, sha, verify_bindings

SCHEMA = 'atrass-transcript-editorial-decisions-v1'


def exact_fields(value, fields, label):
    require(isinstance(value, dict) and set(value) == set(fields), 'Invalid '+label+' fields')


def refs(value):
    require(isinstance(value, list), 'ASR references must be an array')
    result = []
    for pair in value:
        require(isinstance(pair, list) and len(pair) == 2 and type(pair[0]) is int
                and pair[0] >= 0 and (pair[1] is None or type(pair[1]) is int and pair[1] >= 0), 'Invalid ASR identity')
        result.append(tuple(pair))
    require(len(result) == len(set(result)), 'Duplicate ASR identity')
    return set(result)


def complement(spans, end):
    result, previous = [], 0
    for a, b in spans:
        require(type(a) is int and type(b) is int and previous <= a < b <= end, 'Invalid ordered frame intervals')
        if previous < a:
            result.append([previous, a])
        previous = b
    if previous < end:
        result.append([previous, end])
    return result


def union(spans):
    result = []
    for a, b in sorted(spans):
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def transform(plan, transcript, decisions, identities, duration, fits):
    exact_fields(decisions, ['schema', 'source_sha256', 'transcript_sha256', 'input_plan_sha256', 'cuts', 'caption_breaks_source_seconds'], 'editorial decision')
    require(decisions['schema'] == SCHEMA, 'Unsupported editorial decisions schema')
    for key in ('source_sha256', 'transcript_sha256', 'input_plan_sha256'):
        require(decisions[key] == identities[key], key+' differs from actual input')
    require(plan.get('schema') == 'atrass-edit-plan-v1' and plan.get('variant') == 'A'
            and plan.get('automatic_draft', {}).get('policy') == POLICY, 'Expected conservative A transcript draft')
    fps = Fraction(plan['fps']); r = float(fps)
    require(fps == Fraction(60000, 1001), 'Unexpected draft frame clock')
    end = math.floor(duration*r+1e-8)
    units, normalization = speech_units(transcript, duration)
    font = plan['fonts']['body']['path']
    spans = []
    for take in plan['takes']:
        a, b = take['source_in']*r, take['source_out']*r
        require(abs(a-round(a)) < 1e-6 and abs(b-round(b)) < 1e-6, 'Draft source interval is not on the common frame clock')
        spans.append([round(a), round(b)])
    quiet = complement(spans, end)
    original_groups = caption_groups(units, fps, fits)
    expected, _ = plan_from_evidence(plan['media']['raw']['path'], duration,
        identities['source_sha256'], transcript, identities['transcript_sha256'], original_groups, quiet, fps, font)
    # Exact reconstruction prevents discarding altered captions, graphic events,
    # geometry, audio offsets, speed changes, or unrecognized plan extensions.
    require(plan == expected, 'Input must be the unmodified conservative draft; edited/style plans need their own retiming adapter')
    require(isinstance(decisions['cuts'], list) and decisions['cuts'], 'Explicit nonempty editorial cuts required')
    ledger, cut_frames, removed, used, previous = [], [], set(), set(), -1
    all_refs = {tuple(x) for u in units for x in u['refs']}
    for cut in decisions['cuts']:
        exact_fields(cut, ['id', 'source_range', 'remove_asr_refs', 'expected_text', 'reason', 'evidence'], 'cut')
        cid = cut['id']
        require(isinstance(cid, str) and re.fullmatch(r'[A-Za-z0-9_-]+', cid) and cid not in used, 'Unique safe cut ID required')
        used.add(cid)
        for field in ('reason', 'evidence'):
            require(isinstance(cut[field], str) and cut[field].strip(), 'Every cut needs '+field)
        pair = cut['source_range']
        require(isinstance(pair, list) and len(pair) == 2, 'Cut source range required')
        a, b = pair
        require(all(type(v) in (int, float) and math.isfinite(v) for v in pair)
                and 0 <= a < b <= duration and a >= previous, 'Cuts must be finite, sorted, disjoint, and inside common media coverage')
        previous = b
        named = refs(cut['remove_asr_refs'])
        require(named <= all_refs and not named & removed, 'Unknown or reused removed ASR identity')
        selected = [u for u in units if a < u['end']-1e-8 and b > u['start']+1e-8]
        for u in selected:
            require(a <= u['start']+1e-8 and b >= u['end']-1e-8, 'Cut crosses a word or indivisible overlapping cue; select complete ASR units')
        actual = {tuple(x) for u in selected for x in u['refs']}
        require(named == actual, 'Every removed speech identity must be explicitly named; no implicit dialogue deletion')
        text = ' '.join(u['text'] for u in selected)
        require(cut['expected_text'] == text, 'Removed ASR wording differs from the reviewed decision')
        lo, hi = math.ceil(a*r-1e-8), math.floor(b*r+1e-8)
        # The source tail is already truncated to common video/audio coverage.
        lo, hi = max(0, lo), min(end, hi)
        require(lo < hi, 'Editorial cut collapsed on the frame clock')
        require(any(lo < z and hi > x for x, z in spans), 'Editorial cut removes nothing beyond existing quiet cuts')
        removed |= named; cut_frames.append([lo, hi])
        ledger.append({**copy.deepcopy(cut), 'removed_source_frames': [lo, hi],
            'rendered_remove_source_range': [lo/r, hi/r],
            'inward_rounding_residual_seconds': [lo/r-a, b-hi/r],
            'interpretation': 'agent editorial candidate; ASR timing and audible join remain unverified'})
    combined = union(quiet+cut_frames)
    kept_spans = complement(combined, end)
    kept_units = [u for u in units if not set(map(tuple, u['refs'])) & removed]
    require(kept_spans and kept_units, 'Cannot remove the entire programme')
    breaks = decisions['caption_breaks_source_seconds']
    require(isinstance(breaks, list) and all(type(v) in (int, float) and math.isfinite(v)
            and 0 < v < duration for v in breaks) and breaks == sorted(set(breaks)), 'Caption breaks must be ordered unique source seconds')
    require(all(not u['start']+1e-8 < v < u['end']-1e-8 for v in breaks for u in kept_units),
            'Caption break crosses a spoken word or indivisible cue')
    groups = []
    for a, b in kept_spans:
        selected = [u for u in kept_units if a/r-1e-8 <= u['start'] and u['end'] <= b/r+1e-8]
        partitions = {}
        for unit in selected:
            partitions.setdefault(bisect.bisect_right(breaks, unit['start']+1e-8), []).append(unit)
        for partition in partitions.values():
            groups.extend(caption_groups(partition, fps, fits))
    kept_refs = [tuple(x) for g in groups for x in g['refs']]
    require(len(kept_refs) == len(set(kept_refs)) and set(kept_refs) | removed == all_refs
            and not set(kept_refs) & removed, 'Retained word escapes a kept take or ownership was lost/duplicated')
    output, report = plan_from_evidence(plan['media']['raw']['path'], duration,
        identities['source_sha256'], transcript, identities['transcript_sha256'], groups, combined, fps, font)
    output['title'] = '원본 전사 초안 · 촬영 대화와 리테이크 편집 후보'
    output['automatic_draft']['limitations'][0] = 'Explicit agent decisions applied; retained ASR wording and editorial/audio joins remain unverified.'
    output['editorial_refinement'] = {'schema': SCHEMA, 'input_sha256': identities,
        'decision_source': 'agent_authored_source_specific', 'semantic_decisions_inferred': False,
        'cuts': ledger, 'caption_breaks_source_seconds': breaks,
        'removed_ASR_identities': len(removed), 'retained_ASR_identities': len(kept_refs),
        'release_ready': False, 'direct_listening_seconds': 0,
        'boundary_policy': 'Round deletion inward on common output frame clock. Up to one frame of excluded boundary material may remain; no retained ASR unit may be clipped. Direct listening is still required.'}
    report.update(schema='atrass-transcript-editorial-report-v1', decisions=ledger, normalization=normalization,
        original_output_frames=sum(z-a for a,z in spans), removed_editorial_frames=sum(z-a for a,z in spans)-report['output_frames'],
        retained_ASR_refs=[list(x) for x in kept_refs], removed_ASR_refs=[list(x) for x in sorted(removed, key=lambda p:(p[0], -1 if p[1] is None else p[1]))],
        caption_count=len(groups), release_ready=False, direct_listening_seconds=0)
    return output, report


def build(plan_path, transcript_path, decisions_path, output):
    output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink(), 'Choose a new output directory')
    plan, transcript, decisions = map(read, (plan_path, transcript_path, decisions_path))
    raw = Path(plan['media']['raw']['path']).resolve(strict=True)
    font = Path(plan['fonts']['body']['path']).resolve(strict=True)
    require(sha(font) == '12e1fea375d27869a686d7432c1e6a38154d394994f5d82ace24f36a7db68333', 'Measured candidate font bytes differ')
    inputs = [binding(p) for p in (plan_path, transcript_path, decisions_path, raw, font)]
    inputs += [binding(p) for p in sorted(Path(__file__).parent.glob('*.py'))]
    identities = dict(zip(('input_plan_sha256', 'transcript_sha256', 'decisions_sha256', 'source_sha256'), [x['sha256'] for x in inputs[:4]]))
    require(transcript.get('source_sha256') == identities['source_sha256'], 'Transcript belongs to another raw source')
    import av
    with av.open(str(raw)) as c:
        require(c.streams.video and c.streams.audio, 'Original video/audio required')
        v, a = c.streams.video[0], c.streams.audio[0]
        require(v.start_time == 0 and a.start_time == 0 and v.duration is not None and a.duration is not None, 'Unsupported media coverage')
        duration = min(float(v.duration*v.time_base), float(a.duration*a.time_base))
    composer = Composer({'width':1920,'height':1080,'fonts':plan['fonts']}, output)
    from fontTools.ttLib import TTFont
    with TTFont(str(font), lazy=True) as ft:
        cmap = ft.getBestCmap()
    def fits(text):
        require(all(ch.isspace() or ord(ch) in cmap for ch in text), 'Missing glyph in retained transcript')
        mask = composer.text_mask(text, {'font':'body','font_size':54,'tracking':0})
        return 0 < mask.width <= POLICY['max_caption_ink_width'] and 0 < mask.height <= 64
    result, report = transform(plan, transcript, decisions, identities, duration, fits)
    verify_bindings(inputs)
    output.mkdir(parents=True, exist_ok=False)
    save(output/'edit-plan.json', result)
    save(output/'editorial-report.json', {**report, 'inputs':inputs})
    return {'plan':str(output/'edit-plan.json'), 'report':str(output/'editorial-report.json'),
            'output_frames':report['output_frames'], 'removed_editorial_frames':report['removed_editorial_frames'],
            'caption_count':report['caption_count'], 'release_ready':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'transcript', 'decisions', 'output-dir'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.plan, args.transcript, args.decisions, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
