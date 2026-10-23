#!/usr/bin/env python3
"""Read selected longform template facts from a local Adobe asset library.

No Adobe APIs, account state, project execution, font activation, or writes to
the library. Resolve cached components by both recorded size and SHA-256.
Only selected content metadata is exported; account/device fields are omitted.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

SELECT = ('자막', '카톡', '로고 (왼쪽상단)', '아트라상 한글로고',
          '컬러코드', '그레인', '오버레이', '대판', '소제목', '밑줄')


def sha_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def extract(manifest):
    data = json.loads(manifest.read_text())
    if data.get('name') != '★아트라상':
        raise ValueError('Expected the explicitly selected ★아트라상 library')
    folder = manifest.parent / 'components'
    sizes = {}
    for p in folder.iterdir():
        if p.is_file() and not p.is_symlink():
            sizes.setdefault(p.stat().st_size, []).append(p)
    cache = {}
    elements = next(g['children'] for g in data['children'] if g['name'] == 'elements')
    assets = []
    for e in elements:
        name = e['name']
        if any(x in name for x in ('쇼츠', '릴스')) or not any(x in name for x in SELECT):
            continue
        row = {'name': name, 'element_id': e['id'], 'components': []}
        for c in e.get('components', []):
            if c.get('rel') == 'rendition':
                continue
            digest = c.get('library#sha256')
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError(f'Missing component identity: {name}')
            matches = []
            for p in sizes.get(c.get('length'), []):
                if p not in cache:
                    cache[p] = sha_file(p)
                if cache[p] == digest:
                    matches.append(p)
            record = {'name': c['name'], 'media_type': c['type'],
                      'sha256': digest, 'bytes': c['length'],
                      'cache_basenames': sorted(p.name for p in matches),
                      'available_locally': bool(matches)}
            if matches:
                p = matches[0]
                if c['type'] == 'application/vnd.adobe.graphic+json':
                    definition = json.loads(p.read_text())
                    record['definition'] = {k: definition[k] for k in
                        ('authorApp', 'apiVersion', 'clientControls', 'usedEffects',
                         'usedCompRenderers', 'usedFonts', 'usedFontsLocalized') if k in definition}
                elif c['type'] in ('application/vnd.adobe.ae.graphic+zip',
                                   'application/vnd.adobe.ppro.graphic+zip'):
                    with zipfile.ZipFile(p) as z:
                        members = []
                        for info in z.infolist():
                            member = {'name': info.filename, 'bytes': info.file_size}
                            # Inspect small sound assets and text reports, never execute projects.
                            suffix = Path(info.filename).suffix.lower()
                            if suffix in ('.wav', '.mp3') and info.file_size <= 16 * 1024 * 1024:
                                payload = z.read(info)
                                member['sha256'] = hashlib.sha256(payload).hexdigest()
                            elif info.filename.endswith('Report.txt') and info.file_size <= 1024 * 1024:
                                payload = z.read(info)
                                text = payload.decode('utf-8-sig', errors='replace')
                                member['sha256'] = hashlib.sha256(payload).hexdigest()
                                member['font_lines'] = [line.strip() for line in text.splitlines()
                                                        if 'Font family:' in line]
                                member['effect_names'] = [m.strip() for m in
                                    re.findall(r'^Effect:[ \t]*([^\r\n]*)', text, flags=re.MULTILINE)
                                    if m.strip()]
                            members.append(member)
                        record['archive_members'] = members
            row['components'].append(record)
        assets.append(row)
    return {'schema': 'atrass-local-authored-template-facts-v1',
            'library': data['name'], 'source_manifest_sha256': sha_file(manifest),
            'assets': assets,
            'scope': 'Authored library defaults and hashed assets, mainly historical templates. Not proof that the current A/B videos use every template or its default values.',
            'shorts_assets_opened': False, 'account_metadata_exported': False,
            'projects_executed': False, 'fonts_copied_or_activated': False,
            'direct_listening_seconds': 0, 'release_ready': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new evidence file')
    result = extract(args.manifest)
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'assets': len(result['assets']), 'components': sum(len(x['components']) for x in result['assets']),
                      'missing_components': sum(not c['available_locally'] for x in result['assets'] for c in x['components'])}))


if __name__ == '__main__':
    main()
