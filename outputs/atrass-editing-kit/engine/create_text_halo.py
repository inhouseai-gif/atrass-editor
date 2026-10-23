#!/usr/bin/env python3
"""Add padded, separate halo layers for static text without editing its glyphs.

The output must be a new project beside the input. An absent asset directory is
created exclusively. Geometry animation, character reveal and text backplates
are explicitly unsupported. Existing parent opacity/fades are copied to the
halo, while the parent JSON and all existing media/layers remain unchanged.
This is a candidate effect generator, not confirmation of an original AE filter.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import unicodedata

import cv2
import numpy as np
from PIL import Image
from fontTools.ttLib import TTFont

from render import Composer, validate

SCHEMA = 'atrass-text-halo-generator-v1'
UNSUPPORTED = ('character_reveal', 'reveal_keys', 'rotation_keys', 'scale_keys',
               'x_keys', 'y_keys')
TEMPORAL = ('opacity', 'opacity_keys', 'fade_in', 'fade_out')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def relative(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def verify_inputs(project: Path, report: dict, asset_dir: Path) -> None:
    """Optimistically reject changed inputs at build and publication boundaries."""
    if digest(project) != report['source_project_sha256']:
        raise ValueError('Source project changed during halo generation')
    for item in report['font_bindings']:
        if digest((asset_dir / item['path']).resolve()) != item['sha256']:
            raise ValueError('Selected font changed during halo generation')


def check_parent(layer: dict) -> None:
    if layer.get('kind') not in ('text', 'label', 'caption'):
        raise ValueError('Halo parent must be a text, label or caption layer')
    if any(key in layer for key in UNSUPPORTED):
        raise ValueError('Animated geometry/reveal is unsupported; no flattening was performed')
    if layer.get('rotation', 0) != 0 or layer.get('scale', 1) != 1:
        raise ValueError('Rotated/scaled text is unsupported')
    if layer.get('background') or layer.get('shadow'):
        raise ValueError('Existing shadow/backplate is unsupported; separate it first')
    text = layer.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 2048:
        raise ValueError('Expected visible text of at most2048codepoints')
    if text != unicodedata.normalize('NFC', text):
        raise ValueError('Text must already be NFC; the input is never rewritten')
    if any(unicodedata.category(ch).startswith('C') and ch != '\n' for ch in text):
        raise ValueError('Unsupported control character in text')
    if any(not line.strip() for line in text.split('\n')):
        raise ValueError('Blank text lines are unsupported')
    if layer.get('anchor') not in (None, 'center', 'top_center'):
        raise ValueError('Unsupported anchor')
    if not finite(layer.get('z', 0)):
        raise ValueError('Nonfinite layer z')
    for key in ('opacity', 'fade_in', 'fade_out'):
        if key in layer and (not finite(layer[key]) or layer[key] < 0):
            raise ValueError('Invalid temporal property ' + key)
    if layer.get('opacity', 1) > 1:
        raise ValueError('Opacity must not exceed1')
    if 'opacity_keys' in layer:
        keys = layer['opacity_keys']
        if not isinstance(keys, list) or not keys:
            raise ValueError('Opacity keys must be nonempty')
        previous = -math.inf
        for key in keys:
            if not isinstance(key, list) or len(key) not in (2, 3):
                raise ValueError('Invalid opacity key')
            if not finite(key[0]) or key[0] < 0 or key[0] <= previous:
                raise ValueError('Opacity keys must have increasing local times')
            if not finite(key[1]) or not 0 <= key[1] <= 1:
                raise ValueError('Invalid opacity key value')
            if len(key) == 3 and key[2] not in ('hold', 'linear', 'smoothstep'):
                raise ValueError('Unsupported opacity interpolation')
            previous = key[0]


def padded_halo(mask: Image.Image, sigma: float, opacity: float,
                dx: int, dy: int, padding: int) -> Image.Image:
    """Blur after padding, with a zero boundary; never blur a tight glyph box."""
    base = np.zeros((mask.height + 2 * padding, mask.width + 2 * padding), np.float32)
    x, y = padding + dx, padding + dy
    base[y:y + mask.height, x:x + mask.width] = np.asarray(mask, np.float32) / 255
    soft = cv2.GaussianBlur(base, (0, 0), sigmaX=sigma, sigmaY=sigma,
                            borderType=cv2.BORDER_CONSTANT)
    alpha = np.rint(np.clip(soft * opacity * 255, 0, 255)).astype(np.uint8)
    rgba = np.zeros((*alpha.shape, 4), np.uint8)
    rgba[:, :, 3] = alpha
    return Image.fromarray(rgba)


def build(project: Path, layer_ids: list[str], output: Path, asset_dir: Path,
          *, sigma: float = 9, opacity: float = .55,
          dx: int = 0, dy: int = 0) -> tuple[dict, list[tuple[str, bytes]], dict]:
    """Validate and prepare all bytes in memory. No filesystem writes."""
    project, output, asset_dir = (Path(x).absolute() for x in (project, output, asset_dir))
    if output.suffix.lower() != '.json' or output.parent.resolve() != project.parent.resolve():
        raise ValueError('Use a new .json file beside the input project to preserve all path bases')
    if os.path.lexists(output) or os.path.lexists(asset_dir):
        raise FileExistsError('Output project and asset directory must both be absent')
    if not finite(sigma) or not 0 < sigma <= 64 or not finite(opacity) or not 0 < opacity <= 1:
        raise ValueError('Require0<sigma<=64 and0<opacity<=1')
    if any(type(v) is not int or abs(v) > 256 for v in (dx, dy)):
        raise ValueError('Offsets must be integers within256pixels')
    if not layer_ids or len(set(layer_ids)) != len(layer_ids):
        raise ValueError('Select one or more distinct layer IDs')
    source_bytes = project.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    p = json.loads(source_bytes)
    validate(p, project.parent, True)
    if max(p['width'], p['height']) > 8192:
        raise ValueError('Output canvas exceeds supported8192pixel dimension')
    ids = [e.get('id') for e in p['layers']]
    if len(set(ids)) != len(ids):
        raise ValueError('Input layer IDs must be unique')
    if any(key not in ids for key in layer_ids):
        raise ValueError('Selected layer ID not found')
    if asset_dir.resolve() == project.parent.resolve() or asset_dir.resolve().is_relative_to(output.resolve()):
        raise ValueError('Invalid asset directory')
    result = copy.deepcopy(p)
    result['release_ready'] = False
    comp = Composer(p, project.parent)
    padding = math.ceil(6 * sigma + max(abs(dx), abs(dy))) + 2
    artifacts, generated, records, font_bindings = [], {}, [], {}
    for number, ident in enumerate(layer_ids, 1):
        parent = next(e for e in p['layers'] if e['id'] == ident)
        check_parent(parent)
        if not isinstance(p.get('fonts', {}).get(parent.get('font')), dict):
            raise ValueError('Selected text font is not defined')
        font = p['fonts'][parent['font']]
        font_path = (project.parent / font['path']).resolve()
        font_sha = digest(font_path)
        if font_path in font_bindings and font_bindings[font_path] != font_sha:
            raise ValueError('Selected font changed during halo generation')
        font_bindings[font_path] = font_sha
        if font.get('sha256') and font['sha256'] != font_sha:
            raise ValueError('Selected font SHA differs from the declared file')
        with TTFont(font_path, fontNumber=font.get('index', 0), lazy=True) as face:
            cmap = face.getBestCmap() or {}
            missing = sorted({ch for ch in parent['text'] if ch != '\n' and ord(ch) not in cmap})
            if missing:
                raise ValueError('Selected font has no glyph for: ' + ', '.join(f'U+{ord(ch):04X}' for ch in missing))
        if not finite(parent.get('font_size', 64)) or not 1 <= parent.get('font_size', 64) <= 1024:
            raise ValueError('Unsupported font size')
        font_face = comp.font(parent)
        for line in parent['text'].split('\n'):
            width = font_face.getlength(line) + max(0, len(line) - 1) * parent.get('tracking', 0)
            if not finite(width) or not 0 < width <= 8192:
                raise ValueError('Natural text width exceeds supported range')
        for key in ('glyph_width', 'glyph_height'):
            if key in parent and (not finite(parent[key]) or not 0 < parent[key] <= 8192):
                raise ValueError('Unsupported glyph dimensions')
        if 'box' in parent:
            box = parent['box']
            if not isinstance(box, list) or len(box) != 4 or not all(finite(v) for v in box) or not 0 < box[2]-box[0] <= 8192 or not 0 < box[3]-box[1] <= 8192:
                raise ValueError('Unsupported text box')
        # Composer owns text layout, glyph resize, multiline alignment and box
        # positioning. Remove only opacity from this measurement, then copy it
        # unchanged onto the independent shadow layer below.
        measurement = copy.deepcopy(parent)
        for key in TEMPORAL:
            measurement.pop(key, None)
        measurement['fill'] = '#FFFFFF'
        glyph, xy = comp.image(measurement, parent['in'])
        if glyph.width > 8192 or glyph.height > 8192:
            raise ValueError('Text canvas exceeds supported8192pixel dimension')
        mask = glyph.getchannel('A')
        if not mask.getbbox():
            raise ValueError('Text layer contains no visible glyphs')
        image = padded_halo(mask, sigma, opacity, dx, dy, padding)
        alpha = np.asarray(image)[:, :, 3]
        if np.any(alpha[0]) or np.any(alpha[-1]) or np.any(alpha[:, 0]) or np.any(alpha[:, -1]):
            raise ValueError('Halo would reach its asset boundary')
        halo_xy = [xy[0] - padding, xy[1] - padding]
        bounds = image.getbbox()
        if bounds is None:
            raise ValueError('Halo rounded entirely to transparent')
        world = [bounds[0] + halo_xy[0], bounds[1] + halo_xy[1],
                 bounds[2] + halo_xy[0], bounds[3] + halo_xy[1]]
        if not (0 <= world[0] < world[2] <= p['width'] and 0 <= world[1] < world[3] <= p['height']):
            raise ValueError('Nontransparent halo would be clipped by the output frame')
        resource, halo_id = f'text-halo-{number:03}', ident + '-halo'
        if resource in result['media'] or halo_id in ids:
            raise ValueError('Generated resource/layer ID collision')
        filename = resource + '.png'
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        data = stream.getvalue()
        artifacts.append((filename, data))
        result['media'][resource] = {'type': 'image', 'role': 'generated_text_shadow_candidate',
            'path': relative(asset_dir / filename, output.parent),
            'sha256': hashlib.sha256(data).hexdigest()}
        layer = {'id': halo_id, 'kind': 'image', 'source': resource,
                 'in': parent['in'], 'out': parent['out'], 'position': halo_xy,
                 'z': parent.get('z', 0), 'status': 'generated_candidate_requires_visual_review',
                 'source_layer_id': ident}
        for key in TEMPORAL:
            if key in parent:
                layer[key] = copy.deepcopy(parent[key])
        generated[ident] = layer
        # Expanding again must not change pixels inside the first result.
        larger = padded_halo(mask, sigma, opacity, dx, dy, padding * 2)
        inset = larger.crop((padding, padding, padding + image.width, padding + image.height))
        if image.tobytes() != inset.tobytes():
            raise ValueError('Padding expansion changed the halo; cannot certify containment')
        records.append({'parent_id': ident, 'halo_id': halo_id, 'asset': filename,
            'sha256': hashlib.sha256(data).hexdigest(), 'font_sha256': font_sha,
            'glyph_canvas': list(glyph.size), 'glyph_top_left': list(xy),
            'halo_position': halo_xy, 'halo_alpha_world_bbox_exclusive': world,
            'padding_px': padding, 'expanded_padding_identical': True, 'edge_alpha_max': 0})
    result['layers'] = []
    for layer in p['layers']:
        if layer['id'] in generated:
            result['layers'].append(generated[layer['id']])
        result['layers'].append(copy.deepcopy(layer))
    report = {'schema': SCHEMA, 'source_project': relative(project, asset_dir),
        'source_project_sha256': source_sha, 'renderer_sha256': digest(Path(__file__).with_name('render.py')),
        'generator_sha256': digest(Path(__file__)),
        'font_bindings': [{'path': relative(path, asset_dir), 'sha256': sha}
                          for path, sha in font_bindings.items()],
        'input_consistency_policy': 'Parse/hash the same project bytes; optimistic project/font hash checks after build and immediately before publication. This does not lock externally writable files.',
        'effect_candidate': {'black': '#000000', 'gaussian_sigma_px': sigma,
                             'opacity': opacity, 'offset_px': [dx, dy], 'padding_px': padding},
        'layer_records': records, 'original_layers_unchanged': True,
        'z_policy': 'Insert immediately before parent at the same z; Composer stable sort retains all original order.',
        'scope': 'Static text geometry; optional constant opacity, opacity keys and fades. No geometry animation or reveal.',
        'new_copy_supported_by_regenerating_assets': True, 'original_effect_settings_confirmed': False,
        'actual_render_completed': False, 'style_match_approved': False, 'release_ready': False}
    result.setdefault('provenance', {})['text_halo_generation'] = {
        'path': relative(asset_dir / 'provenance.json', output.parent), 'schema': SCHEMA}
    verify_inputs(project, report, asset_dir)
    return result, artifacts, report


def create(project: Path, layer_ids: list[str], output: Path, asset_dir: Path, **kwargs) -> dict:
    result, artifacts, report = build(project, layer_ids, output, asset_dir, **kwargs)
    return publish(project, output, asset_dir, result, artifacts, report)


def publish(project: Path, output: Path, asset_dir: Path, result: dict,
            artifacts: list[tuple[str, bytes]], report: dict) -> dict:
    """Publish validated generated effects without replacing existing paths.

    Shared by the bounded text-halo and rectangle-shadow builders. This helper
    is internal; callers must finish their geometry and input checks first.
    """
    output, asset_dir = Path(output).absolute(), Path(asset_dir).absolute()
    project_bytes = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    report_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    verify_inputs(Path(project).absolute(), report, asset_dir)
    asset_dir.mkdir(parents=False, exist_ok=False)
    owned, temporary, published = [], None, False
    try:
        for filename, data in artifacts:
            target = asset_dir / filename
            with target.open('xb') as f:
                owned.append(target)
                f.write(data)
        target = asset_dir / 'provenance.json'
        with target.open('xb') as f:
            owned.append(target)
            f.write(report_bytes)
        validate(result, output.parent, True)
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.halo-project-', suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            f.write(project_bytes)
            f.flush()
            os.fsync(f.fileno())
        verify_inputs(Path(project).absolute(), report, asset_dir)
        os.link(temporary, output)  # Atomic publication; never replaces a file.
        published = True
    except Exception:
        if not published:
            for target in reversed(owned):
                target.unlink()
            asset_dir.rmdir()
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'project': str(output), 'project_sha256': hashlib.sha256(project_bytes).hexdigest(),
            'assets': str(asset_dir), 'added_layers': len(artifacts), 'release_ready': False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--layer-id', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--asset-dir', type=Path, required=True)
    parser.add_argument('--sigma', type=float, default=9)
    parser.add_argument('--opacity', type=float, default=.55)
    parser.add_argument('--dx', type=int, default=0)
    parser.add_argument('--dy', type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(create(args.project, args.layer_id, args.output, args.asset_dir,
                           sigma=args.sigma, opacity=args.opacity, dx=args.dx, dy=args.dy), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
