#!/usr/bin/env python3
"""Agent-driven production for a NEW recording: prepare -> decide -> build -> render.

The hosting Codex/Claude reads the transcript and authors source-bound decisions.
No paid API, fixed episode replay, reference video passthrough, or claimed
autonomous semantic inference is hidden in this entry point.
"""
from pathlib import Path
from fractions import Fraction
import argparse
import copy
import json
import importlib.util
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata

import av
from PIL import Image, ImageDraw
from fontTools.ttLib import TTFont
from render import Composer, render, render_audio, validate, resolve
from review_movie import require, sha, save, parse_loudness
from run_longform import binding, verify_bindings, job_lock
from draft_from_transcript import caption_groups, plan_from_evidence
from compile_plan import compile_plan

KIT = Path(__file__).resolve().parents[1]
SCHEMA = 'atrass-agent-production-v1'
FPS = Fraction(30)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= value.keys() <= set(required) | set(optional),
            'Unexpected or missing fields; required: '+', '.join(required))


def ffmpeg_path(value=None):
    found = str(value) if value else shutil.which('ffmpeg')
    if not found:
        import imageio_ffmpeg
        found = imageio_ffmpeg.get_ffmpeg_exe()
    return str(Path(found).resolve(strict=True))


def probe(raw):
    with av.open(str(raw)) as c:
        require(c.streams.video and c.streams.audio, 'Original video and audio are required')
        v, a = c.streams.video[0], c.streams.audio[0]
        require(v.start_time == 0 and a.start_time == 0 and v.duration and a.duration,
                'Nonzero stream starts or unknown duration require explicit timing repair')
        require(not v.metadata.get('rotate') or v.metadata['rotate'] == '0', 'Apply a reviewed rotation before this input')
        return {'width': v.width, 'height': v.height, 'fps': str(v.average_rate),
                'duration': min(float(v.duration*v.time_base), float(a.duration*a.time_base))}


def speech_units(transcript, duration):
    """Preserve every word; merge zero-length words without inventing timestamps."""
    result, repairs = [], []
    compact = lambda t: re.sub(r'\s', '', unicodedata.normalize('NFC', t))
    for i, cue in enumerate(transcript['segments']):
        a, b, text = cue['start'], cue['end'], cue['text'].strip()
        if not text and a == b:
            repairs.append({'cue': i, 'method': 'empty_metadata_ignored'})
            continue
        require(all(finite(x) for x in (a, b)) and 0 <= a < b <= duration+.001 and text,
                'Invalid speech cue: '+str(i))
        words = cue.get('words') or []
        valid = (bool(words) and compact(''.join(w['word'] for w in words)) == compact(text)
                 and all(finite(w.get('start')) and finite(w.get('end'))
                         and max(0, a-1) <= w['start'] <= w['end'] <= min(duration+.001, b+1)
                         and w['word'].strip() for w in words)
                 and all(x['end'] <= y['start']+.001 for x, y in zip(words, words[1:])))
        local = []
        if valid:
            # Whisper segment timestamps are coarse and may start after the
            # first aligned word. Keep observed word times, never replace them
            # with the later segment start and truncate a syllable. Larger
            # (>1s) disagreements still fall back to an indivisible cue.
            if words[0]['start'] < a-.001 or words[-1]['end'] > b+.001:
                repairs.append({'cue': i, 'method': 'preserve_observed_word_envelope',
                                'segment_range': [a, b], 'word_range': [words[0]['start'], words[-1]['end']]})
            for j, w in enumerate(words):
                u = {'start': w['start'], 'end': w['end'], 'text': w['word'].strip(),
                     'refs': [[i, j]], 'cue_indices': [i]}
                if local and (u['end'] == u['start'] or local[-1]['end'] == local[-1]['start']):
                    last = local[-1]
                    last['text'] += ' '+u['text']; last['end'] = u['end']; last['refs'] += u['refs']
                    repairs.append({'ref': [i, j], 'method': 'merge_zero_duration_adjacent_word'})
                else:
                    local.append(u)
        if not local or any(u['start'] >= u['end'] for u in local):
            local = [{'start': a, 'end': b, 'text': text, 'refs': [[i, None]], 'cue_indices': [i]}]
            repairs.append({'cue': i, 'method': 'indivisible_cue_without_trusted_word_alignment'})
        result += local
    merged = []
    for u in sorted(result, key=lambda u: u['start']):
        if merged and u['start'] < merged[-1]['end']-.001:
            v = merged[-1]; v['text'] += ' '+u['text']; v['end'] = max(v['end'], u['end'])
            v['refs'] += u['refs']; v['cue_indices'] = sorted(set(v['cue_indices']+u['cue_indices']))
            repairs.append({'refs': u['refs'], 'method': 'merge_overlapping_ownership_no_text_deleted'})
        else:
            merged.append(copy.deepcopy(u))
    require(merged, 'No usable speech')
    for i, u in enumerate(merged):
        u['id'] = i
    return merged, repairs


