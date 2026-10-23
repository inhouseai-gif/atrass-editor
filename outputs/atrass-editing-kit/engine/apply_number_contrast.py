#!/usr/bin/env python3
"""Replace an explicit oversized dialogue cue with two source-timed numbers.

A-only candidate layout learned from the 100/10 reference. Values and the second
number's source word come from a hash-bound editorial decision, never from the
reference movie's timestamps. Keeps the dialogue as a natural-width caption.
"""
import argparse, copy, json, math, os, re
from fractions import Fraction
from pathlib import Path

from apply_source_assets import read, sha, require, relative, keys
from compile_plan import complete_emphasis_mask
from render import Composer, validate, resolve

SCHEMA = 'atrass-source-number-contrast-v1'
PROFILE = {
    'id': 'A-two-person-number-contrast-v1',
    'reference': 'A-left_heading-0008 / A-other_graphic_text-0016',
    'reference_geometry_evidence': 'evidence/mid-160-211-v1/research',
    'font_identity': 'candidate_not_original_font_identified',
    'timing': 'first_at_art_start_second_at_explicit_source_word_nearest_output_frame',
    'colors': {'left': '#FFFFFF', 'right': '#FFFBDA'},
    'color_basis': 'decoded_reference_RGB_not_authored_RGBA',
    'left': {'font': 'body', 'weight': 550, 'height': 223, 'center': [500, 429],
             'calibration_text': '100', 'calibration_width': 327},
    'right': {'font': 'source-chapter-pretendard', 'weight': 650, 'height': 217,
              'center': [1460, 432], 'calibration_text': '10', 'calibration_width': 193},
    'shape_policy': 'one_constant_horizontal_font_scale_per_role; new_values_use_natural_width',
    'max_number_width': 600,
}


