#!/usr/bin/env python3
"""Export a create-only, hash-bound render runtime without source recordings.

This relocates execution resources, not the historical research/validation tree.
Editorial changes still belong in the complete editing kit. No render or install
is started by this command; a receiving machine must relink source identities.
"""
from pathlib import Path
import argparse, copy, hashlib, importlib.metadata, json, platform, re, sys

RUNTIME = ('render.py', 'character_reveal.py', 'audio_policy.py',
           'source_quantization.py', 'relink_project.py', 'relink_by_identity.py',
           'export_delivery.py', 'requirements.txt')
PACKAGES = ('av', 'numpy', 'Pillow', 'opencv-python-headless', 'fontTools')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def identity(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value),
            'A lowercase SHA-256 identity is required')
    return value


def save(path, data):
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def export(project, output):
    project = Path(project).resolve(strict=True)
    output = Path(output).absolute()
    require(not output.exists(), 'Choose a new delivery directory')
    raw_bytes = project.read_bytes()
    original_sha = hashlib.sha256(raw_bytes).hexdigest()
    p = json.loads(raw_bytes)
    require(p.get('schema') == 'atrass-render-v1', 'A compiled render project is required')
    require(isinstance(p.get('media'), dict) and p['media'], 'Project media are required')
    q = copy.deepcopy(p)
    planned, resources, required, rewrites = {}, [], {}, []
    root = project.parent
    kit = Path(__file__).resolve().parent.parent

    def collect(path, category, expected=None):
        path = path.resolve(strict=True)
        require(path.is_file(), 'Resource is not a file')
        actual = sha(path)
        if expected is not None:
            require(actual == identity(expected), 'Resource SHA mismatch: ' + path.name)
        target = category + '/' + actual + path.suffix.lower()
        if target not in planned:
            planned[target] = (path, actual)
            resources.append({'path': target, 'sha256': actual, 'bytes': path.stat().st_size})
        return target

    for mid, item in q['media'].items():
        require(isinstance(item, dict), 'Media entry must be an object')
        require(item.get('role') != 'reference', 'Finished reference media cannot enter the runtime')
        old_path = item['path']
        if item.get('role') == 'source_footage' or item.get('type') != 'image':
            digest = identity(item.get('sha256'))
            row = required.setdefault(digest, {'sha256': digest, 'media_ids': [],
                                               'role': item.get('role'), 'copied': False})
            row['media_ids'].append(mid)
            item['path'] = 'SOURCE_REQUIRED/' + digest + '.media'
        else:
            from PIL import Image
            source = (root / old_path).resolve(strict=True)
            with Image.open(source) as image:
                image.verify()
            item['path'] = collect(source, 'assets', item.get('sha256'))
            item['sha256'] = sha(source)
        rewrites.append({'field': 'media.' + mid + '.path', 'original': old_path, 'portable': item['path']})
        for key, hash_key in (('provenance', 'provenance_sha256'), ('source_asset_path', 'source_asset_sha256')):
            if hash_key in item:
                source = (root / item[key]).resolve(strict=True)
                if key == 'provenance':
                    require(source.suffix.lower() == '.json', 'Provenance must be a JSON record')
                    require(isinstance(json.loads(source.read_text(encoding='utf-8')), dict), 'Invalid provenance record')
                else:
                    from PIL import Image
                    with Image.open(source) as image:
                        image.verify()
                item[key] = collect(source, 'provenance' if key == 'provenance' else 'asset-originals', item[hash_key])
                rewrites.append({'field': 'media.' + mid + '.' + key, 'original': p['media'][mid][key], 'portable': item[key]})
                if key == 'provenance':
                    credit = source.parent / 'ATTRIBUTION.txt'
                    if credit.is_file():
                        collect(credit, 'credits')
    for fid, font in q.get('fonts', {}).items():
        old_path = font['path']
        font['path'] = collect(root / old_path, 'fonts', font.get('sha256'))
        font['sha256'] = planned[font['path']][1]
        rewrites.append({'field': 'fonts.' + fid + '.path', 'original': old_path, 'portable': font['path']})
    # The kit's font licenses accompany font files; no ownership is inferred.
    for license_path in sorted((kit / 'assets/fonts').glob('*-OFL.txt')):
        collect(license_path, 'licenses')
    for name in RUNTIME:
        path = kit / 'engine' / name
        require(path.is_file(), 'Missing runtime module ' + name)
        target = 'engine/' + name
        planned[target] = (path, sha(path))
        resources.append({'path': target, 'sha256': planned[target][1], 'bytes': path.stat().st_size})
    versions = {name: importlib.metadata.version(name) for name in PACKAGES}
    require(sha(project) == original_sha, 'Project changed during export preparation')
    q['release_ready'] = False
    q['delivery'] = {'schema': 'atrass-runtime-delivery-v1', 'original_project_sha256': original_sha,
                     'runtime_paths_portable': True, 'historical_evidence_paths_portable': False,
                     'source_recordings_included': False, 'company_execution_verified': False,
                     'editorial_style_or_audio_approval': False}
    output.mkdir(parents=True, exist_ok=False)
    for target, (source, expected) in sorted(planned.items()):
        destination = output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as src, destination.open('xb') as dst:
            for block in iter(lambda: src.read(4 * 1024 * 1024), b''):
                dst.write(block)
        require(sha(destination) == expected and sha(source) == expected,
                'Resource changed while copying: ' + source.name)
    save(output / 'project.json', q)
    save(output / 'SOURCE_REQUIRED.json', {'sources': list(required.values()),
         'instruction': 'Relink each SHA with engine/relink_by_identity.py. Filenames do not establish identity.'})
    save(output / 'runtime-environment.json', {'python': platform.python_version(),
         'system': platform.system(), 'machine': platform.machine(), 'packages': versions,
         'scope': 'Exporter environment; recipient environment is not yet verified.'})
    (output / 'requirements-tested.txt').write_text(''.join(f'{n}=={v}\n' for n,v in versions.items()), encoding='utf-8')
    source_args = ' '.join('--source ' + d + '=/path/to/original.media' for d in required)
    readme = f'''# 아트라상 실행 패키지

원본 미디어는 포함하지 않습니다. SOURCE_REQUIRED.json의 SHA가 일치하는 원본을 준비합니다.
이 패키지는 지정된 편집본의 렌더 실행용이며, 새 편집 결정과 조사 근거는 원래 비공개 키트에서 이어갑니다.

Python 3.12 가상환경에서 requirements-tested.txt를 설치하고 FFmpeg 실행 파일을 준비합니다.
고정 버전은 runtime-environment.json에 기록한 환경의 값이며 다른 OS의 설치·재현 성공을 보장하지 않습니다.
아래 명령은 패키지 폴더에서 실행하며 실제 경로에 공백이 있으면 인수 전체를 따옴표로 감쌉니다.

    python engine/export_delivery.py verify .
    python engine/relink_by_identity.py project.json {source_args} --output project-linked.json
    python engine/render.py project-linked.json --output result.mp4 --draft --ffmpeg /path/to/ffmpeg

credits/의 사진 출처·라이선스·수정 표기를 배포 영상 설명이나 크레딧에 포함합니다.
licenses/는 폰트 라이선스입니다. 원작과 다른 대체 그림·후보 폰트·유한 복구 자산의 한계는 프로젝트에 유지됩니다.
원본 컷·글자·색·음향·전체 검수 상태를 완료로 승격하지 않았습니다.
과거 provenance JSON 안의 경로와 연구 인수인계는 역사적 기록이며 이 패키지에서 모두 해석되지 않습니다.
'''
    (output / 'README.md').write_text(readme, encoding='utf-8')
    for name in ('project.json', 'SOURCE_REQUIRED.json', 'runtime-environment.json', 'requirements-tested.txt', 'README.md'):
        path = output / name
        resources.append({'path': name, 'sha256': sha(path), 'bytes': path.stat().st_size})
    manifest = {'schema': 'atrass-runtime-package-v1', 'original_project_sha256': original_sha,
                'project_sha256': sha(output / 'project.json'), 'files': sorted(resources, key=lambda x:x['path']),
                'external_sources': list(required.values()), 'rewritten_fields': rewrites,
                'source_recordings_copied': False, 'release_ready': False,
                'historical_evidence_portable': False}
    save(output / 'MANIFEST.json', manifest)
    return verify(output)


