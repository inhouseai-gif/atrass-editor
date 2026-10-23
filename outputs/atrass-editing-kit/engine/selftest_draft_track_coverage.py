#!/usr/bin/env python3
"""Exercise real mismatched-track recordings and repeated last-frame reads."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import av
import numpy as np
from render import SourceReader
from run_longform import read, save, sha


def run(output, ffmpeg):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    checks, movies = [], []
    engine = Path(__file__).resolve().parent
    def check(name, value):
        assert value, name
        checks.append(name)
    def reject(name, action):
        try:
            action()
        except ValueError:
            checks.append(name)
        else:
            raise AssertionError(name)
    for label, audio_duration, expected_frames in [('audio-longer', 2.08, 119), ('video-longer', 1.9, 113)]:
        raw = output / (label + '.mov')
        subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=12:duration=2',
                        '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={audio_duration}',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'pcm_s16le', str(raw)], check=True)
        raw_hash = sha(raw)
        with av.open(str(raw)) as c:
            frames = list(c.decode(video=0))
            last_pixels = frames[-1].to_ndarray(format='rgb24')
        reader = SourceReader(raw)
        try:
            for time in [1.92, 1.94, 1.96, 1.98, 1.999]:
                arr, pts = reader.get(time)
                check(label + '_last_frame_' + str(time), np.array_equal(arr, last_pixels) and abs(pts - 23/12) < 1e-9)
            reject(label + '_no_hold_past_video_end', lambda: reader.get(2.001))
            reject(label + '_nonfinite_time', lambda: reader.get(float('nan')))
            arr, pts = reader.get(0.)
            check(label + '_reverse_seek_after_eof', pts == 0 and np.array_equal(arr, frames[0].to_ndarray(format='rgb24')))
        finally:
            reader.close()
        asr = output / (label + '-asr.json')
        save(asr, {'source_sha256': raw_hash, 'segments': [{'start': .2, 'end': .7, 'text': '원본 대사는 보존합니다.'}]})
        job = output / (label + '-job')
        command = [sys.executable, str(engine/'start_from_raw.py'), 'start', '--raw', str(raw), '--asr-json', str(asr),
                   '--draft', '--output-dir', str(job), '--ffmpeg', ffmpeg]
        with (output/(label+'.stdout.txt')).open('x') as out, (output/(label+'.stderr.txt')).open('x') as err:
            subprocess.run(command, stdout=out, stderr=err, check=True)
        result = read(job/'result.json')
        report = read(result['report'])
        movie = Path(result['movie'])
        with av.open(str(movie)) as c:
            count = sum(1 for _ in c.decode(video=0))
        check(label + '_whole_movie_closed_and_decoded', count == expected_frames == result['review']['frames'])
        check(label + '_all_ASR_retained', report['all_ASR_text_retained'] and report['caption_count'] == 1)
        check(label + '_raw_unchanged', sha(raw) == raw_hash)
        check(label + '_no_semantic_completion_claim', result['release_ready'] is False)
        movies.append({'case': label, 'frames': count, 'sha256': sha(movie), 'coverage': report['media_coverage']})
        if label == 'audio-longer':
            tail_asr = output/'speech-outside-video.json'
            save(tail_asr, {'source_sha256': raw_hash, 'segments': [{'start': 1.99, 'end': 2.06, 'text': '끝 대사도 보존해야 합니다.'}]})
            command[command.index(str(asr))] = str(tail_asr)
            command[command.index(str(job))] = str(output/'tail-speech-job')
            command.append('--prepare-only')
            rejected = subprocess.run(command, capture_output=True, text=True)
            save(output/'speech-rejection.json', {'returncode': rejected.returncode, 'stderr': rejected.stderr})
            check('speech_outside_common_video_audio_coverage_rejected_not_dropped',
                  rejected.returncode != 0 and 'Invalid cue timing' in rejected.stderr)
    result = {'pass': True, 'count': len(checks), 'checks': checks, 'movies': movies,
              'direct_listening_seconds': 0, 'release_ready': False}
    save(output/'validation.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--ffmpeg', required=True)
    args = parser.parse_args()
    run(args.output_dir, args.ffmpeg)
