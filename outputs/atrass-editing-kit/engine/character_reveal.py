"""Fixed-layout, per-codepoint opacity reveal for Pillow text layers.

Observed B quote cards reveal their glyphs in roughly 0.8 seconds. This module
implements a reproducible candidate, not the original font, AE preset or curve.
Keep attribution, logo, dialogue bar and backplate in separate layers.

    reveal = CharacterReveal('첫째 줄\n둘째 줄', font, align='center')
    image = reveal.render(local_seconds, timing=RevealTiming(
        duration=0.8, glyph_fade_duration=1 / 30))

All positions and the canvas are computed from the COMPLETE text once. A reveal
never measures or recenters a partial substring. Spaces reserve horizontal space
but consume no reveal slot; newlines reserve lines but consume no reveal slot.
The unit is a Python Unicode codepoint (precomposed Korean syllables recommended),
not a grapheme cluster. No implicit Unicode normalization or word wrapping occurs.
Tabs are rejected because their width must be chosen explicitly by the caller.
The layout uses individual glyph advances, without pair kerning/ligatures. This
is suitable for the observed Korean quote; it is not a general shaping engine.

Use static_image() as this layer's final static text. Another renderer's whole-
string shaping/tight-cropping can differ, so do not swap implementations at the
end of a reveal. The caller may resize this entire fixed canvas consistently.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from PIL import Image, ImageColor, ImageDraw, ImageFont


def _finite(value: float, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    value = float(value)
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError(f'{name} must be finite' + (' and nonnegative' if nonnegative else ''))
    return value


def _color(value: str | Sequence[int]) -> tuple[int, int, int, int]:
    if isinstance(value, str):
        return ImageColor.getcolor(value, 'RGBA')
    result = tuple(value)
    if len(result) == 3:
        result += (255,)
    if len(result) != 4 or any(type(v) is not int or not 0 <= v <= 255 for v in result):
        raise ValueError('Color must contain three or four integers from 0 to 255')
    return result


@dataclass(frozen=True)
class RevealTiming:
    """Local seconds; duration includes the last glyph's fade.

    The first glyph begins at start; the last finishes at start + duration for
    2+ glyphs. A one-glyph string finishes after glyph_fade_duration. With zero
    fade, glyphs step on at their individual scheduled starts. duration=0 is an
    instantaneous reveal and requires glyph_fade_duration=0. Easing applies to
    each glyph's alpha, not its position. These values are adjustable candidates.
    """

    duration: float = 0.8
    glyph_fade_duration: float = 1 / 30
    start: float = 0.0
    easing: str = 'linear'

    def __post_init__(self) -> None:
        for name in ('duration', 'glyph_fade_duration', 'start'):
            _finite(getattr(self, name), name, nonnegative=True)
        if self.glyph_fade_duration > self.duration:
            raise ValueError('glyph_fade_duration cannot exceed duration')
        if self.easing not in ('linear', 'smoothstep'):
            raise ValueError('easing must be linear or smoothstep')

    def opacity(self, local_seconds: float, slot: int, count: int) -> float:
        t = _finite(local_seconds, 'local_seconds')
        if type(slot) is not int or type(count) is not int or not 0 <= slot < count:
            raise ValueError('slot must be an integer in [0, count)')
        if t >= self.start + self.duration:
            return 1.0
        step = (self.duration - self.glyph_fade_duration) / (count - 1) if count > 1 else 0.0
        elapsed = t - self.start - slot * step
        if elapsed < 0:
            return 0.0
        if self.glyph_fade_duration == 0:
            return 1.0
        alpha = min(1.0, max(0.0, elapsed / self.glyph_fade_duration))
        return alpha * alpha * (3 - 2 * alpha) if self.easing == 'smoothstep' else alpha


@dataclass(frozen=True)
class GlyphPlacement:
    """Final, immutable placement. source_index indexes the input string."""

    source_index: int
    character: str
    line_index: int
    reveal_slot: int | None
    origin: tuple[float, float]
    advance: float
    ink_box: tuple[int, int, int, int] | None


class CharacterReveal:
    """Pre-rasterized glyph layer returning fresh, fixed-size RGBA frames.

    font is caller-supplied; no fallback font or font-identity assertion is made.
    align aligns complete logical line widths. line_gap is additional baseline
    distance beyond ascent+descent. Blank lines and leading/trailing spaces stay.
    An all-whitespace string returns a transparent but stable logical canvas.
    stroke follows each glyph's alpha; fill/stroke color alpha is respected.
    """

    def __init__(self, text: str, font: ImageFont.FreeTypeFont, *,
                 fill: str | Sequence[int] = '#ffffff',
                 stroke_width: int = 0,
                 stroke_fill: str | Sequence[int] | None = None,
                 tracking: float = 0.0, line_gap: float = 18.0,
                 align: str = 'center', padding: int = 0) -> None:
        if not isinstance(text, str):
            raise ValueError('text must be a string')
        if not isinstance(font, ImageFont.FreeTypeFont):
            raise ValueError('font must be a caller-supplied Pillow FreeTypeFont')
        if any(ch in text for ch in ('\r', '\t')):
            raise ValueError('Expand tabs and convert CR/CRLF to LF explicitly')
        if any(ord(ch) < 32 and ch != '\n' for ch in text):
            raise ValueError('Control characters other than LF are not supported')
        if align not in ('left', 'center', 'right'):
            raise ValueError('align must be left, center or right')
        if type(padding) is not int or padding < 0:
            raise ValueError('padding must be a nonnegative integer')
        if type(stroke_width) is not int or stroke_width < 0:
            raise ValueError('stroke_width must be a nonnegative integer')
        tracking = _finite(tracking, 'tracking')
        line_gap = _finite(line_gap, 'line_gap', nonnegative=True)
        fill_rgba = _color(fill)
        stroke_rgba = fill_rgba if stroke_fill is None else _color(stroke_fill)
        self.text = text
        self.align = align
        self._sprites: list[tuple[int, Image.Image, tuple[int, int]]] = []
        ascent, descent = font.getmetrics()
        line_step = ascent + descent + line_gap
        lines = text.split('\n')
        advances = [[float(font.getlength(ch)) for ch in line] for line in lines]
        widths = [sum(a) + max(0, len(a) - 1) * tracking for a in advances]
        if any(width < 0 for width in widths):
            raise ValueError('tracking creates a negative logical line width')
        logical_width = max(widths, default=0.0)
        logical_height = ascent + descent + (len(lines) - 1) * line_step
        provisional = []
        min_x, min_y, max_x, max_y = 0.0, 0.0, logical_width, logical_height
        source_index = slot = 0
        for li, line in enumerate(lines):
            x = 0.0 if align == 'left' else (logical_width - widths[li]) * (0.5 if align == 'center' else 1.0)
            baseline = ascent + li * line_step
            for ci, ch in enumerate(line):
                advance = advances[li][ci]
                if ch.isspace():
                    provisional.append((source_index, ch, li, None, x, baseline, advance, None, None))
                else:
                    box = font.getbbox(ch, anchor='ls', stroke_width=stroke_width)
                    left, top, right, bottom = map(int, box)
                    sprite = Image.new('RGBA', (max(1, right-left), max(1, bottom-top)))
                    ImageDraw.Draw(sprite).text((-left, -top), ch, font=font, anchor='ls',
                        fill=fill_rgba, stroke_width=stroke_width, stroke_fill=stroke_rgba)
                    px, py = round(x + left), round(baseline + top)
                    ink = (px, py, px + sprite.width, py + sprite.height)
                    min_x, min_y = min(min_x, ink[0]), min(min_y, ink[1])
                    max_x, max_y = max(max_x, ink[2]), max(max_y, ink[3])
                    provisional.append((source_index, ch, li, slot, x, baseline, advance, ink, sprite))
                    slot += 1
                x += advance + tracking
                source_index += 1
            if li < len(lines)-1:
                provisional.append((source_index, '\n', li, None, x, baseline, 0.0, None, None))
                source_index += 1
        shift_x, shift_y = padding - math.floor(min_x), padding - math.floor(min_y)
        self.size = (max(1, math.ceil(max_x)-math.floor(min_x)+2*padding),
                     max(1, math.ceil(max_y)-math.floor(min_y)+2*padding))
        self.glyph_count = slot
        self.line_widths = tuple(widths)
        placements = []
        for index, ch, li, reveal_slot, x, y, advance, ink, sprite in provisional:
            shifted = None if ink is None else (ink[0]+shift_x, ink[1]+shift_y, ink[2]+shift_x, ink[3]+shift_y)
            placements.append(GlyphPlacement(index, ch, li, reveal_slot,
                (x+shift_x, y+shift_y), advance, shifted))
            if sprite is not None:
                self._sprites.append((reveal_slot, sprite, shifted[:2]))
        self.placements = tuple(placements)
        self._static = self._compose([1.0] * self.glyph_count)
        # Crop this SAME box on every frame if the caller needs a tight canvas.
        # Cropping each partial frame's getbbox() would destroy fixed placement.
        self.ink_bbox = self._static.getbbox()

    def _compose(self, opacities: Sequence[float]) -> Image.Image:
        out = Image.new('RGBA', self.size)
        for slot, sprite, position in self._sprites:
            opacity = opacities[slot]
            if opacity <= 0:
                continue
            if opacity >= 1:
                out.alpha_composite(sprite, position)
            else:
                faded = sprite.copy()
                faded.putalpha(sprite.getchannel('A').point([round(v*opacity) for v in range(256)]))
                out.alpha_composite(faded, position)
        return out

    def glyph_opacities(self, local_seconds: float, *, timing: RevealTiming | None = None) -> tuple[float, ...]:
        _finite(local_seconds, 'local_seconds')
        timing = timing or RevealTiming()
        return tuple(timing.opacity(local_seconds, slot, self.glyph_count) for slot in range(self.glyph_count))

    def render(self, local_seconds: float, *, timing: RevealTiming | None = None) -> Image.Image:
        """Return this text alone. No caption, logo, attribution or background."""
        opacities = self.glyph_opacities(local_seconds, timing=timing)
        if all(value >= 1 for value in opacities):
            return self._static.copy()
        return self._compose(opacities)

    def static_image(self) -> Image.Image:
        """Return the exact completed frame, with identical canvas and positions."""
        return self._static.copy()
