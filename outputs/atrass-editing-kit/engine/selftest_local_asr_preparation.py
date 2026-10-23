#!/usr/bin/env python3
"""Exercise the ASR adapter with MLX-like numpy timings, without inference."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audio_audit import transcribe_local, RATE
from normalize_asr_overlap import normalize


def run():
    checks = []
    def check(name, condition):
        assert condition, name
        checks.append(name)
    calls = []
    def fake_transcribe(audio, **kwargs):
        calls.append((len(audio), kwargs))
        return {'segments': [
            {'start': np.float64(.25), 'end': np.float64(.75), 'text': ' 아니요',
             'words': [{'start': np.float64(.25), 'end': np.float64(.75), 'word': ' 아니요'}]},
            {'start': np.float64(1), 'end': np.float64(1), 'text': '', 'words': [], 'tokens': []},
        ]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); model = root/'model'; model.mkdir()
        (model/'weights.safetensors').write_bytes(b'mock-only-no-real-weights')
        with patch.dict(sys.modules, {'torch': SimpleNamespace(set_num_threads=lambda n: None),
                                      'mlx_whisper': SimpleNamespace(transcribe=fake_transcribe)}):
            audio = np.zeros((121 * RATE, 2), dtype=np.float32)
            doc = transcribe_local(audio, model, root, 'continuous', method='continuous')
            check('new_source_inference_called_once_with_whole_recording', len(calls) == 1 and calls[0][0] == len(audio))
            check('existing_local_weights_and_word_alignment_requested', calls[0][1]['path_or_hf_repo'] == str(model.resolve()) and calls[0][1]['word_timestamps'])
            check('returned_transcript_equals_saved_json', doc == json.loads((root/'continuous-asr.json').read_text()))
            check('numpy_timestamps_canonicalized_to_json_numbers', type(doc['segments'][0]['start']) is float and type(doc['segments'][0]['words'][0]['end']) is float)
            check('original_empty_cue_preserved_before_derived_cleanup', len(doc['segments']) == 2)
            doc['source_sha256'] = 'a'*64
            cleaned, receipt = normalize(doc, 'b'*64)
            check('strict_normalizer_accepts_canonicalized_adapter_output', len(cleaned['segments']) == 1 and receipt['empty_metadata_cues_removed'] == 1)
            check('spoken_negation_and_timing_unchanged', cleaned['segments'][0] == doc['segments'][0])
            check('continuous_inference_not_treated_as_external_overlap', not receipt['known_chunk_context_provenance'])
            calls.clear()
            legacy = transcribe_local(audio, model, root, 'legacy')
            check('research_default_preserves_two_context_windows', len(calls) == 2 and legacy['chunk_method'].startswith('120s ownership'))
            check('no_listening_or_upload_claim', not legacy['manual_listening'] and not legacy['network_upload'])
    return {'pass': True, 'checks': checks, 'count': len(checks), 'model_inference_mocked': True, 'release_ready': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path)
    args = parser.parse_args(); result = run()
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))