def prepare(raw, kind, output, asr=None, model=None, backend='auto', ffmpeg=None, count=1):
    raw, output = Path(raw).resolve(strict=True), Path(output).resolve()
    require(not output.exists(), 'Choose a new job directory; prior jobs are preserved')
    require(type(count) is int and 1 <= count <= 10, 'Requested count must be 1..10')
    require(kind == 'shorts' or count == 1, 'A longform job produces one episode')
    identity, meta = binding(raw), probe(raw)
    output.mkdir(parents=True)
    if asr:
        from prepare_job import verify_asr_source
        transcript = read(asr)
        require(transcript.get('source_sha256') == identity['sha256'], 'Imported transcript must be SHA-bound to this recording')
        verify_asr_source(transcript, raw, identity['sha256'])
        asr_mode = 'imported_source_bound_transcript'
        model_name = transcript.get('model', 'external_source_bound_ASR')
    else:
        # Both supported backends accept decoded PCM; no system FFmpeg install
        # or older PyAV file adapter is required. Only model weights download.
        import numpy as np
        audio = output/'analysis-audio-16k.f32'
        subprocess.run([ffmpeg_path(ffmpeg), '-v', 'error', '-nostdin', '-i', str(raw), '-vn',
                        '-ac', '1', '-ar', '16000', '-f', 'f32le', str(audio)], check=True)
        samples = np.fromfile(audio, dtype=np.float32)
        models = sorted((Path.home()/'.cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo/snapshots').glob('*/config.json'))
        if backend == 'auto':
            backend = 'mlx' if sys.platform == 'darwin' and importlib.util.find_spec('mlx_whisper') else 'cpu'
        if backend == 'mlx':
            import mlx_whisper
            model_name = str(model or (models[-1].parent if models else 'mlx-community/whisper-large-v3-turbo'))
            result = mlx_whisper.transcribe(samples, path_or_hf_repo=model_name, language='ko',
                word_timestamps=True, condition_on_previous_text=False, temperature=0., verbose=False)
            transcript = {'segments': [{'start': float(s['start']), 'end': float(s['end']), 'text': s['text'],
                'words': [{'start': float(w['start']), 'end': float(w['end']), 'word': w['word']} for w in s.get('words', [])]} for s in result['segments']]}
        else:
            from faster_whisper import WhisperModel
            model_name = str(model or 'large-v3-turbo')
            recognizer = WhisperModel(model_name, device='cpu', compute_type='int8')
            segments, info = recognizer.transcribe(samples, language='ko', word_timestamps=True, vad_filter=False)
            # CTranslate2 can return NumPy scalars. Convert only at this backend
            # boundary; the source-bound JSON validator remains deliberately strict.
            transcript = {'segments': [{'start': float(s.start), 'end': float(s.end), 'text': s.text,
                'words': [{'start': float(w.start), 'end': float(w.end), 'word': w.word} for w in (s.words or [])]} for s in segments]}
        transcript.update(input=str(raw), source_sha256=identity['sha256'])
        asr_mode = 'local_'+backend
    require(sha(raw) == identity['sha256'], 'Raw changed during transcription')
    save(output/'transcript.json', transcript)
    # Retain the original ASR result even if its timing fails validation. Do not
    # silently clamp a recognizer's speech to the input duration.
    units, repairs = speech_units(transcript, meta['duration'])
    save(output/'units.json', {'source_sha256': identity['sha256'], 'units': units, 'repairs': repairs})
    job = {'schema': SCHEMA, 'kind': kind, 'count': count, 'raw': identity, 'metadata': meta,
           'transcript_sha256': sha(output/'transcript.json'), 'units_sha256': sha(output/'units.json'),
           'asr_mode': asr_mode, 'asr_model': model_name, 'semantic_decision_author': 'hosting_Codex_or_Claude', 'release_ready': False}
    save(output/'job.json', job)
    with (output/'transcript-for-agent.txt').open('x', encoding='utf-8') as f:
        f.write('Read the entire transcript. Unit IDs are inclusive in keep ranges. Never reuse another episode IDs.\n')
        for u in units:
            f.write(f"{u['id']:04} | {u['start']:.3f}-{u['end']:.3f} | {u['text']}\n")
    return {'job': str(output/'job.json'), 'units': len(units), 'next': 'Read PRODUCTION.md, author decisions.json from the entire transcript, then build and render. Do not stop here or ask the user for JSON.'}


def select_units(units, ranges):
    require(isinstance(ranges, list) and ranges, 'Nonempty inclusive unit ranges required')
    ids = []
    for pair in ranges:
        require(isinstance(pair, list) and len(pair) == 2 and all(type(v) is int for v in pair)
                and 0 <= pair[0] <= pair[1] < len(units), 'Invalid unit range')
        ids.extend(range(pair[0], pair[1]+1))
    require(ids == sorted(set(ids)), 'Keep ranges must be chronological and disjoint; no duplicate speech')
    return [copy.deepcopy(units[i]) for i in ids]