def transform(p, root, d):
    required = ('schema', 'project_sha256', 'event_id', 'asset_layer_id', 'replace_layer_id',
                'first_caption_id', 'caption_text', 'values', 'second_word', 'reason', 'evidence')
    keys(d, required, required, 'number contrast decision')
    require(d['schema'] == SCHEMA, 'Invalid decision schema')
    require(p.get('variant') == 'A' and (p['width'], p['height']) == (1920, 1080), 'A 1080p required')
    require('source_number_contrast' not in p, 'Already applied; start from an unmodified input')
    for name in ('event_id', 'asset_layer_id', 'replace_layer_id', 'first_caption_id', 'reason', 'evidence'):
        require(isinstance(d[name], str) and d[name].strip(), 'Nonempty ' + name + ' required')
    values = d['values']
    require(isinstance(values, list) and len(values) == 2 and
            all(isinstance(s, str) and re.fullmatch(r'(?:0|[1-9][0-9]{0,3})', s) for s in values),
            'Two distinct unsigned integer strings of at most four digits required')
    require(values[0] != values[1], 'A comparison needs distinct values')
    by_id = {e['id']: e for e in p['layers']}
    require(len(by_id) == len(p['layers']), 'Duplicate layer id')
    require(all(d[k] in by_id for k in ('asset_layer_id', 'replace_layer_id', 'first_caption_id')),
            'Explicit existing layers required')
    art, old, first = [by_id[d[k]] for k in ('asset_layer_id', 'replace_layer_id', 'first_caption_id')]
    require(art['kind'] == 'image' and old['kind'] == 'text' and first['kind'] == 'caption',
            'Expected art, oversized text and first dialogue caption')
    require(not any(k.endswith('_keys') or k in ('fade_in', 'fade_out', 'character_reveal') for k in old),
            'Animated text requires a separate conversion policy')
    require(art.get('source_asset_binding') and old.get('source_visual_binding') and first.get('source_dialogue_binding'),
            'Source-bound layers required')
    binding = old['source_visual_binding']; take = binding['take_id']
    require(art['source_asset_binding']['take_id'] == first['source_dialogue_binding']['take_id'] == take,
            'All layers must share one selected source take')
    require(abs(art['in']-first['in']) < 1e-8 and abs(first['out']-old['in']) < 1e-8 and
            abs(art['out']-old['out']) < 1e-8, 'Two contiguous captions must cover exactly the art interval')
    caption = d['caption_text']
    require(isinstance(caption, str) and caption == old['text'].removesuffix('.'),
            'Only an existing terminal period may be removed; no spoken words can change')
    number_pattern = r'(?<![0-9])[0-9]+(?![0-9])'
    require(values[0] in re.findall(number_pattern, first['text']) and
            values[1] in re.findall(number_pattern, caption), 'Values must appear in their source captions')
    w = d['second_word']; keys(w, ('text', 'source_start', 'source_end', 'basis'),
                             ('text', 'source_start', 'source_end', 'basis'), 'second source word')
    require(isinstance(w['text'], str) and values[1] in re.findall(number_pattern, w['text']) and
            w['text'].strip() in caption, 'Second source word must contain the selected number')
    require(w['basis'] == 'existing_ASR_word_timestamp_not_direct_listening', 'Explicit ASR timing basis required')
    require(all(type(w[k]) in (int, float) and math.isfinite(w[k]) for k in ('source_start', 'source_end')),
            'Finite source timestamps required')
    require(binding['source_range'][0] <= w['source_start'] < w['source_end'] <= binding['source_range'][1],
            'Source word outside the selected dialogue')
    clips = [c for c in p['clips'] if c['id'] == take]
    require(len(clips) == 1, 'One source take required'); clip = clips[0]
    require(clip.get('rate', 1) == 1 and not clip.get('freeze_source') and 'source_frame_times' not in clip,
            'Rate1 contiguous source time required')
    require(clip['in'] <= art['in'] < art['out'] <= clip['out'], 'Art must remain in the source take')
    fps = Fraction(p['fps']); second_frame = round((clip['in']+w['source_start']-clip['source_in'])*fps)
    second_time = float(Fraction(second_frame, 1)/fps)
    require(old['in'] <= second_time < old['out'], 'Mapped second number outside its caption')
    for e in p['layers']:
        if e['id'] not in (old['id'], first['id']) and e['kind'] == 'caption':
            require(not (e['in'] < old['out'] and e['out'] > old['in']), 'Another caption occupies the replacement')
    q = copy.deepcopy(p); comp = Composer(q, root)
    caption_layer = {k: copy.deepcopy(first[k]) for k in
                     ('kind', 'font', 'font_size', 'tracking', 'fill', 'background', 'padding', 'z')}
    # Composer permits a caption to inherit its variable-font weight from the
    # font descriptor. Conservative drafts use that form; older authored plans
    # stored the same value on every caption. Preserve the effective value.
    weight = first.get('weight', p['fonts'][first['font']].get('weight'))
    if weight is not None:
        caption_layer['weight'] = copy.deepcopy(weight)
    caption_layer.update(id=old['id'], text=caption, **{'in': old['in'], 'out': old['out']},
                         status='source_bound_number_contrast_caption_candidate',
                         source_dialogue_binding=copy.deepcopy(binding))
    mask = comp.text_mask(caption, caption_layer)
    complete = complete_emphasis_mask(comp, caption, caption_layer)
    require(mask.size == complete.size and mask.tobytes() == complete.tobytes(), 'Incomplete caption raster')
    require(0 < mask.width <= 1640 and 0 < mask.height <= 64, 'Caption must fit without clipping or shrinking')
    left = round(960-(mask.width+32)/2)
    caption_layer.update(box=[left, 938, left+mask.width+32, 1018], text_offset=[16, round((80-mask.height)/2)])
    q['layers'] = [caption_layer if e['id'] == old['id'] else e for e in q['layers']]
    numbers = []; geometries = []
    expected_fonts = {'body': '12e1fea375d27869a686d7432c1e6a38154d394994f5d82ace24f36a7db68333',
                      'source-chapter-pretendard': '347376fe0eec9f09b6b930429db48981303741e6bee28b78bbb17f8080ea513b'}
    for i, role in enumerate(('left', 'right')):
        spec = PROFILE[role]; font = spec['font']
        require(font in p['fonts'] and sha(resolve(root, p['fonts'][font]['path'])) == expected_fonts[font],
                'Candidate number font hash mismatch')
        e = {'id': d['event_id']+'-'+role, 'kind': 'text', 'text': values[i], 'font': font,
             'font_size': 120, 'weight': spec['weight'], 'tracking': 0, 'glyph_height': spec['height'],
             'fill': PROFILE['colors'][role], 'position': spec['center'], 'anchor': 'top_center', 'z': 30,
             'in': art['in'] if i == 0 else second_time, 'out': art['out'],
             'status': 'source_bound_number_contrast_candidate_not_original_font_confirmed'}
        require(e['id'] not in by_id, 'New number id collision')
        calibration = comp.text_mask(spec['calibration_text'], e)
        e['scale_x'] = spec['calibration_width']/calibration.width
        actual = comp.text_mask(values[i], e)
        require(actual.width <= PROFILE['max_number_width'], 'Number exceeds the per-person safe area')
        e['source_number_binding'] = {'event_id': d['event_id'], 'take_id': take, 'role': role,
            'art_layer_id': art['id'], 'source_word': copy.deepcopy(w) if i else None,
            'start_basis': 'source_word_nearest_output_frame' if i else 'selected_source_art_start',
            'source_sha256': art['source_asset_binding']['source_sha256']}
        numbers.append(e)
        geometries.append({'id': e['id'], 'calibration_natural_width': calibration.width,
                           'scale_x': e['scale_x'], 'actual_ink_size': list(actual.size)})
    q['layers'].extend(numbers)
    record = {'event_id': d['event_id'], 'profile': PROFILE, 'superseded_layer': copy.deepcopy(old),
              'restored_caption_id': old['id'], 'restored_caption_text': caption,
              'added_layer_ids': [e['id'] for e in numbers], 'geometries': geometries,
              'second_start_frame': second_frame, 'second_start_seconds': second_time,
              'audio_unchanged': q['audio'] == p['audio'], 'clips_unchanged': q['clips'] == p['clips'],
              'reason': d['reason'], 'evidence': d['evidence'], 'direct_listening_seconds': 0}
    return q, record


