#!/usr/bin/env python3
"""Create a silent, editable chapter card for NEW text; never overwrite a file.

python engine/create_chapter.py --title '마음이 닿는 순간' --number 2 \
    --duration 3.5 --output projects/my-chapter.json
python engine/render.py projects/my-chapter.json --draft --output chapter.mp4

Keep the generated JSON and the kit's assets in the same relative directory
structure when transferring. Fonts and generated background are candidates,
not identified original artwork. This CLI does not render or count raw footage.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import unicodedata

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

KIT = Path(__file__).resolve().parents[1]
VERSION = 'atrass-chapter-template-v1'
SAFE_MARGIN_X = 96
MIN_TITLE_HEIGHT = 48
MAX_TITLE_CODEPOINTS = 512


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(kit: Path, path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(kit.resolve()) or not path.is_file():
        raise ValueError('Template dependency is missing or outside this kit: ' + path.name)
    return path


def _relative(path: Path, root: Path) -> str:
    # macOS /var and /tmp are symlinks; mixing lexical and resolved parents
    # produces incorrect ../../ references when a project lives there.
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def _font(path: Path, size: int, weight: int, text: str):
    with TTFont(path, lazy=True) as face:
        cmap = face.getBestCmap() or {}
        missing = sorted({ch for ch in text if ord(ch) not in cmap})
        if missing:
            raise ValueError('Candidate font has no verified glyph for: ' + ', '.join(f'U+{ord(ch):04X}' for ch in missing))
        axes = face['fvar'].axes if 'fvar' in face else []
        if len(axes) != 1 or axes[0].axisTag != 'wght' or not axes[0].minValue <= weight <= axes[0].maxValue:
            raise ValueError('Template requires its packaged single-weight-axis candidate font')
    font = ImageFont.truetype(str(path), size)
    font.set_variation_by_axes([weight])
    return font


def natural_mask(text: str, font, tracking: float) -> Image.Image:
    """Mirror the renderer's unscaled text mask, including its word spacing.

    No measured reference width is applied. Resizing in the render project uses
    glyph_height alone, which applies one uniform scale to glyphs AND spaces.
    """
    box = font.getbbox(text)
    width = max(1, math.ceil(font.getlength(text) + max(0, len(text) - 1) * tracking + 12))
    mask = Image.new('L', (width, max(1, box[3] - box[1] + 12)))
    draw = ImageDraw.Draw(mask)
    if tracking:
        x = 4.0
        for ch in text:
            draw.text((x, 4 - box[1]), ch, font=font, fill=255)
            x += font.getlength(ch) + tracking
    else:
        draw.text((4, 4 - box[1]), text, font=font, fill=255)
    ink = mask.getbbox()
    if ink is None:
        raise ValueError('The title contains no visible glyphs')
    # Guard the allocation itself; cropping must not hide a clipped glyph.
    if ink[0] == 0 or ink[1] == 0 or ink[2] == mask.width or ink[3] == mask.height:
        raise ValueError('Candidate font ink exceeds the unscaled text canvas')
    return mask.crop(ink)


def _measure(text, layer, font_path, height):
    font = _font(font_path, int(layer['font_size']), int(layer['weight']), text)
    mask = natural_mask(text, font, layer.get('tracking', 0))
    scale = height / mask.height
    return mask, {
        'natural_mask_size': list(mask.size),
        'glyph_size': [max(1, round(mask.width * scale)), height],
        'uniform_scale': scale,
        'space_advance_px': round(font.getlength(' ') * scale, 8),
        'tracking_px': round(layer.get('tracking', 0) * scale, 8),
    }


def make_project(title: str, number: int, duration: str, output: Path, *, kit: Path = KIT) -> dict:
    """Validate and return a project without writing any file."""
    if not isinstance(title, str) or not title or title != title.strip():
        raise ValueError('Use a nonempty title without leading or trailing whitespace')
    if len(title) > MAX_TITLE_CODEPOINTS or any(unicodedata.category(ch).startswith('C') or ch in '\n\r\t' for ch in title):
        raise ValueError('Title must be one visible line of at most 512 codepoints; controls are unsupported')
    if any(ch.isspace() and ch != ' ' for ch in title):
        raise ValueError('Only ordinary spaces are supported in a one-line title')
    normalized = unicodedata.normalize('NFC', title)
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 9999:
        raise ValueError('Chapter number must be an integer from 1 to 9999')
    try:
        requested = Fraction(str(duration))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError('Duration must be a finite positive number of seconds') from exc
    if not 0 < requested <= 60:
        raise ValueError('A chapter card duration must be greater than 0 and at most 60 seconds')
    output = Path(output).absolute()
    if output.suffix.lower() != '.json':
        raise ValueError('Output must be a new .json project path')
    if os.path.lexists(output):
        raise FileExistsError('Refusing to overwrite existing output: ' + str(output))

    pilot_path = _inside(kit, kit / 'projects/A-chapter-pilot-procedural-v1.json')
    pilot = json.loads(pilot_path.read_text(encoding='utf-8'))
    if (pilot['width'], pilot['height'], pilot['fps']) != (1920, 1080, '60000/1001'):
        raise ValueError('Unexpected chapter pilot canvas or timebase')
    layers = {layer['id']: layer for layer in pilot['layers']}
    title_style, heading_style, backing = (layers[key] for key in (
        'chapter1-title', 'chapter1-small_heading', 'chapter1-white-title-box'))
    bx, by = backing['position']; bw, bh = backing['size']
    tx, ty = title_style['position']
    refw, refh = title_style['glyph_width'], title_style['glyph_height']
    padding = [tx - bx, ty - by, bx + bw - tx - refw, by + bh - ty - refh]
    if padding != [24, 25, 24, 24] or refh != 76 or heading_style['glyph_height'] != 32:
        raise ValueError('Pilot typography geometry changed; review the template policy before reuse')
    gap = by - (heading_style['position'][1] + heading_style['glyph_height'])
    center_y = by + bh / 2
    fps = Fraction(pilot['fps'])
    frames = math.ceil(requested * fps)
    actual_duration = float(Fraction(frames, 1) / fps)

    fonts, dependencies, font_paths = {}, [], {}
    for role in (title_style['font'], heading_style['font']):
        entry = pilot['fonts'][role]
        path = _inside(kit, pilot_path.parent / entry['path'])
        font_paths[role] = path
        fonts[role] = {'path': _relative(path, output.parent), 'sha256': sha256(path),
                       'status': 'candidate_not_original_confirmed'}
        dependencies.append({'kind': 'font', **fonts[role]})

    natural, title_metrics = _measure(normalized, title_style, font_paths[title_style['font']], refh)
    max_width = pilot['width'] - 2 * SAFE_MARGIN_X - padding[0] - padding[2]
    title_height = refh
    while title_metrics['glyph_size'][0] > max_width and title_height >= MIN_TITLE_HEIGHT:
        title_height -= 1
        scale = title_height / natural.height
        title_metrics['glyph_size'] = [max(1, round(natural.width * scale)), title_height]
        title_metrics['uniform_scale'] = scale
    if title_height < MIN_TITLE_HEIGHT:
        raise ValueError(f'Title is too long: fitting inside the safe area would require glyph height below {MIN_TITLE_HEIGHT}px. Shorten the title; no clipping or forced horizontal squeeze was applied.')
    # Recompute spacing after uniform downscaling, without changing the text.
    _, title_metrics = _measure(normalized, title_style, font_paths[title_style['font']], title_height)
    heading = f'CHAPTER {number}'
    _, head_metrics = _measure(heading, heading_style, font_paths[heading_style['font']], heading_style['glyph_height'])
    if head_metrics['glyph_size'][0] > pilot['width'] - 2 * SAFE_MARGIN_X:
        raise ValueError('Chapter heading exceeds the safe area')
    tw, th = title_metrics['glyph_size']
    boxw, boxh = tw + padding[0] + padding[2], th + padding[1] + padding[3]
    boxx = math.floor((pilot['width'] - boxw) / 2 + .5)
    boxy = math.floor(center_y - boxh / 2 + .5)
    title_position = [boxx + padding[0], boxy + padding[1]]
    hw, hh = head_metrics['glyph_size']
    heading_position = [math.floor((pilot['width'] - hw) / 2 + .5), boxy - gap - hh]
    if not (boxx >= SAFE_MARGIN_X and boxx + boxw <= pilot['width'] - SAFE_MARGIN_X and heading_position[1] >= 0 and boxy + boxh <= pilot['height']):
        raise ValueError('Computed chapter geometry leaves the safe canvas')

    media = {}
    for role in ('procedural-gradient', 'procedural-grain'):
        entry = pilot['media'][role]
        path = _inside(kit, pilot_path.parent / entry['path'])
        if entry.get('role') != 'procedurally_created_graphic_asset':
            raise ValueError('Chapter background must be procedurally created, not finished footage')
        with Image.open(path) as image:
            if image.size != (1920, 1080):
                raise ValueError('Unexpected generated background canvas')
            image.verify()
        media[role] = {**entry, 'path': _relative(path, output.parent), 'sha256': sha256(path)}
        dependencies.append({'kind': 'image', 'path': media[role]['path'], 'sha256': media[role]['sha256']})

    common = {'in': 0, 'out': actual_duration, 'status': 'new_text_template_candidate_pending_review'}
    def text_layer(identifier, text, style, position, height, z):
        return {**common, 'id': identifier, 'kind': 'text', 'text': text, 'position': position,
                'fill': style['fill'], 'font': style['font'], 'font_size': style['font_size'],
                'weight': style['weight'], 'tracking': style.get('tracking', 0),
                'glyph_height': height, 'z': z}
    provenance = _inside(kit, kit / 'assets/chapter-pilot/provenance.json')
    return {
        'schema': 'atrass-render-v1', 'title': f'챕터 {number} — {normalized}', 'variant': 'A',
        'width': 1920, 'height': 1080, 'fps': pilot['fps'], 'duration': actual_duration,
        'release_ready': False, 'media': media, 'fonts': fonts,
        'clips': [{**common, 'id': 'chapter-generated-gradient', 'source': 'procedural-gradient',
                   'source_in': 0, 'geometry': {'scale': 1}}],
        'layers': [
            {**common, 'id': 'chapter-generated-grain', 'kind': 'image', 'source': 'procedural-grain', 'position': [0, 0], 'z': 10},
            {**common, 'id': 'chapter-title-box', 'kind': 'rect', 'position': [boxx, boxy], 'size': [boxw, boxh], 'fill': backing['fill'], 'z': 20},
            text_layer('chapter-heading', heading, heading_style, heading_position, hh, 30),
            text_layer('chapter-title', normalized, title_style, title_position, th, 40),
        ],
        'audio': [],
        'chapter_template': {
            'version': VERSION, 'input_title': title, 'rendered_title': normalized,
            'unicode_normalization': 'NFC; input is preserved separately', 'number': number,
            'requested_duration_seconds': float(requested), 'frame_count': frames,
            'duration_policy': 'ceil to whole 60000/1001 frames; duration never shorter than requested',
            'base_media_class': 'procedural_graphics', 'raw_reconstruction_frames': 0,
            'manual_visual_review': 'pending_for_this_new_project',
            'source_pilot': {'path': _relative(pilot_path, output.parent), 'sha256': sha256(pilot_path)},
            'background_provenance': {'path': _relative(provenance, output.parent), 'sha256': sha256(provenance)},
            'dependencies': dependencies,
            'layout_policy': {'safe_x': [SAFE_MARGIN_X, 1920 - SAFE_MARGIN_X], 'title_base_glyph_height': refh,
                'title_min_glyph_height': MIN_TITLE_HEIGHT, 'padding_ltrb': padding,
                'backing_center_y': center_y, 'heading_to_box_gap': gap,
                'minimum_size_is_new_readability_policy_not_observed_original': True,
                'horizontal_stretch': False, 'line_wrap': False,
                'description': 'Natural candidate glyph ratio and spaces; only uniform height-based shrink, constant backing padding, horizontally centered independent layers.'},
            'title_measurement': {**title_metrics, 'glyph_bbox': [*title_position, title_position[0] + tw, title_position[1] + th]},
            'heading_measurement': {**head_metrics, 'glyph_bbox': [*heading_position, heading_position[0] + hw, heading_position[1] + hh]},
            'box_bbox': [boxx, boxy, boxx + boxw, boxy + boxh],
        },
        'limitations': [
            'Silent procedural graphic; no source MOV and no reference video/audio is rendered. Raw-footage reconstruction count is zero.',
            'Fonts are candidates. New text preserves candidate glyph aspect and spacing; the old 941×76 measured phrase bbox is never imposed on new text.',
            'Generated gradient/static grain approximate the reference; original artwork, font identity, authored RGBA and texture settings remain unknown.',
            '48px minimum title ink height and 96px side safety margin are explicit template policies, not verified original author settings.',
            'Independent static layers hold for the requested duration. Automatic semantic chapter selection and full long-form editing are not implemented by this CLI.',
            'Transfer the JSON and kit assets with their relative directory structure; copying this JSON alone does not embed dependencies.',
        ],
    }


def create_project(title: str, number: int, duration: str, output: Path, *, kit: Path = KIT) -> dict:
    """Publish a complete JSON atomically, using an exclusive hard-link commit."""
    output = Path(output).absolute()
    project = make_project(title, number, duration, output, kit=kit)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=output.parent, prefix='.' + output.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(project, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        # Unlike replace(), link() fails if a competing writer created output.
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--title', required=True)
    parser.add_argument('--number', required=True, type=int)
    parser.add_argument('--duration', required=True, help='Positive seconds; at most 60')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        project = create_project(args.title, args.number, args.duration, args.output)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f'Chapter creation refused: {exc}\n')
    print(json.dumps({'project': str(args.output), 'frames': project['chapter_template']['frame_count'],
                      'duration': project['duration'], 'title_glyph_size': project['chapter_template']['title_measurement']['glyph_size'],
                      'review': 'pending', 'release_ready': False, 'raw_reconstruction_frames': 0}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
