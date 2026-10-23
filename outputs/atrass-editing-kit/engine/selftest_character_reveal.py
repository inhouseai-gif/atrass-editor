#!/usr/bin/env python3
"""Local regressions for character_reveal; no media download or source mutation.

python engine/selftest_character_reveal.py --preview work/reveal-preview.png
The included Noto font is a test font, not a verified reference font.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import unittest

from PIL import Image, ImageChops, ImageDraw, ImageFont

from character_reveal import CharacterReveal, RevealTiming


class RevealTests(unittest.TestCase):
    font_path: Path

    @classmethod
    def setUpClass(cls):
        cls.font = ImageFont.truetype(str(cls.font_path), 48)

    def test_full_frame_matches_static_without_layout_switch(self):
        layer = CharacterReveal('마음은 이성이\n알지 못하는 이유', self.font, padding=7)
        static = layer.static_image()
        original_positions = layer.placements
        for t in [-1, 0, .01, .3, .79, .8, 1, 100]:
            self.assertEqual(layer.render(t).size, static.size)
            self.assertEqual(layer.placements, original_positions)
        for t in [.8, 1, 100]:
            self.assertEqual(layer.render(t).tobytes(), static.tobytes())

    def test_complete_korean_line_matches_independent_pillow_text(self):
        layer = CharacterReveal('가나다', self.font, align='left')
        expected = Image.new('RGBA', layer.size)
        ImageDraw.Draw(expected).text(layer.placements[0].origin, '가나다',
            font=self.font, anchor='ls', fill='white')
        self.assertEqual(layer.static_image().tobytes(), expected.tobytes())

    def test_partial_first_glyph_has_final_position(self):
        layer = CharacterReveal('가나다\n라마', self.font, align='center')
        first = layer.placements[0]
        timing = RevealTiming(duration=1, glyph_fade_duration=.1)
        expected = Image.new('RGBA', layer.size)
        ImageDraw.Draw(expected).text(first.origin, '가', font=self.font,
            anchor='ls', fill='white')
        self.assertEqual(layer.render(.1, timing=timing).tobytes(), expected.tobytes())

    def test_spaces_and_newlines_preserve_layout_but_not_timing_slots(self):
        compact = CharacterReveal('가나다', self.font)
        spaced = CharacterReveal(' 가  나\n\n다 ', self.font)
        self.assertEqual(spaced.glyph_count, 3)
        self.assertEqual([p.character for p in spaced.placements if p.reveal_slot is not None], list('가나다'))
        for t in [0, .02, .4, .78, .8]:
            self.assertEqual(compact.glyph_opacities(t), spaced.glyph_opacities(t))
        self.assertGreater(spaced.size[1], compact.size[1])
        self.assertEqual(''.join(p.character for p in spaced.placements), spaced.text)
        self.assertEqual([p.source_index for p in spaced.placements], list(range(len(spaced.text))))

    def test_monotonic_alpha_no_position_or_canvas_flicker(self):
        layer = CharacterReveal('고정 위치\n글자 등장', self.font, tracking=-2,
            stroke_width=2, stroke_fill='#000000', padding=4)
        previous = layer.render(-1).getchannel('A')
        for frame in range(61):
            current = layer.render(frame/60).getchannel('A')
            self.assertEqual(current.size, previous.size)
            self.assertIsNone(ImageChops.subtract(previous, current).getbbox())
            previous = current

    def test_one_glyph_opacity_is_an_actual_fade(self):
        layer = CharacterReveal('가', self.font, fill=(255, 255, 255, 128))
        timing = RevealTiming(duration=.8, glyph_fade_duration=.2)
        self.assertEqual(layer.render(-.1, timing=timing).getchannel('A').getextrema(), (0, 0))
        self.assertEqual(layer.render(0, timing=timing).getchannel('A').getextrema(), (0, 0))
        self.assertEqual(layer.render(.1, timing=timing).getchannel('A').getextrema()[1], 64)
        self.assertEqual(layer.render(.2, timing=timing).getchannel('A').getextrema()[1], 128)

    def test_delayed_sequence_and_last_fade_endpoint(self):
        timing = RevealTiming(duration=.8, glyph_fade_duration=.1, start=2)
        self.assertEqual(timing.opacity(1.99, 0, 3), 0)
        self.assertAlmostEqual(timing.opacity(2.05, 0, 3), .5)
        self.assertEqual(timing.opacity(2.35, 2, 3), 0)
        self.assertAlmostEqual(timing.opacity(2.75, 2, 3), .5)
        self.assertEqual(timing.opacity(2.8, 2, 3), 1)

    def test_explicit_zero_fade_and_instant_mode(self):
        layer = CharacterReveal('가나', self.font)
        step = RevealTiming(duration=.8, glyph_fade_duration=0)
        self.assertEqual(layer.glyph_opacities(0, timing=step), (1, 0))
        self.assertEqual(layer.glyph_opacities(.8, timing=step), (1, 1))
        instant = RevealTiming(duration=0, glyph_fade_duration=0)
        self.assertEqual(layer.render(0, timing=instant).tobytes(), layer.static_image().tobytes())
        self.assertIsNone(layer.render(-.01, timing=instant).getbbox())

    def test_smoothstep_candidate_does_not_change_schedule(self):
        timing = RevealTiming(duration=.8, glyph_fade_duration=.2, easing='smoothstep')
        self.assertAlmostEqual(timing.opacity(.05, 0, 1), .15625)
        self.assertEqual(timing.opacity(.2, 0, 1), 1)

    def test_returned_images_do_not_mutate_cached_static(self):
        layer = CharacterReveal('가나', self.font)
        expected = layer.static_image().tobytes()
        returned = layer.render(1)
        returned.paste((255, 0, 0, 255), (0, 0, *returned.size))
        self.assertEqual(layer.static_image().tobytes(), expected)

    def test_empty_and_whitespace_are_transparent_fixed_canvas(self):
        for text in ['', ' ', '\n', '  \n  ']:
            layer = CharacterReveal(text, self.font)
            self.assertEqual(layer.glyph_count, 0)
            self.assertEqual(layer.glyph_opacities(.5), ())
            self.assertIsNone(layer.render(.5).getbbox())
            self.assertEqual(layer.render(.5).size, layer.static_image().size)

    def test_codepoints_are_not_silently_normalized(self):
        text = '\u1100\u1161\u11a8'
        layer = CharacterReveal(text, self.font)
        self.assertEqual(layer.text, text)
        self.assertEqual(layer.glyph_count, 3)

    def test_validation_rejects_nonfinite_or_undefined_layout(self):
        for opts in [dict(duration=-1), dict(duration=float('nan')),
                     dict(glyph_fade_duration=2), dict(start=float('inf')),
                     dict(easing='unknown')]:
            with self.assertRaises(ValueError): RevealTiming(**opts)
        for text in ['가\t나', '가\r\n나', '\0']:
            with self.assertRaises(ValueError): CharacterReveal(text, self.font)
        with self.assertRaises(ValueError): CharacterReveal('가나', self.font, align='middle')
        with self.assertRaises(ValueError): CharacterReveal('가나', self.font, tracking=-10000)
        with self.assertRaises(ValueError): CharacterReveal('가나', self.font).render(float('nan'))


def preview(path: Path, font_path: Path):
    font = ImageFont.truetype(str(font_path), 38)
    layer = CharacterReveal('마음은 이성이 알지 못하는\n자기만의 이유를 가지고 있다', font, padding=10)
    timing = RevealTiming(duration=.8, glyph_fade_duration=1/30)
    times = [0, .016667, .05, .2, .4, .6, .783333, .8]
    width, height = max(layer.size[0]+20, 520), layer.size[1]+74
    sheet = Image.new('RGB', (width*2, height*4), '#202020')
    for index, t in enumerate(times):
        tile = Image.new('RGBA', (width, height), '#3a2925')
        tile.alpha_composite(layer.render(t, timing=timing), ((width-layer.size[0])//2, 8))
        draw = ImageDraw.Draw(tile)
        draw.text((10, height-42), f't={t:.6f}s; TEST FONT / CANDIDATE ANIMATOR', fill='white')
        draw.text((10, height-24), 'Attribution, dialogue bar and logo are separate layers.', fill='#cccccc')
        sheet.paste(tile.convert('RGB'), ((index%2)*width, (index//2)*height))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', type=Path, default=Path(__file__).resolve().parents[1]/'assets/fonts/NotoSansKR[wght].ttf')
    parser.add_argument('--preview', type=Path)
    args = parser.parse_args()
    RevealTests.font_path = args.font
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RevealTests))
    if result.wasSuccessful() and args.preview:
        preview(args.preview, args.font)
        print('Preview:', args.preview)
    raise SystemExit(0 if result.wasSuccessful() else 1)