def apply(project, decision, output):
    project, decision, output = [Path(x).absolute() for x in (project, decision, output)]
    require(output.suffix == '.json' and output.parent.resolve() == project.parent.resolve(), 'New JSON beside input required')
    require(not os.path.lexists(output), 'Output exists')
    p, ph = read(project); d, dh = read(decision)
    require(ph == d['project_sha256'], 'Input project SHA mismatch')
    validate(p, project.parent, True); q, record = transform(p, project.parent, d)
    q['source_number_contrast'] = {'schema': SCHEMA, 'input_project': relative(project, output.parent),
        'input_sha256': ph, 'decision': relative(decision, output.parent), 'decision_sha256': dh,
        'implementation_sha256': sha(Path(__file__)), 'record': record, 'release_ready': False}
    q['release_ready'] = False; validate(q, output.parent, True)
    require(sha(project) == ph and sha(decision) == dh, 'Inputs changed during conversion')
    with output.open('x', encoding='utf-8') as f:
        json.dump(q, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')
    return {'project': str(output), 'sha256': sha(output), 'layers': len(q['layers']),
            'second_start_frame': record['second_start_frame'], 'audio_unchanged': True, 'release_ready': False}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'decision', 'output'): ap.add_argument('--'+name, required=True)
    a = ap.parse_args(); print(json.dumps(apply(a.project, a.decision, a.output), ensure_ascii=False))
