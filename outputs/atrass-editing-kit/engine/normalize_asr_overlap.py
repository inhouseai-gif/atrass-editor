#!/usr/bin/env python3
"""Resolve duplicate ownership at known overlapping ASR chunk boundaries.

This edits a derived transcript only, never footage or audio. Distinct-time
repetitions, conflicting text and uncertain timings remain untouched. It is not
retake detection, transcript correctness, or direct listening approval.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

POLICY = {
    'id': 'atrass-asr-chunk-ownership-v1',
    'max_start_difference_seconds': .55,
    'max_end_difference_seconds': .08,
    'minimum_overlap_fraction_of_shorter_group': .50,
    'minimum_matching_groups': 2,
    'minimum_matching_characters': 4,
    'requires_known_context_overlap_provenance': True,
    'canonical_owner': 'earlier_segment_original_word_text_and_times',
    'empty_cue_policy': 'remove_only_zero_duration_whitespace_text_with_empty_words_and_no_tokens; retain_original_receipt',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text_key(value):
    # Do not remove internal punctuation, numbers, negation or letter case.
    return re.sub(r'\s', '', unicodedata.normalize('NFC', value)).rstrip('.,!?;:…。！？')


def valid_words(segment):
    words = segment.get('words')
    if not isinstance(words, list) or not words:
        return False
    previous = segment['start']
    for word in words:
        a, b = word.get('start'), word.get('end')
        if not (type(a) in (int, float) and type(b) in (int, float)
                and math.isfinite(a) and math.isfinite(b)
                and segment['start'] <= a < b <= segment['end'] + 1e-8
                and a >= previous - 1e-8
                and isinstance(word.get('word'), str) and text_key(word['word'])):
            return False
        previous = b
    return re.sub(r'\s', '', ''.join(w['word'] for w in words)) == re.sub(r'\s', '', segment['text'])


def timed_groups(left, right):
    """Match groups with the same character ends; never interpolate phonemes."""
    a = [text_key(w['word']) for w in left]
    b = [text_key(w['word']) for w in right]
    if ''.join(a) != ''.join(b) or len(''.join(a)) < POLICY['minimum_matching_characters']:
        return None
    if not any(unicodedata.category(c).startswith('L') for c in ''.join(a)):
        return None
    def boundaries(keys):
        total = 0
        result = {}
        for i, key in enumerate(keys):
            total += len(key)
            result[total] = i + 1
        return result
    la, rb = boundaries(a), boundaries(b)
    groups = []; ai = bi = 0
    for end in sorted(set(la) & set(rb)):
        aj, bj = la[end], rb[end]
        x, y = [left[ai]['start'], left[aj-1]['end']], [right[bi]['start'], right[bj-1]['end']]
        overlap = min(x[1], y[1]) - max(x[0], y[0])
        fraction = overlap / min(x[1]-x[0], y[1]-y[0])
        if (abs(x[0]-y[0]) > POLICY['max_start_difference_seconds'] + 1e-8
                or abs(x[1]-y[1]) > POLICY['max_end_difference_seconds'] + 1e-8
                or fraction < POLICY['minimum_overlap_fraction_of_shorter_group'] - 1e-8):
            return None
        groups.append({'left_words': [ai, aj], 'right_words': [bi, bj],
                       'left_source_range': x, 'right_source_range': y,
                       'same_text_key': ''.join(a[ai:aj]), 'overlap_fraction': fraction})
        ai, bi = aj, bj
    return groups if len(groups) >= POLICY['minimum_matching_groups'] else None


def normalize(transcript, input_sha256):
    require(isinstance(transcript, dict) and isinstance(transcript.get('segments'), list), 'Transcript segments required')
    identity = transcript.get('source_sha256')
    require(isinstance(identity, str) and re.fullmatch('[0-9a-f]{64}', identity) is not None, 'Source-bound transcript required')
    require('chunk_overlap_normalization' not in transcript, 'Already derived: preserve the prior report instead of normalizing twice')
    source = transcript['segments']
    require(source, 'Nonempty transcript required')
    previous = -1
    empty = {}
    for i, s in enumerate(source):
        require(isinstance(s, dict), 'Invalid cue')
        a, b = s.get('start'), s.get('end')
        require(all(type(v) in (int, float) and math.isfinite(v) for v in (a, b))
                and 0 <= a <= b and a >= previous, 'Invalid or unordered cue timing')
        if (a == b and isinstance(s.get('text'), str) and not s['text'].strip()
                and s.get('words') == [] and s.get('tokens', []) == []):
            empty[i] = copy.deepcopy(s)
            previous = a
            continue
        require(a < b, 'Nonempty zero-duration cue requires explicit timing repair')
        require(isinstance(s.get('text'), str) and s['text'].strip(), 'Empty cue text')
        previous = a
    require(len(empty) < len(source), 'No speech cues remain after empty metadata cleanup')
    provenance = str(transcript.get('chunk_method', ''))
    known = (re.fullmatch(r'mlx-whisper(?:\d+(?:\.\d+)*)?', str(transcript.get('engine', ''))) is not None
             and 'ownership' in provenance and 'overlap' in provenance)
    removals = {}; decisions = []; kept = []; origins = []
    for i, original in enumerate(source):
        if i in empty:
            decisions.append({'input_segment': i, 'action': 'remove_empty_metadata_cue',
                              'reason': 'zero_duration_no_text_words_or_decoder_tokens',
                              'original_cue': empty[i], 'source_video_or_audio_edited': False})
            continue
        segment = copy.deepcopy(original)
        chosen = None
        if kept and original['start'] < kept[-1]['end'] - 1e-8:
            earlier = kept[-1]; earlier_origin = origins[-1]
            reason = 'unknown_chunk_context_provenance' if not known else None
            if not reason and (not valid_words(earlier) or not valid_words(original)):
                reason = 'invalid_or_missing_word_alignment_preserved'
            matches = []
            if not reason:
                lw, rw = earlier['words'], original['words']
                for lo in range(len(lw)):
                    for hi in range(1, len(rw)+1):
                        # A matching suffix must not hide a conflicting earlier
                        # word whose midpoint belongs to the later prefix. This
                        # catches e.g. '안 되는 일' versus '되는 일' at the same time.
                        if any(rw[0]['start'] <= (w['start']+w['end'])/2 < rw[hi-1]['end']
                               for w in lw[:lo]):
                            continue
                        groups = timed_groups(lw[lo:], rw[:hi])
                        if groups:
                            matches.append((lo, hi, groups))
                if len(matches) == 1:
                    lo, hi, groups = matches[0]
                    chosen = (lo, hi, groups)
                else:
                    reason = 'ambiguous_overlap_preserved' if matches else 'conflicting_text_or_timing_preserved'
            row = {'earlier_input_segment': earlier_origin['input_segment'], 'later_input_segment': i,
                   'action': 'collapse_duplicate_analysis_ownership' if chosen else 'preserve',
                   'reason': reason or 'one_exact_text_suffix_prefix_with_overlapping_word_group_times',
                   'direct_listening_seconds': 0}
            if chosen:
                lo, hi, groups = chosen
                require(i not in removals, 'Duplicate removal ownership')
                removals[i] = hi
                row.update(removed_prefix_word_indices=list(range(hi)),
                    canonical_word_indices=earlier_origin['input_word_indices'][lo:],
                    canonical_text=''.join(w['word'] for w in earlier['words'][lo:]),
                    removed_duplicate_text=''.join(w['word'] for w in original['words'][:hi]),
                    groups=groups, original_later_cue=original)
                rest = segment['words'][hi:]
                if rest:
                    segment.update(words=rest, start=rest[0]['start'], end=rest[-1]['end'],
                                   text=''.join(w['word'] for w in rest))
                    # These decoder-token/confidence fields described the whole
                    # original cue; preserve them in the receipt, not as new scores.
                    for key in ('tokens', 'avg_logprob', 'compression_ratio', 'no_speech_prob'):
                        segment.pop(key, None)
            decisions.append(row)
        if chosen and chosen[1] == len(original['words']):
            continue
        kept.append(segment)
        origins.append({'input_segment': i,
                        'input_word_indices': list(range(removals.get(i, 0), len(original.get('words', [])))),
                        'unchanged_whole_cue': not bool(chosen)})
    removed_count = sum(removals.values())
    report = {'schema': 'atrass-asr-chunk-ownership-v1', 'policy': POLICY,
              'input_transcript_sha256': input_sha256, 'source_sha256': transcript['source_sha256'],
              'known_chunk_context_provenance': known, 'decisions': decisions,
              'output_segment_origins': origins, 'input_cues': len(source), 'output_cues': len(kept),
              'duplicate_word_records_collapsed': removed_count,
              'empty_metadata_cues_removed': len(empty),
              'source_video_or_audio_edited': False, 'retakes_deleted': False,
              'transcript_text_verified': False, 'direct_listening_seconds': 0, 'release_ready': False}
    result = copy.deepcopy(transcript)
    result['segments'] = kept
    result['chunk_overlap_normalization'] = {k: v for k, v in report.items() if k not in ('decisions', 'output_segment_origins')}
    return result, report


def apply(input_path, output_dir):
    path = Path(input_path).resolve(strict=True); out = Path(output_dir).absolute()
    require(not out.exists() and not out.is_symlink(), 'Choose a new output directory')
    before = sha(path)
    result, report = normalize(json.loads(path.read_text()), before)
    require(sha(path) == before, 'Input transcript changed during normalization')
    out.mkdir(parents=True, exist_ok=False)
    def save(p, data): p.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    save(out/'transcript.json', result)
    report.update(output_transcript_sha256=sha(out/'transcript.json'), implementation_sha256=sha(Path(__file__)))
    save(out/'normalization.json', report)
    return {'transcript': str(out/'transcript.json'), 'report': str(out/'normalization.json'),
            'duplicate_word_records_collapsed': report['duplicate_word_records_collapsed'], 'release_ready': False}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--transcript', required=True)
    ap.add_argument('--output-dir', required=True)
    args = ap.parse_args()
    print(json.dumps(apply(args.transcript, args.output_dir), ensure_ascii=False, indent=2))