def take_spans(units, selected, duration):
    chosen = {u['id'] for u in selected}
    spans = []
    for u in selected:
        i = u['id']; a = max(0, u['start']-.12); b = min(duration, u['end']+.14)
        if i and i-1 not in chosen:
            a = max(a, (units[i-1]['end']+u['start'])/2)
        if i+1 < len(units) and i+1 not in chosen:
            b = min(b, (units[i+1]['start']+u['end'])/2)
        lo, hi = math.floor(a*30+1e-8), min(math.floor(duration*30), math.ceil(b*30-1e-8))
        require(lo/30 <= u['start']+.001 and hi/30 >= u['end']-.001, 'Speech escapes available source frames')
        # Join only adjacent SELECTED units, never bridge across removed words.
        if spans and i == spans[-1][3]+1 and lo-spans[-1][1] <= 16:
            spans[-1][1] = max(spans[-1][1], hi); spans[-1][3] = i
        else:
            if spans:
                require(lo >= spans[-1][1], 'Selection boundary overlap; retain or review the adjacent utterance')
            spans.append([lo, hi, i, i])
    return [s[:2] for s in spans]


def asset(name):
    require(isinstance(name, str) and re.fullmatch('illustr-[a-z0-9-]+', name), 'Use a packaged illustration ID')
    p = (KIT/'assets/generated'/name/'v1.png').resolve(strict=True)
    require(p.is_relative_to((KIT/'assets/generated').resolve()), 'Asset path escaped library')
    provenance = p.parent/'provenance.json'
    require(provenance.is_file(), 'Illustration provenance missing')
    return p, provenance


def map_range(maps, pair):
    require(isinstance(pair, list) and len(pair) == 2 and all(finite(x) for x in pair) and 0 <= pair[0] < pair[1], 'Invalid source event range')
    result = []
    for m in maps:
        a, b = m['source_frames']; lo = max(a, math.ceil(pair[0]*30-1e-8)); hi = min(b, math.ceil(pair[1]*30-1e-8))
        if lo < hi:
            o = m['output_frames'][0]-a
            result.append(((lo+o)/30, (hi+o)/30, m['take_id']))
    require(result, 'Event maps to no retained source speech')
    return result


def balanced_groups(units, fits):
    """Repair a short automatic tail inside one agent-authored caption partition."""
    result = []
    for group in caption_groups(units, FPS, fits):
        previous = result[-1] if result else None
        text = previous['text']+' '+group['text'] if previous else ''
        if (previous and (group['end']-group['start'] < .65 or len(group['text']) <= 7)
                and group['start']-previous['end'] <= .35 and group['end']-previous['start'] <= 4.8
                and len(text) <= 36 and fits(text) and not re.search(r'[.!?。！？]$', previous['text'])):
            previous.update(end=group['end'], text=text, refs=previous['refs']+group['refs'],
                            cue_indices=sorted(set(previous['cue_indices']+group['cue_indices'])))
        else:
            result.append(copy.deepcopy(group))
    return result


def apply_framing(project, events, maps, meta):
    """Split only the image track for explicit centered source-bound punch-ins."""
    require(isinstance(events, list), 'Framing must be an event list')
    windows = []
    for event in events:
        fields(event, ['source_range', 'zoom', 'reason'])
        require(finite(event['zoom']) and 1 <= event['zoom'] <= 1.3 and
                isinstance(event['reason'], str) and event['reason'].strip(), 'Invalid framing zoom or rationale')
        for lo, hi, tid in map_range(maps, event['source_range']):
            windows.append((round(lo*30), round(hi*30), event))
    windows.sort(key=lambda x: x[:2])
    require(all(a[1] <= b[0] for a, b in zip(windows, windows[1:])), 'Framing events overlap')
    clips = []
    for original in project['clips']:
        start, end = round(original['in']*30), round(original['out']*30)
        own = [(max(start, lo), min(end, hi), e) for lo, hi, e in windows if lo < end and start < hi]
        bounds = sorted({start, end} | {n for lo, hi, e in own for n in (lo, hi)})
        for lo, hi in zip(bounds, bounds[1:]):
            c = copy.deepcopy(original)
            c.update(id=original['id']+'-frame-'+str(lo), **{'in': lo/30, 'out': hi/30})
            c['source_in'] = original['source_in']+(lo-start)/30*original.get('rate', 1)
            active = next((e for a, b, e in own if a <= lo and hi <= b), None)
            if active:
                scale = max(project['width']/meta['width'], project['height']/meta['height'])*active['zoom']
                c['geometry'] = {'scale': scale, 'tx': (project['width']-meta['width']*scale)/2,
                                 'ty': (project['height']-meta['height']*scale)/2}
                c['framing_evidence'] = copy.deepcopy(active)
            clips.append(c)
    project['clips'] = clips


