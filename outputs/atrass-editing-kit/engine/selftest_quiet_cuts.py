#!/usr/bin/env python3
"""Safety regressions for quiet-cut suggestions; no media or edit decisions."""
from fractions import Fraction
import json
import math
import tempfile
from pathlib import Path
import numpy as np
from suggest_quiet_cuts import channel_levels, protected_speech, suggest, subtract, read_bound_asr, verify_inputs_unchanged, digest


def run():
    checks = []
    def check(ok, label):
        assert ok, label
        checks.append(label)
    # Opposite channels have a silent mono mean but substantial real energy.
    x = np.array([[.3, -.3], [-.3, .3]], dtype=np.float32)
    rms, peak = channel_levels(x)
    check(abs(rms - 20 * math.log10(.3)) < 1e-5 and peak > -11, 'stereo cancellation cannot hide speech')
    rms, peak = channel_levels(np.array([[0, .2], [0, -.2]]))
    check(rms > -14 and peak > -14, 'one silent channel cannot hide the other')
    check(channel_levels(np.zeros((8, 2))) == (-300., -300.), 'finite exact-silence measurement')
    for bad in [np.zeros(4), np.zeros((0, 2)), np.array([[float('nan'), 0]])]:
        try:
            channel_levels(bad)
        except ValueError:
            check(True, 'reject invalid PCM ' + str(len(checks)))
        else:
            raise AssertionError('Invalid PCM accepted')
    asr = {'segments': [
        {'start': 1., 'end': 2., 'words': [{'start': .9, 'end': 1.2}]},
        {'start': 1., 'end': 1.4, 'words': [{'start': 1., 'end': 1.3}]},
        {'start': 3., 'end': 3.5, 'words': [{'start': 3., 'end': 3.}]},
        {'start': 5., 'end': 6.}]}
    protected, fallback = protected_speech(asr, 10, .1)
    check(np.allclose(protected, [[.8, 1.4], [2.9, 3.6], [4.9, 6.1]]), 'word union includes out-of-cue times and duplicated windows')
    check(fallback == [2, 3], 'zero-length or missing words protect entire cue')
    mixed = {'segments': [{'start': 3, 'end': 4, 'words': [
        {'start': 1, 'end': 2}, {'start': 3, 'end': 3}]}]}
    spans, fb = protected_speech(mixed, 10, .1)
    check(np.allclose(spans, [[.9, 2.1], [2.9, 4.1]]) and fb == [0], 'invalid word preserves valid out-of-cue word plus cue')
    with tempfile.TemporaryDirectory() as directory:
        raw, transcript = Path(directory) / 'source.bin', Path(directory) / 'asr.json'
        raw.write_bytes(b'synthetic source identity only')
        transcript.write_text(json.dumps(mixed))
        loaded, asr_sha = read_bound_asr(transcript)
        raw_sha = digest(raw)
        verify_inputs_unchanged(raw, raw_sha, transcript, asr_sha)
        check(loaded == mixed, 'ASR parsed bytes and original hash bound together')
        transcript.write_text(json.dumps(asr))
        try:
            verify_inputs_unchanged(raw, raw_sha, transcript, asr_sha)
        except ValueError:
            check(True, 'refuse ASR mutation during measurement')
        else:
            raise AssertionError('Changed ASR accepted')
        transcript.write_text(json.dumps(mixed))
        raw.write_bytes(b'changed raw identity')
        try:
            verify_inputs_unchanged(raw, raw_sha, transcript, asr_sha)
        except ValueError:
            check(True, 'refuse raw mutation during measurement')
        else:
            raise AssertionError('Changed raw accepted')
    cuts = suggest([[0, 10]], protected, Fraction(60))
    check(len(cuts) == 4, 'quiet is split around every protected speech interval')
    for cut in cuts:
        a, b = cut['remove_source_range']
        check(all(b <= x or a >= y for x, y in protected), 'cut does not touch protected speech')
        check(cut['retained_quiet_left_seconds'] >= .15 - 1e-12 and cut['retained_quiet_right_seconds'] >= .15 - 1e-12, 'retain both breathing margins')
        check(cut['decision'] is None, 'never auto-approve deletion')
    check(not suggest([[0, .64]], [], Fraction(60)), 'short pause preserved')
    check(not suggest([[0, .8]], [[0, .8]], Fraction(60)), 'low-volume ASR speech preserved even in acoustic silence')
    c = suggest([[.013, 1.017]], [], Fraction(60000, 1001))[0]
    check(c['remove_source_range'][0] >= .163 and c['remove_source_range'][1] <= .867, 'fractional FPS quantizes inward')
    check(subtract([[0, 2]], [[0, 2]]) == [], 'whole protected quiet span excluded')
    check(subtract([[0, 2]], [[.5, .8], [.8, 1.5]]) == [[0, .5], [1.5, 2]], 'adjacent speech protections do not create phantom gaps')
    for bad in [{'segments': []}, {'segments': [{'start': 0, 'end': 11}]}, {'segments': [{'start': float('nan'), 'end': 2}]}]:
        try:
            protected_speech(bad, 10, .1)
        except ValueError:
            check(True, 'reject unsafe ASR ' + str(len(checks)))
        else:
            raise AssertionError('Invalid ASR accepted')
    return {'passed': True, 'conditions': len(checks), 'checks': checks, 'direct_listening_seconds': 0}


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
