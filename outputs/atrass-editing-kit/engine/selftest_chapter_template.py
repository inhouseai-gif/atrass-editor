#!/usr/bin/env python3
"""Offline behavioral tests for new chapter text and create-only project output."""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import create_chapter as chapter
from render import Composer, validate


SHORT = '마음이 닿는 순간'
LONG = '말보다 행동으로 전해지는 마음을 천천히 이해하는 방법'


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='atrass-chapter-test-')
        self.root = Path(self.temporary.name)
        self.output = self.root / 'new.json'

    def tearDown(self):
        self.temporary.cleanup()

    def make(self, title=SHORT, number=2, duration='3.5'):
        return chapter.make_project(title, number, duration, self.output)

    def test_short_text_uses_natural_aspect_not_old_phrase_width(self):
        p = self.make(); m = p['chapter_template']['title_measurement']
        self.assertEqual(m['glyph_size'][1], 76)
        self.assertNotEqual(m['glyph_size'][0], 941)
        self.assertAlmostEqual(m['glyph_size'][0], m['natural_mask_size'][0] * 76 / m['natural_mask_size'][1], delta=.5)
        for layer in p['layers']:
            self.assertNotIn('glyph_width', layer)
            self.assertNotIn('scale_x', layer)

    def test_long_title_uniformly_shrinks_inside_safety_area(self):
        p = self.make(LONG); m = p['chapter_template']['title_measurement']; b = p['chapter_template']['box_bbox']
        self.assertLess(m['glyph_size'][1], 76)
        self.assertGreaterEqual(m['glyph_size'][1], chapter.MIN_TITLE_HEIGHT)
        self.assertAlmostEqual(m['glyph_size'][0], m['natural_mask_size'][0] * m['uniform_scale'], delta=.5)
        self.assertGreaterEqual(b[0], 96); self.assertLessEqual(b[2], 1824)
        self.assertEqual(p['layers'][-1]['text'], LONG)

    def test_spaces_are_preserved_and_scaled_with_the_glyphs(self):
        a, b = self.make('마음이 닿는 순간'), self.make('마음이  닿는 순간')
        self.assertEqual(b['layers'][-1]['text'], '마음이  닿는 순간')
        ma, mb = (p['chapter_template']['title_measurement'] for p in (a, b))
        self.assertGreater(mb['glyph_size'][0], ma['glyph_size'][0])
        self.assertAlmostEqual(mb['glyph_size'][0] - ma['glyph_size'][0], ma['space_advance_px'], delta=1)
        long = self.make(LONG)['chapter_template']['title_measurement']
        self.assertAlmostEqual(long['space_advance_px'] / ma['space_advance_px'], long['uniform_scale'] / ma['uniform_scale'], places=6)

    def test_backing_padding_heading_gap_and_centering(self):
        for title in (SHORT, LONG, '한 소녀와 한 소년 이야기'):
            p = self.make(title); data = p['chapter_template']
            b, t, h = data['box_bbox'], data['title_measurement']['glyph_bbox'], data['heading_measurement']['glyph_bbox']
            self.assertEqual([t[0] - b[0], t[1] - b[1], b[2] - t[2], b[3] - t[3]], [24, 25, 24, 24])
            self.assertEqual(b[1] - h[3], 44)
            for rect in (b, t, h):
                self.assertAlmostEqual((rect[0] + rect[2]) / 2, 960, delta=.5)

    def test_heading_number_is_independent_of_title(self):
        p = self.make(number=9999)
        self.assertEqual(p['layers'][-2]['text'], 'CHAPTER 9999')
        self.assertEqual(p['layers'][-1]['text'], SHORT)
        self.assertLess(p['chapter_template']['heading_measurement']['glyph_size'][0], 1728)

    def test_duration_ceil_frame_contract_and_complete_lifetime(self):
        for value in ('0.001', '1/3', '3.5', '60'):
            p = self.make(duration=value)
            frames = math.ceil(Fraction(value) * Fraction(p['fps']))
            self.assertEqual(p['chapter_template']['frame_count'], frames)
            self.assertEqual(p['duration'], float(Fraction(frames) / Fraction(p['fps'])))
            self.assertGreaterEqual(p['duration'] + 1e-12, float(Fraction(value)))
            for layer in p['clips'] + p['layers']:
                self.assertEqual((layer['in'], layer['out']), (0, p['duration']))

    def test_layer_separation_and_generated_media_scope(self):
        p = self.make()
        self.assertEqual(len(p['clips']), 1); self.assertEqual(len(p['layers']), 4)
        self.assertEqual([x['kind'] for x in p['layers']], ['image', 'rect', 'text', 'text'])
        self.assertEqual(p['audio'], [])
        self.assertFalse(p['release_ready'])
        self.assertEqual(p['chapter_template']['raw_reconstruction_frames'], 0)
        self.assertEqual(p['chapter_template']['manual_visual_review'], 'pending_for_this_new_project')
        self.assertNotIn('reference_range_frames', p)
        self.assertTrue(all(m['type'] == 'image' and m['role'] == 'procedurally_created_graphic_asset' for m in p['media'].values()))

    def test_actual_renderer_masks_match_declared_geometry_without_clipping(self):
        for title in (SHORT, LONG, '가각 깎 꽉', 'ABC 123.?!'):
            p = self.make(title); validate(p, self.output.parent, True)
            composer = Composer(p, self.output.parent)
            for layer, key in [(p['layers'][-1], 'title_measurement'), (p['layers'][-2], 'heading_measurement')]:
                image = composer.build_static(layer)
                self.assertEqual(list(image.size), p['chapter_template'][key]['glyph_size'])
                self.assertIsNotNone(image.getchannel('A').getbbox())
                # Place the whole mask on a larger canvas; no active ink is lost.
                canvas = Image.new('RGBA', (1920, 1080))
                canvas.alpha_composite(image, tuple(layer['position']))
                crop = canvas.crop((*layer['position'], layer['position'][0] + image.width, layer['position'][1] + image.height))
                self.assertEqual(crop.tobytes(), image.tobytes())

    def test_nfc_normalization_is_explicit_and_input_is_retained(self):
        raw = '\u1100\u1161\u11a8'
        p = self.make(raw)
        self.assertEqual(p['chapter_template']['input_title'], raw)
        self.assertEqual(p['layers'][-1]['text'], '각')

    def test_unreadable_long_title_fails_before_any_output(self):
        with self.assertRaisesRegex(ValueError, 'below 48px'):
            chapter.create_project('긴 문장을 ' * 40 + '끝', 1, '3', self.output)
        self.assertFalse(self.output.exists())

    def test_unsupported_glyph_is_rejected_instead_of_tofu(self):
        with self.assertRaisesRegex(ValueError, 'no verified glyph'):
            chapter.create_project('마음 🦄', 1, '3', self.output)
        self.assertFalse(self.output.exists())

    def test_empty_controls_multiline_and_hidden_spacing_are_rejected(self):
        for title in ('', '   ', ' 앞', '뒤 ', '가\n나', '가\t나', '가\u200b나', '가\u202e나', '가\u00a0나'):
            with self.subTest(title=repr(title)), self.assertRaises(ValueError):
                self.make(title)
        self.assertFalse(self.output.exists())

    def test_bad_numbers_durations_and_suffix_are_rejected(self):
        for n in (0, -1, 10000, True, 1.5):
            with self.subTest(number=n), self.assertRaises(ValueError): self.make(number=n)
        for d in ('0', '-1', 'NaN', 'Infinity', '1/0', '61'):
            with self.subTest(duration=d), self.assertRaises(ValueError): self.make(duration=d)
        with self.assertRaises(ValueError): chapter.make_project(SHORT, 1, '3', self.root / 'bad.mp4')

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b'user-owned bytes')
        with self.assertRaises(FileExistsError): chapter.create_project(SHORT, 1, '3', self.output)
        self.assertEqual(self.output.read_bytes(), b'user-owned bytes')

    def test_symlink_and_dangling_symlink_are_not_overwritten(self):
        target = self.root / 'target'; target.write_bytes(b'protected')
        for name, path in [('existing', target), ('dangling', self.root / 'not-there')]:
            link = self.root / (name + '.json'); link.symlink_to(path)
            with self.assertRaises(FileExistsError): chapter.create_project(SHORT, 1, '3', link)
            self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_bytes(), b'protected')

    def test_racing_writer_wins_without_partial_json_or_temp_leftover(self):
        original = chapter.os.link
        def racing_link(source, dest):
            Path(dest).write_bytes(b'other writer')
            return original(source, dest)
        with patch.object(chapter.os, 'link', side_effect=racing_link), self.assertRaises(FileExistsError):
            chapter.create_project(SHORT, 1, '3', self.output)
        self.assertEqual(self.output.read_bytes(), b'other writer')
        self.assertEqual([p.name for p in self.root.iterdir()], ['new.json'])

    def test_successful_write_is_complete_json_and_repeat_is_rejected(self):
        p = chapter.create_project(SHORT, 1, '3', self.output)
        self.assertEqual(json.loads(self.output.read_text()), p)
        before = chapter.sha256(self.output)
        with self.assertRaises(FileExistsError): chapter.create_project(LONG, 2, '4', self.output)
        self.assertEqual(chapter.sha256(self.output), before)

    def test_documented_cli_creates_a_new_project_and_refuses_reuse(self):
        command = [sys.executable, str(Path(chapter.__file__)), '--title', SHORT,
                   '--number', '2', '--duration', '3.5', '--output', str(self.output)]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)['frames'], 210)
        self.assertEqual(json.loads(self.output.read_text())['layers'][-1]['text'], SHORT)
        before = chapter.sha256(self.output)
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 2)
        self.assertIn('Refusing to overwrite', second.stderr)
        self.assertEqual(chapter.sha256(self.output), before)

    def test_relative_dependency_hashes_and_move_with_kit(self):
        old = self.root / 'old-kit'; new = self.root / 'moved-kit'
        source = chapter.KIT
        required = ['projects/A-chapter-pilot-procedural-v1.json', 'assets/chapter-pilot/provenance.json',
            'assets/chapter-pilot/gradient-dct-10x8.png', 'assets/chapter-pilot/grain-static-seed-20261021.png',
            'assets/fonts/NotoSansKR[wght].ttf', 'assets/fonts/PretendardVariable.ttf']
        for name in required:
            dest = old / name; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source / name, dest)
        out = old / 'projects/cards/new.json'
        p = chapter.create_project(LONG, 4, '2', out, kit=old)
        for entry in p['chapter_template']['dependencies']:
            self.assertFalse(Path(entry['path']).is_absolute())
            self.assertEqual(chapter.sha256(out.parent / entry['path']), entry['sha256'])
        shutil.move(str(old), str(new))
        moved = new / out.relative_to(old)
        validate(p, moved.parent, True)
        composer = Composer(p, moved.parent)
        base = Image.open(moved.parent / p['media']['procedural-gradient']['path']).convert('RGB')
        import numpy as np
        result = composer.compose(np.asarray(base), p['layers'], 0)
        self.assertEqual(result.shape, (1080, 1920, 3))
        self.assertFalse(old.exists())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, help='Optional new result JSON; existing paths are refused')
    args = parser.parse_args()
    if args.json and args.json.exists(): parser.error('Refusing to overwrite result JSON')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ChapterTests))
    data = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
            'passed': result.wasSuccessful(), 'scope': 'Offline template behavior, actual renderer masks, path relocation and create-only output; not original font/style confirmation.'}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        with args.json.open('x', encoding='utf-8') as stream: json.dump(data, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps(data))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