def longform(plan, clip, maps, output):
    for i, event in enumerate(clip['events']):
        require(event.get('kind') in ('emphasis', 'chapter', 'illustration', 'chat'), 'Unsupported event kind')
        if event['kind'] == 'emphasis':
            fields(event, ['kind', 'source_range', 'text', 'reason'])
            for j, (lo, hi, _) in enumerate(map_range(maps, event['source_range'])):
                plan['events'].append({'id': f'emphasis-{i}-{j}', 'template': 'large_emphasis', 'in': lo, 'out': hi, 'text': event['text']})
    save(output/'edit-plan.json', plan)
    compile_plan(output/'edit-plan.json', output/'compiled-base.json')
    p = read(output/'compiled-base.json')
    from create_chapter import make_project
    from apply_source_chat import wrap_text
    for i, event in enumerate(clip['events']):
        kind = event['kind']
        if kind == 'emphasis':
            continue
        if kind == 'chapter':
            fields(event, ['kind', 'source_range', 'text', 'number', 'reason'])
            t = make_project(event['text'], event['number'], '1', output/'template.json', kit=KIT)
            for group in ('media', 'fonts'):
                for key, item in t[group].items():
                    item = copy.deepcopy(item); item['path'] = str(resolve(output, item['path']))
                    p[group]['chapter-'+key] = item
            layers = [{'kind': 'image', 'source': 'procedural-gradient', 'position': [0, 0], 'z': 1}, *t['layers']]
            for l in layers:
                for field in ('source', 'font'):
                    if field in l:
                        l[field] = 'chapter-'+l[field]
        elif kind == 'illustration':
            fields(event, ['kind', 'source_range', 'asset', 'reason'])
            path, provenance = asset(event['asset']); key = 'art-'+str(i)
            p['media'][key] = {'path': str(path), 'type': 'image', 'role': 'generated_illustration', 'sha256': sha(path),
                               'provenance': str(provenance), 'provenance_sha256': sha(provenance)}
            w, h = Image.open(path).size
            crop = [0, (h-w*9/16)/2, w, (h+w*9/16)/2] if w/h < 16/9 else [(w-h*16/9)/2, 0, (w+h*16/9)/2, h]
            layers = [{'kind': 'image', 'source': key, 'crop': crop, 'size': [1922, 1082], 'position': [960, 540], 'anchor': 'center', 'z': 5}]
        else:
            fields(event, ['kind', 'source_range', 'sender', 'text', 'reason'], ['top', 'left', 'color'])
            left, top = event.get('left', 93), event.get('top', 180)
            require(all(finite(x) for x in (left, top)) and 0 <= left <= p['width']-640 and 0 <= top <= 520, 'Chat must stay inside canvas and above caption safe area')
            color = event.get('color', '#FDE400')
            require(color in ('#FDE400', '#FFFFFF'), 'Observed A bubble color required')
            style = {'font': 'body', 'font_size': 48, 'glyph_height': 27, 'line_gap': 12, 'fill': '#000000'}
            comp = Composer(p, output); wrapped = wrap_text(event['text'], comp, style, 490)
            lines = wrapped.split('\n'); bw = max(comp.text_mask(t, style).width for t in lines)+42
            bh = sum(comp.text_mask(t, style).height for t in lines)+12*(len(lines)-1)+44
            require(top+120+bh < 925, 'Chat overlaps dialogue; shorten or move it')
            layers = [
                {'kind': 'rect', 'position': [left, top], 'size': [640, 120+bh], 'radius': 26, 'fill': '#B9CDE0', 'z': 6},
                {'kind': 'text', **style, 'text': event['sender'], 'glyph_height': 21, 'position': [left+30, top+34], 'z': 7},
                {'kind': 'rect', 'position': [left+30, top+96], 'size': [bw, bh], 'radius': 24, 'fill': color, 'z': 7},
                {'kind': 'text', **style, 'text': wrapped, 'position': [left+51, top+118], 'z': 8}]
        for j, (lo, hi, tid) in enumerate(map_range(maps, event['source_range'])):
            for k, layer in enumerate(layers):
                q = copy.deepcopy(layer); q.update(id=f'{kind}-{i}-{j}-{k}', **{'in': lo, 'out': hi})
                q['source_visual_binding'] = {'source_range': event['source_range'], 'take_id': tid, 'reason': event['reason']}
                p['layers'].append(q)
    return p