def verify(directory):
    root = Path(directory).resolve(strict=True)
    m = json.loads((root / 'MANIFEST.json').read_text(encoding='utf-8'))
    require(m.get('schema') == 'atrass-runtime-package-v1', 'Invalid delivery manifest')
    seen = set()
    for row in m['files']:
        name = row['path']
        require(isinstance(name, str) and name not in seen, 'Duplicate or invalid resource')
        seen.add(name)
        path = (root / name).resolve(strict=True)
        require(path.is_relative_to(root), 'Resource escapes the delivery directory')
        require(path.is_file() and path.stat().st_size == row['bytes'], 'Resource size mismatch: ' + name)
        require(sha(path) == identity(row['sha256']), 'Resource SHA mismatch: ' + name)
    require('project.json' in seen and 'SOURCE_REQUIRED.json' in seen, 'Missing core delivery files')
    require(sha(root / 'project.json') == identity(m['project_sha256']), 'Delivery project SHA mismatch')
    p = json.loads((root / 'project.json').read_text(encoding='utf-8'))
    external = {s['sha256']: set(s['media_ids']) for s in m['external_sources']}
    require(not any(row['sha256'] in external for row in m['files']), 'A source recording was included in the package')
    for group in ('media', 'fonts'):
        for key, item in p.get(group, {}).items():
            name = item['path']
            if group == 'media' and key in external.get(item.get('sha256'), set()):
                require(name == 'SOURCE_REQUIRED/' + item['sha256'] + '.media', 'Wrong external placeholder')
            else:
                require(name in seen, 'Runtime resource not in manifest: ' + name)
                require(sha(root / name) == identity(item['sha256']), 'Runtime resource binding mismatch')
    return {'pass': True, 'files_verified': len(seen), 'external_source_identities': len(external),
            'source_recordings_copied': False, 'render_execution_verified': False, 'release_ready': False,
            'verification_scope': 'Manifest-listed files; recipient-added files are outside this manifest',
            'project_sha256': m['project_sha256'], 'manifest_sha256': sha(root / 'MANIFEST.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create'); create.add_argument('--project', required=True); create.add_argument('--output-dir', required=True)
    check = sub.add_parser('verify'); check.add_argument('directory')
    args = parser.parse_args()
    try:
        result = export(args.project, args.output_dir) if args.command == 'create' else verify(args.directory)
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (ValueError, OSError, KeyError, TypeError, importlib.metadata.PackageNotFoundError) as exc:
        print(str(exc), file=sys.stderr); return 1


if __name__ == '__main__':
    sys.exit(main())
