#!/usr/bin/env python3
"""Exercise actual relocation, identity and corrupt-package rejection."""
from pathlib import Path
import copy, json, tempfile
from PIL import Image
from export_delivery import export, verify, sha


def main():
    checks = 0
    with tempfile.TemporaryDirectory(prefix='atrass-delivery-test-') as temp:
        root = Path(temp)
        source = root / 'private-source.mov'; source.write_bytes(b'private source must never be copied')
        picture = root / 'picture.png'; Image.new('RGB', (8, 8), '#B9CDE0').save(picture)
        p = {'schema': 'atrass-render-v1', 'release_ready': True, 'media': {
            'raw': {'path': str(source), 'sha256': sha(source), 'role': 'source_footage'},
            'raw-alias': {'path': '/unavailable/raw.mov', 'sha256': sha(source), 'role': 'source_footage'},
            'art': {'path': 'picture.png', 'type': 'image', 'sha256': sha(picture), 'role': 'test_generated_image'}},
            'fonts': {}, 'clips': [], 'layers': [], 'audio': [], 'historical': {'path': '/past/research.json'}}
        project = root / 'project.json'
        def write(data): project.write_text(json.dumps(data), encoding='utf-8')
        write(p); target = root / 'delivery'
        receipt = export(project, target)
        assert receipt['pass'] and receipt['external_source_identities'] == 1; checks += 1
        manifest = json.loads((target / 'MANIFEST.json').read_text())
        assert manifest['external_sources'][0]['media_ids'] == ['raw', 'raw-alias']; checks += 1
        assert not any(sha(f) == sha(source) for f in target.rglob('*') if f.is_file()); checks += 1
        q = json.loads((target / 'project.json').read_text())
        assert q['historical'] == p['historical'] and q['release_ready'] is False; checks += 1
        assert (target / q['media']['art']['path']).read_bytes() == picture.read_bytes(); checks += 1
        def denied(fn):
            nonlocal checks
            try: fn()
            except (ValueError, OSError): checks += 1
            else: raise AssertionError('Expected explicit rejection')
        denied(lambda: export(project, target))
        assert verify(target)['pass']; checks += 1
        for index, mutate in enumerate([
            lambda d:d['media']['raw'].pop('sha256'),
            lambda d:d['media']['raw'].update(role='reference'),
            lambda d:d['media']['art'].update(sha256='0'*64),
            lambda d:d['media']['art'].update(path='private-source.mov'),
            lambda d:d['media']['art'].update(provenance='private-source.mov', provenance_sha256=sha(source)),
        ]):
            bad = copy.deepcopy(p); mutate(bad); write(bad)
            denied(lambda: export(project, root / f'denied-{index}'))
            assert not (root / f'denied-{index}').exists()
        resource = target / q['media']['art']['path']
        resource.write_bytes(b'X' * resource.stat().st_size)
        denied(lambda: verify(target))
        resource.write_bytes(picture.read_bytes())
        altered = copy.deepcopy(manifest); altered['files'].append(altered['files'][0])
        (target / 'MANIFEST.json').write_text(json.dumps(altered))
        denied(lambda: verify(target))
        altered = copy.deepcopy(manifest)
        altered['files'].append({'path': '../private-source.mov', 'sha256': sha(source), 'bytes': source.stat().st_size})
        (target / 'MANIFEST.json').write_text(json.dumps(altered))
        denied(lambda: verify(target))
        (target / 'MANIFEST.json').write_text(json.dumps(manifest))
        assert verify(target)['pass']; checks += 1
    print(json.dumps({'pass': True, 'checks': checks, 'scope': 'Package behavior only; no render or recipient installation approval'}))


if __name__ == '__main__': main()