def shorts(plan, clip, maps, output, meta):
    require(not clip['events'], 'Short events are caption_styles; longform events must not be silently dropped')
    headline = clip.get('headline')
    require(isinstance(headline, list) and len(headline) == 2 and all(isinstance(t, str) and t.strip() for t in headline), 'Two headline lines required')
    p = {'schema': 'atrass-render-v1', 'title': clip['title'], 'variant': 'A', 'width': 1080, 'height': 1920,
         'fps': '30', 'duration': maps[-1]['output_frames'][1]/30, 'media': plan['media'], 'fonts': copy.deepcopy(plan['fonts']),
         'clips': [], 'audio': [], 'audio_master': plan['audio_master'], 'layers': [], 'release_ready': False}
    p['fonts'].update(headline={'path': str(KIT/'assets/fonts/PretendardVariable.ttf'), 'weight': 600},
                      strong={'path': str(KIT/'assets/fonts/NotoSansKR[wght].ttf'), 'weight': 850})
    p['media']['brand'] = {'path': str(KIT/'assets/company-library/B-top-left-logo-1920.png'), 'type': 'image', 'role': 'existing_brand_asset'}
    require(.1 <= clip.get('focus_x', .5) <= .9, 'Subject focus_x must be a normalized horizontal coordinate')
    scale = max(1080/meta['width'], 1024/meta['height'])
    for t, m in zip(plan['takes'], maps):
        lo, hi = [n/30 for n in m['output_frames']]
        p['clips'].append({'id': t['id'], 'source': 'raw', 'source_in': t['source_in'], 'in': lo, 'out': hi, 'rate': 1,
            'geometry': {'scale': scale, 'tx': 540-meta['width']*clip.get('focus_x', .5)*scale, 'ty': 576}})
        p['audio'].append({'id': t['id']+'-voice', 'source': 'raw', 'source_in': t['source_in'], 'in': lo, 'out': hi, 'rate': 1, 'gain_db': 0})
    def add(eid, kind, **kw):
        p['layers'].append({'id': eid, 'kind': kind, 'in': 0, 'out': p['duration'], **kw})
    add('header', 'rect', position=[0, 0], size=[1080, 576], fill='#202020', z=3)
    add('footer', 'gradient', position=[0, 1600], size=[1080, 320], stops=[[0, '#1E1D21'], [1, '#211C26']], z=3)
    for i, text in enumerate(headline):
        add('headline-'+str(i), 'text', font='headline', font_size=91, text=text, fill=['#FFFFFF', '#61D5F2'][i], position=[540, 346+108*i], anchor='top_center', z=5)
    add('brand', 'image', source='brand', crop=[53, 44, 307, 106], size=[356, 87], position=[540, 1708], anchor='top_center', z=5)
    add('full-video', 'text', font='body', font_size=33, text='풀영상', fill='#D9D7DC', position=[558, 1830], anchor='center', z=5)
    add('arrow', 'polyline', size=[32, 34], points=[[4, 30], [27, 7], [11, 7], [27, 7], [27, 23]], width=3, fill='#61D5F2', position=[466, 1813], z=5)
    styles = clip.get('caption_styles', [])
    for s in styles:
        fields(s, ['source_range', 'style']); require(s['style'] in ('plain', 'quote', 'emphasis'), 'Unknown caption style')
        map_range(maps, s['source_range'])
    for event in plan['events']:
        source = event['source_dialogue_binding']['source_range']
        matches = [s['style'] for s in styles if s['source_range'][0] <= (source[0]+source[1])/2 < s['source_range'][1]]
        require(len(matches) <= 1, 'Caption styles overlap')
        style = matches[0] if matches else 'plain'
        p['layers'].append({'id': event['id'], 'kind': 'caption', 'in': event['in'], 'out': event['out'],
            'font': 'strong' if style == 'emphasis' else 'body', 'font_size': 69 if style == 'emphasis' else 59,
            'text': event['text'], 'fill': '#F8C56C' if style == 'quote' else '#FFFFFF',
            'background': [0, 0, 0, 230], 'padding': [18, 12], 'position': [540, 1480], 'anchor': 'center', 'z': 9,
            'source_dialogue_binding': event['source_dialogue_binding']})
    comp = Composer(p, output)
    for layer in p['layers']:
        if layer['kind'] in ('text', 'caption'):
            while comp.text_image(layer).width > 970 and layer['font_size'] > 44:
                layer['font_size'] -= 1
            require(comp.text_image(layer).width <= 970, 'Text exceeds safe area; split the wording: '+layer['id'])
    return p


