#!/usr/bin/env python3
"""Fault-injection regression checks for create-only effect publication.

Both public generators exercise the shared publisher. Tests write only inside
TemporaryDirectory, never mutate the packaged font, and simulate ENOSPC rather
than filling the disk. Four test methods cover ten parametrized cases.
"""
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import create_panel_shadow as panel
import create_text_halo as halo

KIT = Path(__file__).resolve().parents[1]


class PartialWrite:
    """Create real partial bytes, then raise the original storage error."""

    def __init__(self, stream, error):
        self.stream, self.error = stream, error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def write(self, data):
        self.stream.write(data[:17])
        self.stream.flush()
        raise self.error


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, mode, case):
        root = self.root / (mode + '-' + case)
        root.mkdir()
        Image.new('RGB', (1280, 720), '#D4D0C4').save(root / 'base.png')
        sibling = root / 'keep.txt'
        sibling.write_bytes(b'existing user file\n')
        project = {
            'schema': 'atrass-render-v1', 'width': 1280, 'height': 720,
            'fps': 30, 'duration': 3, 'release_ready': False,
            'media': {'base': {'path': 'base.png', 'type': 'image',
                               'role': 'procedurally_created_graphic_asset'}},
            'fonts': {}, 'clips': [{'id': 'base', 'source': 'base',
                                    'in': 0, 'out': 3, 'source_in': 0}],
            'audio': []}
        parent = {'id': 'parent', 'in': .4, 'out': 2.6, 'z': 10}
        if mode == 'panel':
            parent.update(kind='rect', position=[140, 180], size=[500, 300],
                          radius=26, fill='#B9CDE0')
            api = panel
        else:
            font = KIT / 'assets/fonts/SUIT-Variable.ttf'
            project['fonts']['body'] = {'path': str(font),
                                        'sha256': halo.digest(font)}
            parent.update(kind='text', text='오늘도 고마웠어', font='body',
                          font_size=64, glyph_height=48, fill='#FFFFFF',
                          position=[640, 360], anchor='center')
            api = halo
        project['layers'] = [parent]
        source, output, assets = root / 'input.json', root / 'output.json', root / 'assets'
        source.write_text(json.dumps(project, ensure_ascii=False), encoding='utf-8')
        return api, source, output, assets, sibling

    def assert_rollback(self, source, output, assets, sibling, expected_source):
        self.assertFalse(output.exists())
        self.assertFalse(assets.exists())
        self.assertEqual(source.read_bytes(), expected_source)
        self.assertEqual(sibling.read_bytes(), b'existing user file\n')
        self.assertEqual(list(source.parent.glob('.halo-project-*.tmp')), [])

    def assert_retry(self, api, source, output, assets):
        before = source.read_bytes()
        api.create(source, ['parent'], output, assets)
        self.assertEqual(source.read_bytes(), before)
        actual = json.loads(output.read_text(encoding='utf-8'))
        original = json.loads(before)
        self.assertEqual(actual['layers'][1], original['layers'][0])
        self.assertEqual(len(list(assets.glob('*.png'))), 1)
        report = json.loads((assets / 'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual(report['source_project_sha256'], halo.digest(source))
        self.assertEqual(list(source.parent.glob('.halo-project-*.tmp')), [])

    def partial_write_cases(self, target_kind):
        for mode in ('panel', 'text'):
            with self.subTest(mode=mode, target_kind=target_kind):
                api, source, output, assets, sibling = self.fixture(mode, target_kind)
                original_source, original_open = source.read_bytes(), Path.open
                error = OSError(errno.ENOSPC, 'simulated disk full after partial write')
                intercepted = []

                def failing_open(path, *args, **kwargs):
                    stream = original_open(path, *args, **kwargs)
                    selected = (path.suffix == '.png' if target_kind == 'png'
                                else path.name == 'provenance.json')
                    if (path.parent == assets and selected
                            and (args[0] if args else kwargs.get('mode')) == 'xb'):
                        intercepted.append(path)
                        return PartialWrite(stream, error)
                    return stream

                with patch.object(Path, 'open', failing_open):
                    with self.assertRaises(OSError) as caught:
                        api.create(source, ['parent'], output, assets)
                self.assertIs(caught.exception, error)
                self.assertEqual(caught.exception.errno, errno.ENOSPC)
                self.assertEqual(len(intercepted), 1)
                self.assert_rollback(source, output, assets, sibling, original_source)
                self.assert_retry(api, source, output, assets)

    def test_partial_png_write_rolls_back_and_allows_same_path_retry(self):
        self.partial_write_cases('png')

    def test_partial_provenance_write_cleans_png_and_allows_retry(self):
        self.partial_write_cases('provenance')

    def test_existing_user_output_or_asset_directory_is_preserved(self):
        for mode in ('panel', 'text'):
            for kind in ('output', 'assets'):
                with self.subTest(mode=mode, kind=kind):
                    api, source, output, assets, sibling = self.fixture(mode, kind)
                    source_bytes = source.read_bytes()
                    if kind == 'output':
                        output.write_bytes(b'existing user project\n')
                        preserved = {output: output.read_bytes()}
                    else:
                        assets.mkdir()
                        preserved = {}
                        for name in ('panel-shadow-001.png', 'text-halo-001.png', 'provenance.json'):
                            path = assets / name
                            path.write_bytes(b'existing user asset\n')
                            preserved[path] = path.read_bytes()
                    with self.assertRaises(FileExistsError):
                        api.create(source, ['parent'], output, assets)
                    self.assertEqual(source.read_bytes(), source_bytes)
                    self.assertEqual(sibling.read_bytes(), b'existing user file\n')
                    for path, expected in preserved.items():
                        self.assertEqual(path.read_bytes(), expected)
                    self.assertFalse(assets.exists() if kind == 'output' else output.exists())
                    self.assertEqual(list(source.parent.glob('.halo-project-*.tmp')), [])

    def test_input_changed_after_temp_write_is_rejected_without_undoing_change(self):
        for mode in ('panel', 'text'):
            with self.subTest(mode=mode):
                api, source, output, assets, sibling = self.fixture(mode, 'input-change')
                changed = source.read_bytes() + b'\n '
                original_fsync = halo.os.fsync
                calls = []

                def mutate_after_fsync(fd):
                    original_fsync(fd)
                    calls.append(fd)
                    source.write_bytes(changed)

                with patch.object(halo.os, 'fsync', mutate_after_fsync):
                    with self.assertRaisesRegex(ValueError, 'Source project changed'):
                        api.create(source, ['parent'], output, assets)
                self.assertEqual(len(calls), 1)
                self.assert_rollback(source, output, assets, sibling, changed)
                self.assert_retry(api, source, output, assets)


if __name__ == '__main__':
    unittest.main()
