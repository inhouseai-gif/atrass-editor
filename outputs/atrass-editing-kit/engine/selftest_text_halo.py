#!/usr/bin/env python3
"""Behavioral tests for new-copy halo generation and create-only publication."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import create_text_halo as halo
from render import Composer

KIT = Path(__file__).resolve().parents[1]


class HaloTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        Image.new('RGB', (1920, 1080), '#EEE4CE').save(self.root / 'base.png')
        font = KIT / 'assets/fonts/SUIT-Variable.ttf'
        self.parent = {'id': 'quote', 'kind': 'text', 'font': 'body', 'font_size': 90,
                       'weight': 400, 'glyph_height': 64, 'fill': '#FFFFFF',
                       'text': '오늘도 고마웠어', 'in': .5, 'out': 2.5,
                       'position': [960, 500], 'anchor': 'center', 'z': 10}
        self.project = {'schema': 'atrass-render-v1', 'width': 1920, 'height': 1080,
                        'fps': 30, 'duration': 3, 'release_ready': False,
                        'media': {'base': {'path': 'base.png', 'type': 'image',
                                          'role': 'procedurally_created_graphic_asset'}},
                        'fonts': {'body': {'path': str(font), 'weight': 400, 'sha256': halo.digest(font)}},
                        'clips': [{'id': 'base', 'source': 'base', 'in': 0, 'out': 3, 'source_in': 0}],
                        'layers': [self.parent], 'audio': []}
        self.path = self.root / 'input.json'
        self.output = self.root / 'output.json'
        self.assets = self.root / 'halos'
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.project, ensure_ascii=False), encoding='utf-8')

    def build(self, **options):
        return halo.build(self.path, ['quote'], self.output, self.assets, **options)

    def create(self, **options):
        return halo.create(self.path, ['quote'], self.output, self.assets, **options)

    def test_existing_parent_pixels_position_and_order_unchanged(self):
        before = self.path.read_bytes()
        self.create()
        p = json.loads(self.output.read_text())
        self.assertEqual(p['layers'][1], self.parent)
        self.assertEqual(self.path.read_bytes(), before)
        old = Composer(self.project, self.root).image(self.parent, 1)
        new = Composer(p, self.root).image(p['layers'][1], 1)
        self.assertEqual(old[1], new[1])
        self.assertEqual(old[0].tobytes(), new[0].tobytes())
        self.assertEqual([x['id'] for x in p['layers']], ['quote-halo', 'quote'])

    def test_halo_is_outside_the_glyph_and_not_clipped(self):
        self.create()
        p = json.loads(self.output.read_text()); c = Composer(p, self.root)
        white = np.full((1080, 1920, 3), 255, np.uint8)
        rendered = c.compose(white, p['layers'], 1)
        self.assertGreater(np.count_nonzero(rendered[:, :, 0] < 250), 1000)
        alpha = np.array(Image.open(self.assets / 'text-halo-001.png'))[:, :, 3]
        self.assertFalse(alpha[0].any() or alpha[-1].any() or alpha[:, 0].any() or alpha[:, -1].any())
        self.assertTrue(json.loads((self.assets / 'provenance.json').read_text())['layer_records'][0]['expanded_padding_identical'])

    def test_parent_fades_opacity_and_lifetime_copy_exactly(self):
        self.parent.update(opacity=.8, opacity_keys=[[0, 0], [.4, 1], [1.9, .5]], fade_in=.2, fade_out=.1)
        self.save(); result, _, _ = self.build()
        child = result['layers'][0]
        for key in ('in', 'out', 'opacity', 'opacity_keys', 'fade_in', 'fade_out'):
            self.assertEqual(child[key], self.parent[key])

    def test_new_copy_changes_asset_without_reference_glyphs(self):
        first, art1, _ = self.build()
        self.parent['text'] = '작은 표현 하나가 마음을 바꿉니다'
        self.save(); second, art2, _ = self.build()
        self.assertNotEqual(art1[0][1], art2[0][1])
        self.assertEqual(second['layers'][1]['text'], self.parent['text'])
        self.assertNotEqual(first['media']['text-halo-001']['sha256'], second['media']['text-halo-001']['sha256'])

    def test_multiline_box_offsets_and_anchor_use_actual_layout(self):
        self.parent.update(text='작은 표현\n오늘부터 시작해요', box=[900, 400, 1550, 650],
                           anchor='top_center', text_offset=[31, 22], align='left', line_gap=24)
        self.save(); result, _, report = self.build()
        glyph, xy = Composer(self.project, self.root).image(self.parent, 1)
        self.assertEqual(report['layer_records'][0]['glyph_top_left'], list(xy))
        self.assertEqual(result['layers'][1], self.parent)

    def test_rejects_motion_reveal_and_existing_effect(self):
        for change in ({'rotation': 1}, {'scale_keys': [[0, 1]]}, {'x_keys': [[0, 2]]},
                       {'character_reveal': {}}, {'reveal_keys': [[0, 1]]},
                       {'background': '#111111'}, {'shadow': {'blur': 2}}):
            with self.subTest(change=change):
                original = copy.deepcopy(self.parent)
                self.parent.update(change); self.save()
                with self.assertRaises((ValueError, TypeError)):
                    self.build()
                self.assertFalse(self.assets.exists() or self.output.exists())
                self.parent.clear(); self.parent.update(original)

    def test_rejects_screen_clipping(self):
        self.parent.update(position=[2, 1], anchor=None)
        self.save()
        with self.assertRaisesRegex(ValueError, 'clipped'):
            self.build()

    def test_existing_outputs_and_assets_are_preserved(self):
        self.output.write_text('owned elsewhere')
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.output.read_text(), 'owned elsewhere')
        self.output.unlink(); self.assets.mkdir(); (self.assets/'keep').write_text('keep')
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual((self.assets/'keep').read_text(), 'keep')

    def test_invalid_parameters_are_write_free(self):
        for options in ({'sigma': 0}, {'sigma': float('nan')}, {'opacity': 2}, {'dx': .5}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.create(**options)
        self.assertFalse(self.assets.exists() or self.output.exists())

    def test_wrong_font_binding_rejected(self):
        self.project['fonts']['body']['sha256'] = '0' * 64; self.save()
        with self.assertRaisesRegex(ValueError, 'font SHA'):
            self.create()
        self.assertFalse(self.assets.exists())

    def test_unknown_duplicate_ids_and_id_collisions(self):
        for ids in ([], ['missing'], ['quote', 'quote']):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                halo.build(self.path, ids, self.output, self.assets)
        self.project['media']['text-halo-001'] = copy.deepcopy(self.project['media']['base']); self.save()
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.build()

    def test_moving_output_parent_is_rejected(self):
        other = self.root/'new'; other.mkdir()
        with self.assertRaisesRegex(ValueError, 'beside'):
            halo.build(self.path, ['quote'], other/'output.json', self.assets)

    def test_atomic_publish_race_preserves_other_writer(self):
        def race(*_args):
            self.output.write_text('other writer')
            raise FileExistsError('race')
        with patch.object(halo.os, 'link', race), self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.output.read_text(), 'other writer')
        self.assertFalse(self.assets.exists())
        self.assertFalse(list(self.root.glob('.halo-project-*.tmp')))

    def test_decomposed_korean_is_not_silently_rewritten(self):
        self.parent['text'] = '고마워'; self.save()
        with self.assertRaisesRegex(ValueError, 'NFC'):
            self.build()

    def test_missing_font_glyph_rejected_before_writing(self):
        self.parent['text'] = '고마워 🦕'; self.save()
        with self.assertRaisesRegex(ValueError, 'no glyph'):
            self.create()
        self.assertFalse(self.assets.exists() or self.output.exists())

    def test_project_mutation_during_measurement_rejected(self):
        original = halo.Composer.image
        def mutate(comp, layer, time):
            changed = copy.deepcopy(self.project)
            changed['layers'][0]['text'] = '다른 문장'
            self.path.write_text(json.dumps(changed, ensure_ascii=False))
            return original(comp, layer, time)
        with patch.object(halo.Composer, 'image', mutate), self.assertRaisesRegex(ValueError, 'Source project changed'):
            self.create()
        self.assertFalse(self.assets.exists() or self.output.exists())

    def test_font_mutation_during_measurement_rejected(self):
        font = self.root / 'font.ttf'
        font.write_bytes(Path(self.project['fonts']['body']['path']).read_bytes())
        self.project['fonts']['body']['path'] = str(font); self.save()
        original = halo.Composer.image
        def mutate(comp, layer, time):
            result = original(comp, layer, time)
            font.write_bytes(font.read_bytes() + b'changed')
            return result
        with patch.object(halo.Composer, 'image', mutate), self.assertRaisesRegex(ValueError, 'Selected font changed'):
            self.create()
        self.assertFalse(self.assets.exists() or self.output.exists())

    def test_project_mutation_just_before_publication_rolls_back(self):
        original = halo.os.fsync
        def mutate(fd):
            original(fd)
            self.path.write_text(self.path.read_text() + ' ')
        with patch.object(halo.os, 'fsync', mutate), self.assertRaisesRegex(ValueError, 'Source project changed'):
            self.create()
        self.assertFalse(self.assets.exists() or self.output.exists())
        self.assertFalse(list(self.root.glob('.halo-project-*.tmp')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