def build(job_path, decisions_path, output):
    job_path, decisions_path, output = Path(job_path).resolve(strict=True), Path(decisions_path).resolve(strict=True), Path(output).resolve()
    require(not output.exists(), 'Choose a fresh build directory')
    job, d = read(job_path), read(decisions_path)
    fields(d, ['schema', 'job_sha256', 'clips'], ['candidates'])
    require(d['schema'] == SCHEMA and d['job_sha256'] == sha(job_path) and job['schema'] == SCHEMA, 'Decisions belong to another job')
    verify_bindings([job['raw']])
    transcript, unit_file = job_path.parent/'transcript.json', job_path.parent/'units.json'
    require(sha(transcript) == job['transcript_sha256'] and sha(unit_file) == job['units_sha256'], 'Prepared transcript changed')
    units = read(unit_file)['units']; meta = job['metadata']
    require(isinstance(d['clips'], list) and len(d['clips']) == job['count'], 'Requested output count differs from decisions')
    ids = [c['id'] for c in d['clips']]
    require(len(set(ids)) == len(ids) and all(isinstance(x, str) and re.fullmatch('[a-z][a-z0-9-]{0,40}', x) for x in ids), 'Unique safe clip IDs required')
    if job['kind'] == 'shorts':
        candidates = d.get('candidates')
        require(isinstance(candidates, list) and len(candidates) >= 10, 'At least ten source-bound Short candidates required')
        candidate_ids, candidate_ranges = set(), set()
        for c in candidates:
            fields(c, ['id', 'title', 'keep', 'hook', 'turn', 'ending', 'reason', 'score'])
            require(c['id'] not in candidate_ids and finite(c['score']) and 0 <= c['score'] <= 100, 'Invalid candidate ID or editorial score')
            require(isinstance(c['id'], str) and re.fullmatch('[a-z][a-z0-9-]{0,40}', c['id']), 'Safe candidate ID required')
            selected = select_units(units, c['keep'])
            signature = tuple(u['id'] for u in selected)
            require(signature not in candidate_ranges, 'Ten renamed duplicates are not ten candidates')
            candidate_ranges.add(signature); candidate_ids.add(c['id'])
            require(all(isinstance(c[k], str) and c[k].strip() for k in ('title', 'hook', 'turn', 'ending', 'reason')), 'Candidate needs independent opening, turn and ending')
        require(set(ids) <= candidate_ids, 'Selected Short is not in candidate list')
        for c in d['clips']:
            require(c['keep'] == next(x['keep'] for x in candidates if x['id'] == c['id']), 'Selected ranges differ from ranked candidate')
    else:
        require('candidates' not in d, 'Short candidates do not belong in a longform job')
    output.mkdir(parents=True)
    projects = []
    for clip in d['clips']:
        fields(clip, ['id', 'title', 'keep', 'reason', 'events'], ['headline', 'focus_x', 'caption_styles', 'corrections', 'caption_breaks', 'framing'])
        require(isinstance(clip['reason'], str) and clip['reason'].strip() and isinstance(clip['events'], list), 'Explicit editorial rationale required')
        if job['kind'] == 'longform':
            require(not any(k in clip for k in ('headline', 'focus_x', 'caption_styles')), 'Short-specific fields cannot be ignored in longform')
        selected = select_units(units, clip['keep'])
        selected_by_id = {u['id']: u for u in selected}
        corrected = set()
        for correction in clip.get('corrections', []):
            fields(correction, ['unit', 'before', 'after', 'reason'])
            u = selected_by_id.get(correction['unit'])
            require(u and u['id'] not in corrected and u['text'] == correction['before'] and isinstance(correction['after'], str)
                    and correction['after'].strip() and correction['reason'], 'Unbound text correction')
            u['text'] = correction['after']; corrected.add(u['id'])
        spans = take_spans(units, selected, meta['duration']); cuts = []; last = 0
        if job['kind'] == 'shorts':
            spans[0][0] = math.floor(selected[0]['start']*30+1e-8)
        for a, b in spans:
            if last < a: cuts.append([last, a])
            last = b
        if last < math.floor(meta['duration']*30): cuts.append([last, math.floor(meta['duration']*30)])
        directory = output/clip['id']; directory.mkdir()
        font = KIT/'assets/fonts/SUIT-Variable.ttf'
        comp = Composer({'width': 1920, 'height': 1080, 'fonts': {'body': {'path': str(font), 'weight': 450}}}, directory)
        limit = 1640 if job['kind'] == 'longform' else 860
        def fits(text):
            return comp.text_mask(text, {'font': 'body', 'font_size': 54, 'tracking': 0}).width <= limit
        groups = []
        breaks = clip.get('caption_breaks', [])
        require(isinstance(breaks, list) and all(type(x) is int and x in selected_by_id for x in breaks)
                and breaks == sorted(set(breaks)), 'Caption breaks must be unique retained unit IDs')
        for a, b in spans:
            own = [u for u in selected if a/30-.001 <= u['start'] and u['end'] <= b/30+.001]
            partition = []
            for unit in own:
                if unit['id'] in breaks and partition:
                    groups += balanced_groups(partition, fits); partition = []
                partition.append(unit)
            groups += balanced_groups(partition, fits)
        require({u['id'] for u in selected} and sum(len(g['refs']) for g in groups) == sum(len(u['refs']) for u in selected), 'Retained dialogue was lost or duplicated')
        plan, ledger = plan_from_evidence(Path(job['raw']['path']), meta['duration'], job['raw']['sha256'], read(transcript),
                                         job['transcript_sha256'], groups, cuts, FPS, font)
        plan['title'] = clip['title']; plan['automatic_draft']['policy'] = 'explicit_host_agent_semantic_decisions_v1'
        plan['audio_master']['sample_underflow_policy'] = 'bounded_linear_rate1_one_sample'
        for take in plan['takes']:
            s = max(1920/meta['width'], 1080/meta['height'])
            take['geometry'] = {'scale': s, 'tx': (1920-meta['width']*s)/2, 'ty': (1080-meta['height']*s)/2}
        p = longform(plan, clip, ledger['take_mapping'], directory) if job['kind'] == 'longform' else shorts(plan, clip, ledger['take_mapping'], directory, meta)
        if 'framing' in clip:
            require(job['kind'] == 'longform', 'Centered longform framing cannot replace Short face positioning')
            apply_framing(p, clip['framing'], ledger['take_mapping'], meta)
        if job['kind'] == 'shorts':
            require(20 <= p['duration'] <= 60, 'Short must contain 20..60 seconds of retained original speech')
        for row in p['audio']: row.update(fade_in=.004, fade_out=.004)
        for group in ('fonts', 'media'):
            for item in p[group].values():
                item['path'] = str(resolve(directory, item['path']).resolve(strict=True))
                item['sha256'] = sha(item['path'])
        cmaps = {}
        for layer in p['layers']:
            if layer.get('kind') in ('text', 'caption', 'label'):
                font_name = layer['font']
                if font_name not in cmaps:
                    with TTFont(p['fonts'][font_name]['path'], lazy=True) as face:
                        cmaps[font_name] = face.getBestCmap()
                require(all(ch.isspace() or ord(ch) in cmaps[font_name] for ch in layer['text']), 'Missing text glyph: '+layer['id'])
        p['production_evidence'] = {'job_sha256': sha(job_path), 'decisions_sha256': sha(decisions_path), 'source_sha256': job['raw']['sha256'],
            'decision_author': 'hosting_agent', 'direct_listening_seconds': 0, 'reference_video_or_audio_passthrough': False}
        validate(p, directory, True)
        save(directory/'project.json', p)
        save(directory/'edit-ledger.json', {**ledger, 'keep_units': [u['id'] for u in selected],
            'removed_units': [u for u in units if u['id'] not in selected_by_id], 'corrections': clip.get('corrections', []),
            'boundary_limit': 'ASR estimates; outward rounding may retain under one frame of adjacent material. Direct listening is still required.', 'release_ready': False})
        projects.append({'id': clip['id'], 'project': str(directory/'project.json'), 'duration': p['duration'], 'layers': len(p['layers'])})
    save(output/'build-result.json', {'projects': projects, 'release_ready': False})
    return {'projects': projects, 'next': 'Render every requested project, inspect returned review frames and listen where supported; fix failures before handing over.'}


