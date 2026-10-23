#!/usr/bin/env python3
"""Measure quiet intervals and suggest inward-quantized, word-protected cuts.

Suggestions are never edit decisions. Original media/ASR are read-only. The
source hash is mandatory; channel maxima prevent phase cancellation from hiding
speech. No downmix, sample-rate conversion, automatic deletion, or ASR approval.
"""
from pathlib import Path
from fractions import Fraction
import argparse
import hashlib
import json
import math
import subprocess

import av
import numpy as np


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def read_bound_asr(path):
    data = Path(path).read_bytes()
    return json.loads(data.decode('utf-8')), hashlib.sha256(data).hexdigest()


def verify_inputs_unchanged(raw, raw_sha, asr_path, asr_sha):
    if digest(raw) != raw_sha:
        raise ValueError('Raw bytes changed during analysis; discard candidates')
    if digest(asr_path) != asr_sha:
        raise ValueError('ASR bytes changed during analysis; discard candidates')


def merge(intervals):
    result = []
    for a, b in sorted(intervals):
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def protected_speech(asr, duration, guard):
    """Union all timed words, including overlapping ASR ownership windows.

    Invalid/absent word timing falls back to the whole cue. Out-of-cue valid
    words remain protected; the original transcript is never repaired here.
    """
    if not number(duration) or duration <= 0 or not number(guard) or guard < 0:
        raise ValueError('Positive duration and nonnegative speech guard required')
    segments = asr.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('Nonempty ASR segments required')
    intervals, fallback = [], []
    for i, segment in enumerate(segments):
        a, b = segment.get('start'), segment.get('end')
        if not (number(a) and number(b) and 0 <= a < b <= duration):
            raise ValueError(f'Invalid ASR cue {i}; do not guess protection')
        words = segment.get('words')
        valid_words = [w for w in words if isinstance(w, dict)
                       and number(w.get('start')) and number(w.get('end'))
                       and 0 <= w['start'] < w['end'] <= duration] if isinstance(words, list) else []
        spans = [(w['start'], w['end']) for w in valid_words]
        if not valid_words or len(valid_words) != len(words):
            # A bad word must not erase protection of other valid words which
            # can extend beyond their owning cue's envelope.
            spans.append((a, b))
            fallback.append(i)
        intervals.extend((max(0., x - guard), min(duration, y + guard)) for x, y in spans)
    return merge(intervals), fallback


def channel_levels(samples):
    if samples.ndim != 2 or not len(samples) or not np.isfinite(samples).all():
        raise ValueError('Finite nonempty samples × channels PCM required')
    x = samples.astype(np.float64)
    # Analyze every channel, never the possibly cancelling channel mean.
    rms = float(np.sqrt(np.mean(x * x, axis=0)).max())
    peak = float(np.abs(x).max())
    db = lambda value: 20 * math.log10(max(value, 1e-15))
    return db(rms), db(peak)


def contiguous_audio_clock(raw, sample_rate):
    """Reject clock gaps before treating decoded sample offsets as source time."""
    samples, frames = 0, 0
    with av.open(str(raw)) as container:
        for frame in container.decode(audio=0):
            if frame.pts is None or frame.sample_rate != sample_rate:
                raise ValueError('Missing or changing native audio clock')
            source_sample = Fraction(frame.pts) * frame.time_base * sample_rate
            if abs(source_sample - samples) > Fraction(1, 1000000):
                raise ValueError('Discontinuous audio PTS; source-aligned quiet cuts unsupported')
            samples += frame.samples
            frames += 1
    if not frames:
        raise ValueError('No native audio clock frames')
    return {'decoded_frames': frames, 'samples_per_channel': samples,
            'all_frame_PTS_contiguous_from_zero': True}


