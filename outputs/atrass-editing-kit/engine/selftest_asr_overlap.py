#!/usr/bin/env python3
"""Counterexamples for timestamp-bound ASR aliasing; never speech deletion."""
import argparse
import copy
import json
from pathlib import Path
from normalize_asr_overlap import normalize, timed_groups, text_key


def word(text, start, end):
    return {'word': ' '+text, 'start': start, 'end': end}


def cue(words):
    return {'start': words[0]['start'], 'end': words[-1]['end'],
            'text': ''.join(w['word'] for w in words), 'words': words,
            'tokens': [1, 2, 3], 'avg_logprob': -.1}


def fixture():
    return {'engine': 'mlx-whisper0.4.3', 'source_sha256': 'a'*64,
            'chunk_method': '120s ownership windows/2s context overlap',
            'segments': [cue([word('정말', 0, .3), word('안', .3, .5), word('되는', .5, .8), word('일입니다', .8, 1.3)]),
                         cue([word('안되는', .3, .8), word('일입니다.', .8, 1.3), word('다음', 1.4, 1.7), word('문장입니다.', 1.7, 2.3)]),
                         cue([word('안되는', 3, 3.5), word('일입니다.', 3.5, 4)])]}


def run(output):
    out = Path(output).absolute(); out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, result):
        assert result, name
        checks.append(name)
    def reject(name, doc):
        try: normalize(doc, 'b'*64)
        except ValueError: checks.append(name)
        else: raise AssertionError(name)
    source = fixture(); before = copy.deepcopy(source)
    result, report = normalize(source, 'b'*64)
    check('input_is_unchanged', source == before)
    check('two_duplicate_records_collapsed_not_real_repetition', report['duplicate_word_records_collapsed'] == 2)
    check('negation_and_original_space_preserved', result['segments'][0] == source['segments'][0])
    check('different_time_repetition_preserved', result['segments'][2] == source['segments'][2])
    check('remaining_suffix_text_and_original_times', result['segments'][1]['words'] == source['segments'][1]['words'][2:]
          and result['segments'][1]['start'] == 1.4 and result['segments'][1]['text'] == ' 다음 문장입니다.')
    check('obsolete_full_cue_decoder_scores_removed', 'tokens' not in result['segments'][1] and 'avg_logprob' not in result['segments'][1])
    check('original_removed_cue_preserved_in_receipt', report['decisions'][0]['original_later_cue'] == source['segments'][1])
    check('no_footage_audio_listening_or_release_claim', not report['source_video_or_audio_edited'] and not report['retakes_deleted']
          and report['direct_listening_seconds'] == 0 and not report['release_ready'])
    unknown = fixture(); unknown.pop('chunk_method')
    x, r = normalize(unknown, 'b'*64)
    check('unknown_chunk_origin_keeps_all_cues', x['segments'] == unknown['segments'] and r['duplicate_word_records_collapsed'] == 0)
    conflict = fixture(); conflict['segments'][1]['words'][0]['word'] = ' 되는'; conflict['segments'][1]['text'] = ' 되는 일입니다. 다음 문장입니다.'
    x, r = normalize(conflict, 'b'*64)
    check('negation_conflict_not_deduplicated', x['segments'] == conflict['segments'])
    invalid = fixture(); invalid['segments'][1]['words'][0]['end'] = .3
    x, r = normalize(invalid, 'b'*64)
    check('zero_duration_word_preserved_for_review', x['segments'] == invalid['segments'])
    invalid = fixture(); invalid['segments'][1]['text'] += ' 다른'
    x, r = normalize(invalid, 'b'*64)
    check('word_cue_text_disagreement_preserved', x['segments'] == invalid['segments'])
    complete = fixture(); complete['segments'][1] = cue(complete['segments'][1]['words'][:2])
    x, r = normalize(complete, 'b'*64)
    check('wholly_duplicate_cue_has_receipt_and_is_removed', len(x['segments']) == 2 and r['decisions'][0]['original_later_cue'] == complete['segments'][1])
    same_cue = fixture(); same_cue['segments'] = [cue([word('아니요', 0, .5), word('아니요', .5, 1)])]
    x, r = normalize(same_cue, 'b'*64)
    check('same_cue_intentional_repetition_preserved', x['segments'] == same_cue['segments'])
    check('internal_punctuation_and_numbers_are_not_erased', text_key('3.1배') != text_key('31배') and text_key('안/되는') != text_key('안되는'))
    check('single_word_alias_not_enough_evidence', timed_groups([word('아닙니다', 0, 1)], [word('아닙니다', 0, 1)]) is None)
    check('different_end_times_preserved', timed_groups([word('틀리지', 0, .5), word('않습니다', .5, 1)], [word('틀리지', 0, .6), word('않습니다', .6, 1)]) is None)
    check('different_start_times_preserved', timed_groups([word('틀리지', 0, 1), word('않습니다', 1, 2)], [word('틀리지', .56, 1), word('않습니다', 1, 2)]) is None)
    invalid = fixture(); invalid['source_sha256'] = 42; reject('invalid_source_identity_rejected', invalid)
    invalid = fixture(); invalid['segments'][0]['start'] = float('nan'); reject('nonfinite_time_rejected', invalid)
    invalid = fixture(); invalid['segments'].reverse(); reject('reverse_cue_order_rejected', invalid)
    reject('repeat_normalization_rejected', result)
    empty = {'start': 0., 'end': 0., 'text': ' \t', 'words': [], 'tokens': []}
    source = fixture(); source['segments'].insert(0, empty); before = copy.deepcopy(source)
    x, r = normalize(source, 'b'*64)
    check('empty_metadata_removed_without_input_mutation', source == before and r['empty_metadata_cues_removed'] == 1)
    check('empty_metadata_receipt_retains_original', r['decisions'][0]['original_cue'] == empty)
    check('original_cue_indices_survive_empty_cleanup', [s['input_segment'] for s in r['output_segment_origins']] == [1, 2, 3])
    check('empty_cleanup_does_not_affect_overlap_decision', r['duplicate_word_records_collapsed'] == 2 and x['segments'] == result['segments'])
    for name, change in [
        ('nonempty_zero_duration_cue_rejected', {'text': '아니요'}),
        ('blank_cue_with_words_rejected', {'words': [word('아니요', 0, .5)]}),
        ('blank_cue_with_decoder_tokens_rejected', {'tokens': [1]}),
        ('positive_duration_blank_cue_rejected', {'end': .5}),
        ('negative_blank_cue_rejected', {'start': -1., 'end': -1.}),
        ('nonfinite_blank_cue_rejected', {'start': float('nan')}),
    ]:
        invalid = fixture(); invalid['segments'].insert(0, {**empty, **change}); reject(name, invalid)
    invalid = fixture(); invalid['segments'] = [empty]; reject('all_empty_transcript_rejected', invalid)
    continuous = fixture(); continuous['chunk_method'] = 'Single uninterrupted source inference; native internal Whisper windows; external chunking disabled.'
    x, r = normalize(continuous, 'b'*64)
    check('continuous_transcript_never_assumed_external_overlap', not r['known_chunk_context_provenance'] and x['segments'] == continuous['segments'])
    doc = {'pass': True, 'count': len(checks), 'checks': checks, 'release_ready': False}
    (out/'validation.json').write_text(json.dumps(doc, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(doc, ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--output-dir', required=True)
    run(ap.parse_args().output_dir)