def check_movie(project, movie, output, ffmpeg):
    """Whole decode and sample-clock checks; compact aspect-correct review sheets."""
    import numpy as np
    p = read(project); expected = round(p['duration']*30)
    chosen = {0, expected-1}
    for row in p['clips']:
        for n in (round(row['in']*30)-1, round(row['in']*30)): chosen.add(n)
    for row in p['layers']: chosen.add(round((row['in']+row['out'])*15))
    chosen = {n for n in chosen if 0 <= n < expected}
    thumb_w = 360 if p['height'] > p['width'] else 640
    thumb_h = round(thumb_w*p['height']/p['width'])
    frames = []; count = 0
    with av.open(str(movie)) as c:
        v = c.streams.video[0]
        require(v.average_rate == 30, 'Output frame rate differs')
        for f in c.decode(v):
            require(Fraction(f.pts)*f.time_base == Fraction(count, 30) and (f.width, f.height) == (p['width'], p['height']), 'Video PTS or dimensions differ')
            if count in chosen: frames.append((count, f.to_image().resize((thumb_w, thumb_h))))
            count += 1
    require(count == expected, 'Full output frame count differs')
    samples = 0; end = Fraction(0); peak = 0.
    with av.open(str(movie)) as c:
        a = c.streams.audio[0]
        require(a.sample_rate == 48000 and a.channels == 2 and abs(float(a.duration*a.time_base)-p['duration']) <= 1/48000+1e-9, 'Audio duration/format differs')
        for f in c.decode(a):
            require(Fraction(f.pts)*f.time_base == end, 'Audio gap or overlap')
            end += Fraction(f.samples, f.sample_rate); samples += f.samples
            peak = max(peak, float(np.max(np.abs(f.to_ndarray()))))
    require(samples >= round(p['duration']*48000) and peak < 1, 'Audio truncated or clipping')
    output.mkdir()
    columns = 4 if thumb_w == 360 else 3
    page_size = columns*2; sheets = []
    for i in range(0, len(frames), page_size):
        page = frames[i:i+page_size]; sheet = Image.new('RGB', (thumb_w*columns, (thumb_h+26)*math.ceil(len(page)/columns)), '#242424')
        draw = ImageDraw.Draw(sheet)
        for j, (n, im) in enumerate(page):
            x, y = j%columns*thumb_w, j//columns*(thumb_h+26)
            draw.text((x+4, y+5), f'{n} / {n/30:.3f}s', fill='white'); sheet.paste(im, (x, y+26))
        path = output/f'review-{i//page_size+1:03}.jpg'; sheet.save(path, quality=90)
        sheets.append({'path': str(path), 'frames': [n for n, _ in page]})
    cmd = [ffmpeg, '-hide_banner', '-nostdin', '-i', str(movie), '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-']
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    (output/'loudness.txt').write_text(r.stderr, encoding='utf-8'); measure = parse_loudness(r.stderr)
    require(float(measure['input_tp']) <= -1, 'True peak exceeds output ceiling')
    result = {'schema': SCHEMA, 'movie_sha256': sha(movie), 'project_sha256': sha(project), 'decoded_frames': count,
        'decoded_samples_with_codec_padding': samples, 'peak': peak, 'loudness': measure, 'sheets': sheets,
        'signal_checks_pass': True, 'actual_viewed_frames': [], 'direct_listening_seconds': 0, 'release_ready': False}
    save(output/'validation.json', result)
    return result


