#!/usr/bin/env python3
"""Decode a complete rendered movie and prepare source-bound visual review.

Collection and signal measurements never constitute perceptual approval.
"""
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
import argparse
import hashlib
import html
import json
import math
import subprocess


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def require(test, message):
    if not test:
        raise ValueError(message)


def save(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def parse_loudness(log):
    start = log.rfind('{')
    require(start >= 0, 'FFmpeg did not return loudness measurements')
    # FFmpeg may append its muxer summary after the JSON object.
    value, _ = json.JSONDecoder().raw_decode(log[start:])
    require(all(k in value for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh')),
            'Incomplete FFmpeg loudness measurements')
    return value


def schedule(project):
    fps = Fraction(project['fps'])
    count = round(project['duration'] * fps)
    require(fps > 0 and count > 0, 'Positive duration/FPS required')
    selected = defaultdict(list)

    def add(n, why):
        if 0 <= n < count:
            selected[n].append(why)

    for i in range(math.ceil(Fraction(count, 1) / fps * 4)):
        add(round(Fraction(i, 4) * fps), {'kind': '4fps', 'tick': i})
    for layer in project['layers']:
        first, end = round(layer['in'] * fps), round(layer['out'] * fps)
        require(0 <= first < end <= count, 'Layer outside output: ' + layer['id'])
        for n, phase in [(first-1, 'before'), (first, 'first'), ((first+end-1)//2, 'mid'), (end-1, 'last'), (end, 'after')]:
            add(n, {'kind': 'layer', 'id': layer['id'], 'phase': phase})
    for clip in project['clips']:
        for delta in [-2, -1, 0, 1, 2]:
            add(round(clip['in'] * fps) + delta, {'kind': 'cut', 'id': clip['id'], 'delta': delta})
    add(count-1, {'kind': 'final'})
    return fps, count, dict(sorted(selected.items()))


def verify_sidecar(project, movie, sidecar):
    p = json.loads(Path(project).read_text(encoding='utf-8'))
    r = json.loads(Path(sidecar).read_text(encoding='utf-8'))
    fps, count, _ = schedule(p)
    require(r.get('project_sha256') == sha(project), 'Render sidecar belongs to another project')
    require(r.get('output_sha256') == sha(movie), 'Movie does not match closed render sidecar')
    require(r.get('range') == [0.0, p['duration']], 'A complete render is required, not a sample range')
    require(r.get('frame_count') == count and Fraction(r['fps']) == fps, 'Render count/FPS differs from project')
    require(r.get('size') == [p['width'], p['height']], 'Render dimensions differ from project')
    require(bool(r.get('audio', {}).get('present')) == bool(p.get('audio')), 'Render audio presence differs from project')
    return p, r


def review(project, movie, sidecar, output, ffmpeg):
    import av
    from PIL import Image, ImageDraw
    project, movie, sidecar, output = map(Path, (project, movie, sidecar, output))
    require(not output.exists(), 'Choose a new review directory')
    p, r = verify_sidecar(project, movie, sidecar)
    original = {'project': sha(project), 'movie': sha(movie), 'sidecar': sha(sidecar)}
    fps, count, chosen = schedule(p)
    output.mkdir(parents=True)
    (output/'sheets').mkdir()
    rows, sheets, page = [], [], []

    def flush():
        if not page:
            return
        canvas = Image.new('RGB', (1920, 392 * math.ceil(len(page)/3)), '#181818')
        draw = ImageDraw.Draw(canvas)
        for i, (n, im) in enumerate(page):
            x, y = i % 3 * 640, i // 3 * 392
            draw.text((x+8, y+8), f'frame {n} / {float(n/fps):.3f}s', fill='white')
            canvas.paste(im, (x, y+32))
        path = output/'sheets'/f'review-{len(sheets):03d}.jpg'
        canvas.save(path, quality=90)
        sheets.append({'path': str(path.relative_to(output)), 'sha256': sha(path), 'frames': [n for n, _ in page]})
        page.clear()

    with av.open(str(movie)) as container:
        require(len(container.streams.video) == 1, 'Exactly one video stream required')
        video = container.streams.video[0]
        require(video.average_rate == fps, 'Encoded video rate mismatch')
        decoded = 0
        for frame in container.decode(video):
            require(frame.pts is not None and Fraction(frame.pts)*frame.time_base == Fraction(decoded, 1)/fps,
                    f'Unexpected video PTS at frame {decoded}')
            require((frame.width, frame.height) == (p['width'], p['height']), 'Unexpected frame dimensions')
            if decoded in chosen:
                rows.append({'frame': decoded, 'pts': frame.pts, 'seconds': float(decoded/fps), 'reasons': chosen[decoded]})
                page.append((decoded, frame.to_image().convert('RGB').resize((640, 360))))
                if len(page) == 12:
                    flush()
            decoded += 1
        require(decoded == count, f'Encoded frame count mismatch: {decoded}/{count}')
    flush()
    require([row['frame'] for row in rows] == list(chosen), 'Capture schedule not fulfilled')
    sound = {'present': False, 'direct_listening_seconds': 0}
    with av.open(str(movie)) as container:
        require(bool(container.streams.audio) == bool(p.get('audio')), 'Encoded audio presence mismatch')
        if container.streams.audio:
            require(len(container.streams.audio) == 1, 'Exactly one mixed audio stream required')
            a = container.streams.audio[0]
            require(a.duration is not None, 'Missing declared audio duration')
            duration = float(a.duration*a.time_base)
            require(abs(duration-p['duration']) <= 1/a.sample_rate+1e-8, 'Audio duration mismatch')
            sound.update(present=True, sample_rate=a.sample_rate, channels=a.channels, declared_duration=duration)
            decoded_samples = 0
            last_end = None
            for frame in container.decode(a):
                require(frame.pts is not None, 'Audio PTS missing')
                start = Fraction(frame.pts)*frame.time_base
                require(last_end is None or start == last_end, 'Decoded audio packet gap/overlap')
                last_end = start + Fraction(frame.samples, frame.sample_rate)
                decoded_samples += frame.samples
            sound['decoded_samples_including_codec_padding'] = decoded_samples
            require(decoded_samples >= round(duration*a.sample_rate), 'Decoded audio is truncated')
    if sound['present']:
        command = [str(ffmpeg), '-hide_banner', '-nostdin', '-i', str(movie), '-vn', '-t', f"{p['duration']:.12f}", '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-']
        with (output/'loudness.stdout.txt').open('x') as out, (output/'loudness.stderr.txt').open('x') as err:
            result = subprocess.run(command, stdout=out, stderr=err, timeout=1800)
        require(result.returncode == 0, 'Loudness measurement failed')
        log = (output/'loudness.stderr.txt').read_text(encoding='utf-8')
        measure = parse_loudness(log)
        # Values remain measurements; no original mix or listening approval.
        sound['loudness'] = {k: measure[k] for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh')}
    require(original == {'project': sha(project), 'movie': sha(movie), 'sidecar': sha(sidecar)}, 'Review inputs changed during decoding')
    manifest = {'schema': 'atrass-movie-review-v1', 'input_sha256': original, 'native_frames_decoded': decoded,
                'samples': rows, 'sheets': sheets, 'audio': sound, 'frame_pts_and_duration_pass': True,
                'actual_viewed_sheets': [], 'full_temporal_review': False, 'style_match_approved': False,
                'direct_listening_seconds': 0, 'release_ready': False}
    save(output/'review.json', manifest)
    save(output/'result.json', {'pass': True, 'scope': 'Whole file decoding, timing/audio presence and measurements; capture only, no perceptual approval',
         'frames': decoded, 'samples': len(rows), 'sheets': len(sheets), 'audio': sound,
         'manifest_sha256': sha(output/'review.json'), 'release_ready': False})
    cards = ''.join(f'<figure><figcaption>{i+1}/{len(sheets)} · frames {row["frames"][0]}–{row["frames"][-1]}</figcaption><img loading="lazy" src="{html.escape(row["path"], quote=True)}"></figure>' for i, row in enumerate(sheets))
    with (output/'review.html').open('x', encoding='utf-8') as stream:
        stream.write('<!doctype html><meta charset="utf-8"><title>아트라상 편집 검수</title><style>body{background:#151515;color:#eee;font:16px sans-serif;margin:24px}img{width:100%}figure{max-width:1600px;margin:24px 0}figcaption{margin-bottom:8px}</style><h1>전체 편집본 검수 화면</h1><p>초당 4장과 모든 레이어·컷 경계를 추출했습니다. 실제 시청·청취 판정은 별도입니다.</p>'+cards)
    return json.loads((output/'result.json').read_text(encoding='utf-8'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'movie', 'sidecar', 'output-dir', 'ffmpeg'):
        ap.add_argument('--'+name, required=True)
    a = ap.parse_args()
    print(json.dumps(review(a.project, a.movie, a.sidecar, a.output_dir, a.ffmpeg), ensure_ascii=False))


if __name__ == '__main__':
    main()