def measure(raw, ffmpeg, sample_rate, channels, rms_db, peak_db):
    if sample_rate % 50:
        raise ValueError('Native sample rate must support exact 20ms windows')
    count = sample_rate // 50
    byte_count = count * channels * 4
    command = [str(ffmpeg), '-v', 'error', '-nostdin', '-threads', '1', '-i', str(raw),
               '-map', '0:a:0', '-vn', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1']
    # A temporary stderr file avoids a blocked diagnostic pipe during decoding.
    import tempfile
    runs, start, cursor, windows, partial = [], None, 0, [], 0
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            while True:
                block = process.stdout.read(byte_count)
                if not block:
                    break
                if len(block) % (4 * channels):
                    raise ValueError('Incomplete interleaved PCM sample')
                samples = np.frombuffer(block, dtype='<f4').reshape(-1, channels)
                rms, peak = channel_levels(samples)
                quiet = len(samples) == count and rms <= rms_db and peak <= peak_db
                windows.append((rms, peak, quiet))
                if quiet and start is None:
                    start = cursor
                if not quiet and start is not None:
                    runs.append([start / sample_rate, cursor / sample_rate])
                    start = None
                if len(samples) != count:
                    partial += 1
                cursor += len(samples)
            code = process.wait()
            errors.seek(0)
            stderr = errors.read().decode('utf-8', errors='replace')
            if code:
                raise ValueError('FFmpeg failed: ' + stderr[-2000:])
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
    if not cursor:
        raise ValueError('No audio samples decoded')
    if start is not None:
        runs.append([start / sample_rate, cursor / sample_rate])
    return runs, {'command': command, 'decoded_samples_per_channel': cursor,
                  'decoded_duration': cursor / sample_rate, 'windows': len(windows),
                  'partial_tail_windows_not_quiet': partial,
                  'quiet_windows': sum(x[2] for x in windows),
                  'rms_dbfs_percentiles': np.percentile([x[0] for x in windows], [1, 10, 50, 90]).tolist(),
                  'stderr': stderr}


def subtract(ranges, protected):
    result = []
    for a, b in ranges:
        cursor = a
        for x, y in protected:
            if y <= cursor:
                continue
            if x >= b:
                break
            if x > cursor:
                result.append([cursor, min(x, b)])
            cursor = max(cursor, y)
            if cursor >= b:
                break
        if cursor < b:
            result.append([cursor, b])
    return result


def suggest(runs, protected, fps, keep_edge=.15, minimum_quiet=.65, minimum_cut=.25):
    if fps <= 0 or not all(number(v) and v >= 0 for v in [keep_edge, minimum_quiet, minimum_cut]):
        raise ValueError('Invalid timing policy')
    if minimum_cut <= 0:
        raise ValueError('Minimum removed duration must be positive')
    candidates = []
    for a, b in subtract(runs, protected):
        if b - a < minimum_quiet:
            continue
        # Frame boundaries are quantized INWARDS, so no rounding cuts protected
        # words or the explicit retained breathing margin.
        first = math.ceil((a + keep_edge) * float(fps))
        end = math.floor((b - keep_edge) * float(fps))
        if (end - first) / float(fps) < minimum_cut:
            continue
        candidates.append({'quiet_range': [a, b], 'remove_source_frames': [first, end],
                           'remove_source_range': [first / float(fps), end / float(fps)],
                           'retained_quiet_left_seconds': first / float(fps) - a,
                           'retained_quiet_right_seconds': b - end / float(fps),
                           'decision': None, 'direct_listening_seconds': 0})
    return candidates


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--raw', type=Path, required=True)
    ap.add_argument('--asr-json', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--ffmpeg', required=True)
    ap.add_argument('--rms-dbfs', type=float, default=-42.)
    ap.add_argument('--peak-dbfs', type=float, default=-30.)
    args = ap.parse_args()
    if args.output.exists():
        raise ValueError('Choose a new candidate output; preserve previous evidence')
    if not all(math.isfinite(x) and -150 <= x <= 0 for x in (args.rms_dbfs, args.peak_dbfs)):
        raise ValueError('Finite thresholds between -150 and 0 dBFS required')
    raw = args.raw.resolve(strict=True)
    asr, asr_sha = read_bound_asr(args.asr_json)
    raw_sha = digest(raw)
    if not isinstance(asr.get('source_sha256'), str) or asr['source_sha256'].lower() != raw_sha:
        raise ValueError('ASR source SHA-256 must match the actual raw bytes')
    with av.open(str(raw)) as container:
        video, audio = container.streams.video[0], container.streams.audio[0]
        fps = Fraction(video.average_rate)
        rate, channels = audio.rate, audio.codec_context.channels
        if audio.start_time is None or abs(float(audio.start_time * audio.time_base)) > 1 / rate:
            raise ValueError('Nonzero/unknown audio PTS origin unsupported; do not guess source times')
        if video.start_time is None or video.start_time != 0:
            raise ValueError('Nonzero/unknown video PTS origin unsupported')
        duration = container.duration / av.time_base
    protected, fallback = protected_speech(asr, duration, .10)
    audio_clock = contiguous_audio_clock(raw, rate)
    runs, metrics = measure(raw, args.ffmpeg, rate, channels, args.rms_dbfs, args.peak_dbfs)
    if metrics['decoded_samples_per_channel'] != audio_clock['samples_per_channel']:
        raise ValueError('PCM decode differs from source-clock sample count')
    if abs(metrics['decoded_duration'] - duration) > max(1 / float(fps), 1 / rate):
        raise ValueError('Audio/container duration mismatch; no source-aligned suggestions')
    candidates = suggest(runs, protected, fps)
    verify_inputs_unchanged(raw, raw_sha, args.asr_json, asr_sha)
    result = {'schema': 'atrass-quiet-cut-candidates-v1', 'status': 'measured_candidates_not_applied',
              'release_ready': False, 'raw_sha256': raw_sha, 'raw_path': str(raw),
              'asr_sha256': asr_sha, 'asr_path': str(args.asr_json.resolve()),
              'input_bytes_reverified_after_measurement': True,
              'script_sha256': digest(Path(__file__)), 'native_video_fps': str(fps),
              'sample_rate': rate, 'channels': channels, 'channel_policy': 'max_per_channel_no_downmix',
              'native_audio_clock': audio_clock,
              'measurement': metrics, 'rms_threshold_dbfs': args.rms_dbfs, 'peak_threshold_dbfs': args.peak_dbfs,
              'speech_guard_seconds': .10, 'retained_quiet_per_edge_seconds': .15,
              'minimum_quiet_seconds': .65, 'minimum_cut_seconds': .25,
              'word_timing_fallback_whole_cues': fallback, 'protected_speech_union': protected,
              'measured_quiet_runs': runs, 'candidates': candidates,
              'candidate_removed_seconds': sum(x['remove_source_range'][1] - x['remove_source_range'][0] for x in candidates),
              'direct_listening_seconds': 0, 'source_modified': False, 'edit_plan_modified': False,
              'limits': ['Thresholds and margins are an explicit conservative candidate policy, not original Atrass settings.',
                         'Missing ASR words may hide untranscribed low-volume speech or performance pauses. Inspect before applying.',
                         'Source frames use nominal FPS; VFR visual cut boundaries require explicit PTS mapping.',
                         'This analysis neither approves phoneme boundaries nor replaces listening or semantic review.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'output': str(args.output), 'candidates': len(candidates),
                      'candidate_removed_seconds': result['candidate_removed_seconds'], 'applied_cuts': 0}))


if __name__ == '__main__':
    main()