def finish(project, output, ffmpeg=None):
    project, output = Path(project).resolve(strict=True), Path(output).resolve()
    require(not output.exists(), 'Choose a fresh render directory')
    output.mkdir(parents=True)
    with job_lock(output):
        p = read(project); validate(p, project.parent, True); ff = ffmpeg_path(ffmpeg)
        inputs = [binding(project)]
        for group in ('media', 'fonts'):
            for item in p[group].values():
                row = binding(resolve(project.parent, item['path']))
                require(item.get('role') not in ('reference', 'reference_video', 'finished_reference'), 'Reference footage cannot enter production')
                require(item.get('sha256') == row['sha256'], 'Project resource changed after build')
                inputs.append(row)
        save(output/'inputs.json', inputs)
        raw_movie = output/'render.mp4'
        report = render(project, raw_movie, draft=True, ffmpeg=ff)
        pcm, stats = render_audio(p, project.parent, 0, p['duration'], ff, output)
        r = subprocess.run([ff, '-hide_banner', '-f', 'f32le', '-ar', '48000', '-ac', '2', '-i', str(pcm), '-af',
            'loudnorm=I=-16:TP=-3:LRA=11:print_format=json', '-f', 'null', '-'], capture_output=True, text=True, check=True)
        m = parse_loudness(r.stderr); (output/'loudness-pass1.txt').write_text(r.stderr, encoding='utf-8')
        gain = max(-24., min(18., -16-float(m['input_i'])))
        af = f'volume={gain:.8f}dB,alimiter=limit=0.707945784:level=false:latency=true,aresample=48000,asettb=1/48000,asetpts=N'
        final = output/'final.mp4'
        cmd = [ff, '-hide_banner', '-i', str(raw_movie), '-f', 'f32le', '-ar', '48000', '-ac', '2', '-i', str(pcm),
            '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-af', af, '-ar', '48000', '-c:a', 'aac', '-b:a', '256k',
            '-movie_timescale', '240000', '-movflags', '+faststart', '-t', f"{p['duration']:.9f}", str(final)]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True); (output/'mux.txt').write_text(r.stderr, encoding='utf-8')
        verify_bindings(inputs)
        checked = check_movie(project, final, output/'review', ff)
        result = {'movie': str(final), 'movie_sha256': sha(final), 'project': str(project), 'signal_checks_pass': True,
                  'review': str(output/'review/validation.json'), 'release_ready': False, 'direct_listening_seconds': 0}
        save(output/'result.json', result)
        return result


def main():
    ap = argparse.ArgumentParser(description=__doc__); sub = ap.add_subparsers(dest='cmd', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--raw', required=True); prep.add_argument('--kind', choices=['longform', 'shorts'], required=True)
    prep.add_argument('--output-dir', required=True); prep.add_argument('--count', type=int, default=1)
    prep.add_argument('--asr-json'); prep.add_argument('--model'); prep.add_argument('--backend', choices=['auto', 'mlx', 'cpu'], default='auto'); prep.add_argument('--ffmpeg')
    build_cmd = sub.add_parser('build'); build_cmd.add_argument('--job', required=True); build_cmd.add_argument('--decisions', required=True); build_cmd.add_argument('--output-dir', required=True)
    render_cmd = sub.add_parser('render'); render_cmd.add_argument('--project', required=True); render_cmd.add_argument('--output-dir', required=True); render_cmd.add_argument('--ffmpeg')
    args = ap.parse_args()
    if args.cmd == 'prepare':
        result = prepare(args.raw, args.kind, args.output_dir, args.asr_json, args.model, args.backend, args.ffmpeg, args.count)
    elif args.cmd == 'build': result = build(args.job, args.decisions, args.output_dir)
    else: result = finish(args.project, args.output_dir, args.ffmpeg)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
