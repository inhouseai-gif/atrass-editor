#!/usr/bin/env python3
"""Append an explicitly selected raw blooper, J-cut and procedural light field.

This compiles editorial decisions, not automatic blooper detection. The smooth
field contains 40 polynomial coefficients per phase, never reference pixels.
Source, timing, text and the profile are hash-bound; outputs are create-only.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, json, math, os
import numpy as np
from PIL import Image
from apply_source_assets import read, sha, require, keys, number, path_at, relative, frame_boundary
from render import validate

SCHEMA = 'atrass-source-ending-v1'
PROFILE = 'atrass-normalized-polynomial-light-v1'


def frames(seconds, fps, label):
    require(number(seconds) and seconds >= 0, label + ' must be finite and nonnegative')
    return round(seconds * float(fps))


def field(profile, phase, width=640, height=360):
    require(profile['schema'] == PROFILE, 'Unknown field profile')
    rows = profile['keyframes']
    ts = np.array([r['phase'] for r in rows], dtype=float)
    cs = np.array([r['coefficients'] for r in rows], dtype=float)
    require(len(rows) >= 2 and cs.shape == (len(rows), 40), 'Field needs 40 coefficients per phase')
    require(np.isfinite(cs).all() and np.isfinite(ts).all() and ts[0] == 0 and ts[-1] == 1 and np.all(np.diff(ts) > 0), 'Invalid field keyframes')
    require(number(phase) and 0 <= phase <= 1, 'Invalid effect phase')
    coeff = np.array([np.interp(phase, ts, cs[:, i]) for i in range(40)])
    y, x = np.mgrid[-1:1:complex(height), -1:1:complex(width)]
    basis = np.stack([np.ones_like(x), x, y, x*x, x*y, y*y, x**3, x*x*y, x*y*y, y**3], axis=-1)
    alpha = basis @ coeff[:10]
    offset = np.maximum(np.stack([basis @ coeff[10+i*10:20+i*10] for i in range(3)], -1), 0)
    alpha = np.clip(np.maximum(alpha, offset.max(axis=2)), 0, .98)
    rgb = np.clip(offset / np.maximum(alpha[:, :, None], 1e-8), 0, 1)
    return Image.fromarray(np.uint8(np.clip(np.dstack([rgb, alpha])*255, 0, 255)))


def transform(p, spec, source_duration):
    required = ('schema', 'project_sha256', 'source', 'video_source_in', 'duration_seconds',
                'audio_source_in', 'audio_lead_seconds', 'gain_db', 'effect', 'closing', 'label', 'evidence')
    keys(spec, required, required, 'Ending decision')
    require(spec['schema'] == SCHEMA and isinstance(spec['evidence'], str) and spec['evidence'].strip(), 'Ending schema/evidence')
    require('source_ending_application' not in p, 'Ending already applied')
    require(p['width'] == 1920 and p['height'] == 1080, 'Measured layout currently requires 1920x1080')
    source = spec['source']; require(source in p['media'] and p['media'][source].get('role') == 'source_footage', 'Only raw source footage is allowed')
    fps = Fraction(p['fps']); cut = frame_boundary(p['duration'], fps)
    duration = frames(spec['duration_seconds'], fps, 'Blooper duration')
    lead = spec['audio_lead_seconds']
    require(2 <= duration <= 30*float(fps), 'Blooper duration must be 2 frames to 30 seconds')
    require(number(lead) and 0 <= lead <= 1, 'Audio lead must be 0–1 seconds')
    require(number(spec['gain_db']) and -30 <= spec['gain_db'] <= 12, 'Invalid audio gain')
    for name in ('video_source_in', 'audio_source_in'):
        require(number(spec[name]) and spec[name] >= 0, 'Invalid raw source start')
    sec = lambda f: float(Fraction(f, 1)/fps)
    end = sec(cut+duration); audio_start = sec(cut)-lead
    require(spec['video_source_in']+sec(duration) <= source_duration and spec['audio_source_in']+end-audio_start <= source_duration, 'Ending exceeds raw duration')
    clips = sorted(p['clips'], key=lambda c: c['in']); last = clips[-1]
    require(last['source'] == source and last['out'] == p['duration'] and last.get('rate', 1) == 1, 'Ending must follow a rate1 raw take')
    voices = [a for a in p['audio'] if a['out'] > audio_start]
    require(len(voices) == 1 and voices[0]['out'] == p['duration'] and voices[0]['source'] == source and voices[0]['in'] < audio_start and voices[0].get('rate', 1) == 1, 'J-cut requires one closing raw voice and no overlapping audio')
    require(not voices[0].get('fade_out'), 'Existing closing fade needs a separate decision')
    effect = spec['effect']; keys(effect, ('profile', 'duration_seconds', 'gap_before_cut_seconds'), ('profile', 'duration_seconds', 'gap_before_cut_seconds'), 'Effect')
    ef = frames(effect['duration_seconds'], fps, 'Effect duration'); gap = frames(effect['gap_before_cut_seconds'], fps, 'Effect gap')
    require(2 <= ef <= 5*float(fps) and gap >= 1 and cut-ef-gap >= frame_boundary(last['in'], fps), 'Effect must end before the cut and stay within closing take')
    closing = spec['closing']; keys(closing, ('caption_id', 'clear_before_cut_seconds', 'source_in', 'headings'), ('caption_id', 'clear_before_cut_seconds', 'source_in', 'headings'), 'Closing')
    clear = cut-frames(closing['clear_before_cut_seconds'], fps, 'Closing clear')
    require(cut-ef-gap < clear <= cut-gap, 'Closing text must clear within the light effect')
    caps = [e for e in p['layers'] if e['id'] == closing['caption_id']]
    require(len(caps) == 1 and caps[0]['kind'] == 'caption' and caps[0]['in'] < sec(clear) and caps[0]['out'] <= sec(clear), 'Explicit last caption extension required')
    require(not any(e is not caps[0] and e.get('kind') == 'caption' and e['out'] > caps[0]['out'] for e in p['layers']), 'Another caption follows the selected closing caption')
    require(number(closing['source_in']) and last['source_in'] <= closing['source_in'] < last['source_in']+sec(clear)-last['in'], 'Heading source start outside closing take')
    heading_start = math.ceil((last['in']+closing['source_in']-last['source_in'])*float(fps)-1e-8)
    require(isinstance(closing['headings'], list) and len(closing['headings']) == 2, 'Two measured heading spans required')
    result = copy.deepcopy(p); result['duration'] = end; result['release_ready'] = False
    next(a for a in result['audio'] if a['id'] == voices[0]['id'])['out'] = audio_start
    next(e for e in result['layers'] if e['id'] == caps[0]['id'])['out'] = sec(clear)
    status = 'explicit_raw_ending_candidate_not_perceptually_approved'
    result['clips'].append({'id': 'source-ending-blooper', 'in': sec(cut), 'out': end, 'source': source, 'source_in': spec['video_source_in'], 'rate': 1, 'geometry': {}, 'status': status})
    result['audio'].append({'id': 'source-ending-blooper-voice', 'in': audio_start, 'out': end, 'source': source, 'source_in': spec['audio_source_in'], 'rate': 1, 'gain_db': spec['gain_db'], 'status': status})
    for i, style in enumerate(closing['headings']):
        allowed = ('text', 'font', 'font_size', 'weight', 'tracking', 'glyph_width', 'glyph_height', 'position', 'padding', 'fill')
        keys(style, allowed, ('text', 'font', 'font_size', 'position', 'fill'), 'Heading style')
        require(isinstance(style['text'], str) and style['text'].strip() and style['font'] in p['fonts'], 'Heading text/font')
        result['layers'].append({**copy.deepcopy(style), 'id': f'source-ending-heading-{i+1}', 'kind': 'text', 'in': sec(heading_start), 'out': sec(clear), 'z': 20, 'status': status})
    label = spec['label']; keys(label, ('in_after_cut_seconds', 'out_after_cut_seconds', 'style'), ('in_after_cut_seconds', 'out_after_cut_seconds', 'style'), 'Ending label')
    a = cut+frames(label['in_after_cut_seconds'], fps, 'Label start'); b = cut+frames(label['out_after_cut_seconds'], fps, 'Label end')
    require(cut <= a < b <= cut+duration, 'Label outside blooper')
    style = label['style']; allowed = ('text', 'font', 'font_size', 'weight', 'tracking', 'glyph_width', 'glyph_height', 'box', 'text_offset', 'fill', 'background')
    keys(style, allowed, ('text', 'font', 'font_size', 'box', 'fill', 'background'), 'Label style')
    require(isinstance(style['text'], str) and style['text'].strip() and style['font'] in p['fonts'], 'Label text/font')
    result['layers'].append({**copy.deepcopy(style), 'id': 'source-ending-label', 'kind': 'caption', 'in': sec(a), 'out': sec(b), 'z': 30, 'status': status})
    return result, {'cut_frame': cut, 'new_frame_count': cut+duration, 'video_source_range': [spec['video_source_in'], spec['video_source_in']+sec(duration)], 'audio_source_range': [spec['audio_source_in'], spec['audio_source_in']+end-audio_start], 'audio_lead_seconds': lead, 'audio_video_source_offset_at_cut_seconds': spec['audio_source_in']+lead-spec['video_source_in'], 'effect_frames': [cut-ef-gap, cut-gap], 'heading_frames': [heading_start, clear], 'label_frames': [a, b], 'last_caption_id': caps[0]['id'], 'last_caption_old_out': caps[0]['out'], 'last_caption_new_out': sec(clear), 'closing_voice_id': voices[0]['id'], 'closing_voice_old_out': voices[0]['out'], 'closing_voice_new_out': audio_start}


def apply(project, decision, output):
    project, decision, output = [Path(x).absolute() for x in (project, decision, output)]
    assets = output.with_suffix('.ending-assets')
    require(output.suffix == '.json' and output.parent.resolve() == project.parent.resolve() and not os.path.lexists(output) and not os.path.lexists(assets), 'Choose new adjacent project and asset paths')
    p, psha = read(project); spec, dsha = read(decision); require(psha == spec['project_sha256'], 'Project SHA mismatch')
    dep = spec['effect']['profile']; keys(dep, ('path', 'sha256'), ('path', 'sha256'), 'Profile binding')
    pp = path_at(decision.parent, dep['path']); profile, profile_sha = read(pp); require(profile_sha == dep['sha256'], 'Profile SHA mismatch')
    # Validate the whole polynomial shape before creating files.
    field(profile, 0)
    validate(p, project.parent, True)
    source = p['media'][spec['source']]; raw = path_at(project.parent, source['path']); require(sha(raw) == source['sha256'], 'Raw source SHA mismatch')
    import av
    with av.open(str(raw)) as c:
        stream = c.streams.video[0]; require(stream.duration is not None, 'Raw duration missing'); source_duration = float(stream.duration*stream.time_base)
    result, record = transform(p, spec, source_duration)
    lo, hi = record['effect_frames']; fps = Fraction(p['fps']); asset_rows = []; assets.mkdir()
    for i in range(hi-lo):
        phase = i/(hi-lo-1); path = assets/f'light-{i:04}.png'; field(profile, phase).save(path)
        mid = f'source-ending-light-{i:04}'; digest = sha(path)
        result['media'][mid] = {'path': relative(path, output.parent), 'type': 'image', 'role': 'procedural_color_field', 'sha256': digest, 'profile_sha256': profile_sha, 'source_reference_pixels_used': False}
        result['layers'].append({'id': mid, 'kind': 'image', 'source': mid, 'in': float(Fraction(lo+i, 1)/fps), 'out': float(Fraction(lo+i+1, 1)/fps), 'size': [1920, 1080], 'position': [0, 0], 'z': 100, 'status': 'finite_polynomial_light_approximation_not_original_effect'})
        asset_rows.append({'path': path.name, 'sha256': digest, 'phase': phase})
    result['source_ending_application'] = {'schema': SCHEMA, 'input_project': relative(project, output.parent), 'input_sha256': psha, 'decisions': relative(decision, output.parent), 'decisions_sha256': dsha, 'profile': relative(pp, output.parent), 'profile_sha256': profile_sha, 'implementation_sha256': sha(Path(__file__)), 'record': record, 'assets': asset_rows, 'direct_listening_seconds': 0, 'release_ready': False}
    validate(result, output.parent, True)
    require(sha(project) == psha and sha(decision) == dsha and sha(pp) == profile_sha, 'Inputs changed during build')
    with output.open('x', encoding='utf-8') as f: json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')
    return {'project': str(output), 'project_sha256': sha(output), 'record': record, 'assets': len(asset_rows), 'release_ready': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'decision', 'output'): parser.add_argument('--'+name, required=True)
    args = parser.parse_args(); print(json.dumps(apply(args.project, args.decision, args.output), ensure_ascii=False))
