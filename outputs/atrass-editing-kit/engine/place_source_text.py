#!/usr/bin/env python3
"""Move one static text layer away from an explicitly reviewed protected region."""
from pathlib import Path
import copy
import json
import math
import os
from PIL import Image
from apply_source_assets import read, sha, keys, require, relative
from render import Composer, validate

SCHEMA = 'atrass-protected-text-placement-v1'


def apply(project, decision, output):
    project, decision, output = [Path(p).absolute() for p in (project, decision, output)]
    require(output.parent.resolve() == project.parent.resolve() and output.suffix == '.json' and not os.path.lexists(output), 'Choose a new adjacent JSON')
    p, ph = read(project); d, dh = read(decision)
    fields = ('schema', 'project_sha256', 'layer_id', 'old_position', 'new_position', 'protected_region', 'evidence')
    keys(d, fields, fields, 'Protected text placement')
    require(d['schema'] == SCHEMA and d['project_sha256'] == ph, 'Placement schema/project identity mismatch')
    require(isinstance(d['evidence'], str) and d['evidence'].strip(), 'Reviewed region evidence required')
    validate(p, project.parent, True)
    selected = [e for e in p['layers'] if e['id'] == d['layer_id']]
    require(len(selected) == 1, 'One layer required')
    e = selected[0]
    require(e['kind'] == 'text' and e.get('position') == d['old_position'], 'Expected static text position changed')
    require(not any(k.endswith('_keys') or k in ('character_reveal', 'fade_in', 'fade_out') for k in e), 'Animated text needs temporal clearance review')
    for key, count in [('new_position', 2), ('protected_region', 4)]:
        require(isinstance(d[key], list) and len(d[key]) == count and all(type(v) in (int, float) and math.isfinite(v) for v in d[key]), 'Invalid coordinates')
    x0, y0, x1, y1 = d['protected_region']
    require(0 <= x0 < x1 <= p['width'] and 0 <= y0 < y1 <= p['height'], 'Protected region outside canvas')
    q = copy.deepcopy(p)
    moved = next(v for v in q['layers'] if v['id'] == e['id'])
    moved['position'] = d['new_position']
    image, xy = Composer(q, project.parent).image(moved, moved['in'])
    box = image.getbbox()
    require(box and 0 <= xy[0]+box[0] < xy[0]+box[2] <= p['width'] and 0 <= xy[1]+box[1] < xy[1]+box[3] <= p['height'], 'Moved text is clipped')
    canvas = Image.new('L', (p['width'], p['height']))
    canvas.paste(image.getchannel('A'), xy)
    protected = canvas.crop((math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)))
    require(protected.getextrema()[1] <= 16, 'Moved text still intersects the protected region')
    q['source_protected_text_placement'] = {'schema': SCHEMA, 'input_project': relative(project, output.parent),
        'input_sha256': ph, 'decision': relative(decision, output.parent), 'decision_sha256': dh,
        'implementation_sha256': sha(Path(__file__)), 'layer_id': e['id'], 'old_position': d['old_position'],
        'new_position': d['new_position'], 'protected_region': d['protected_region'], 'evidence': d['evidence'],
        'protected_region_alpha_max': protected.getextrema()[1], 'automatic_face_detection': False,
        'release_ready': False}
    q['release_ready'] = False
    validate(q, output.parent, True)
    require(sha(project) == ph and sha(decision) == dh, 'Inputs changed during placement')
    with output.open('x') as stream:
        json.dump(q, stream, ensure_ascii=False, indent=2); stream.write('\n')
    return {'project': str(output), 'sha256': sha(output), 'protected_region_alpha_max': protected.getextrema()[1], 'release_ready': False}
