"""Recipient package integrity tests; full adapter relocation is an integration run."""
from pathlib import Path
import copy
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'engine'))
import export_recipe as module


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'한글 회사'
        self.root.mkdir()
        self.digest = '1'*64
        self.plan = self.root/'plan.json'
        self.plan.write_text('{}')
        self.recipe = self.root/'recipe.json'
        self.recipe.write_text(json.dumps({'source_sha256': self.digest,
            'base': {'input': {'path': 'plan.json', 'sha256': module.sha(self.plan)}}}))
        self.manifest = {'schema': module.SCHEMA, 'recipe': 'recipe.json',
            'external_source': {'sha256': self.digest},
            'files': [{'path': p.name, 'sha256': module.sha(p), 'bytes': p.stat().st_size}
                      for p in (self.plan, self.recipe)]}
        self.write_manifest()

    def write_manifest(self):
        (self.root/'MANIFEST.json').write_text(json.dumps(self.manifest))

    def test_valid_unicode_package_does_not_require_raw(self):
        result = module.verify(self.root)
        self.assertTrue(result['pass'])
        self.assertFalse(result['raw_copied'])
        self.assertFalse(result['company_execution_verified'])

    def test_modified_file_rejected(self):
        self.plan.write_text('[]')
        with self.assertRaisesRegex(ValueError, 'changed or missing'):
            module.verify(self.root)

    def test_missing_file_rejected(self):
        self.plan.rename(self.root/'renamed.json')
        with self.assertRaisesRegex(ValueError, 'changed or missing'):
            module.verify(self.root)

    def test_unlisted_file_rejected(self):
        (self.root/'secret.txt').write_text('test')
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            module.verify(self.root)

    def test_manifest_escape_rejected(self):
        for value in ('../escape.json', '/tmp/escape.json'):
            self.manifest['files'][0]['path'] = value
            self.write_manifest()
            with self.assertRaisesRegex(ValueError, 'inside'):
                module.verify(self.root)

    def test_duplicate_manifest_resource_rejected(self):
        self.manifest['files'].append(copy.deepcopy(self.manifest['files'][0]))
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            module.verify(self.root)

    def test_media_file_rejected_even_when_hash_is_valid(self):
        media = self.root/'raw.mov'
        media.write_bytes(b'not real footage')
        self.manifest['files'].append({'path': media.name, 'sha256': module.sha(media), 'bytes': media.stat().st_size})
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'recorded media'):
            module.verify(self.root)

    def test_symlink_resource_rejected(self):
        link = self.root/'linked.json'
        link.symlink_to(self.plan)
        self.manifest['files'].append({'path': link.name, 'sha256': module.sha(link), 'bytes': link.stat().st_size})
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            module.verify(self.root)

    def test_existing_export_folder_never_overwritten(self):
        with self.assertRaisesRegex(ValueError, 'existing work'):
            module.export_recipe(self.recipe, self.root)
        self.assertTrue(module.verify(self.root)['pass'])


if __name__ == '__main__':
    unittest.main()
