#!/usr/bin/env python3
"""Export an editable, SHA-bound A recipe runtime without source footage.

The portable tree preserves kit-relative adapter/template paths. The recipient
binds the same raw identity before composing. Historical evidence references in
provenance records remain historical; this is not a complete research archive.
"""
from pathlib import Path
import argparse
import copy
import importlib.metadata
import json
import os
import shutil

from apply_source_assets import require
from compose_source_style import inspect_recipe, resolved_decision
from run_longform import binding, read, save, sha, verify_bindings

KIT = Path(__file__).resolve().parents[1]
SCHEMA = 'atrass-editable-recipe-package-v1'
PACKAGES = ('av', 'numpy', 'Pillow', 'opencv-python-headless', 'fontTools')
FORBIDDEN = {'.mov', '.mp4', '.mkv', '.webm', '.wav', '.mp3', '.aac', '.m4a', '.pcm', '.f32'}


def relative_file(value):
    p = Path(value)
    require(not p.is_absolute() and p.parts and '..' not in p.parts and '.' not in p.parts,
            'Package paths must stay inside the package')
    return p


def verify(directory):
    root = Path(directory).resolve(strict=True)
    manifest = read(root/'MANIFEST.json')
    require(manifest.get('schema') == SCHEMA, 'An editable recipe package is required')
    seen = set()
    for row in manifest['files']:
        rel = relative_file(row['path'])
        require(str(rel) not in seen and rel.suffix.lower() not in FORBIDDEN,
                'Duplicate file or recorded media in the package')
        seen.add(str(rel))
        p = root/rel
        require(not any((root/Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts)+1)),
                'Package resources must not be symlinks')
        require(p.is_file() and p.stat().st_size == row['bytes'] and sha(p) == row['sha256'],
                'Package file changed or missing: '+str(rel))
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
              and '__pycache__' not in p.parts}
    require(actual == seen | {'MANIFEST.json'}, 'Unexpected or missing package files')
    recipe = root/relative_file(manifest['recipe'])
    require(str(recipe.relative_to(root)) in seen, 'Recipe is not bound by the manifest')
    spec = read(recipe)
    require(spec['source_sha256'] == manifest['external_source']['sha256'], 'Raw identity changed')
    source = (recipe.parent/spec['base']['input']['path']).resolve(strict=True)
    require(source.is_relative_to(root) and sha(source) == spec['base']['input']['sha256'],
            'Portable plan binding is invalid')
    return {'schema': SCHEMA, 'pass': True, 'files': len(seen),
            'bytes': sum(r['bytes'] for r in manifest['files']),
            'manifest_sha256': sha(root/'MANIFEST.json'), 'recipe': str(recipe),
            'source_sha256': spec['source_sha256'], 'raw_copied': False,
            'release_ready': False, 'company_execution_verified': False}


def export_recipe(recipe, output):
    recipe = Path(recipe).resolve(strict=True)
    output = Path(output).absolute()
    require(not output.exists(), 'Choose a new package directory; existing work is preserved')
    require(recipe.is_relative_to(KIT), 'Export a recipe inside the editing kit')
    spec = read(recipe)
    require(spec.get('base', {}).get('mode') == 'plan',
            'Export uses plan recipes; prepare job decisions before packaging')
    spec, inputs = inspect_recipe(recipe, spec['source_sha256'])
    source = (recipe.parent/spec['base']['input']['path']).resolve(strict=True)
    require(source.is_relative_to(KIT), 'Base plan must be inside the kit')
    plan = copy.deepcopy(read(source))
    raw_paths = set()
    for group in ('media', 'fonts'):
        for item in plan.get(group, {}).values():
            p = (source.parent/item['path']).resolve(strict=True)
            if group == 'media' and item.get('role') == 'source_footage':
                require(item.get('sha256') == spec['source_sha256'], 'Unexpected source identity')
                raw_paths.add(p)
                destination = output/'SOURCE_REQUIRED'/f"{spec['source_sha256']}.media"
            else:
                require(p.is_relative_to(KIT), 'External resources must be brought into the kit explicitly')
                destination = output/p.relative_to(KIT)
            item['path'] = os.path.relpath(destination, output/source.relative_to(KIT).parent)
    require(raw_paths, 'A declared raw source is required')
    planned = {}

    def collect(path):
        path = Path(path).resolve(strict=True)
        if path in raw_paths:
            return
        require(path.is_relative_to(KIT), 'An executable dependency is outside the kit: '+str(path))
        rel = path.relative_to(KIT)
        require(rel.suffix.lower() not in FORBIDDEN, 'Recorded media cannot enter the recipe package')
        planned[str(rel)] = binding(path)

    for row in inputs:
        collect(row['path'])
    collect(KIT/'engine/requirements.txt')
    # Attribution travels with generated/licensed visual resources. Font OFLs
    # are already included by inspect_recipe's complete fonts directory.
    for row in list(planned.values()):
        credit = Path(row['path']).parent/'ATTRIBUTION.txt'
        if credit.is_file():
            collect(credit)
    require('engine/export_recipe.py' in planned, 'The verifier must travel with the package')
    portable = copy.deepcopy(spec)
    recipe_rel, plan_rel = recipe.relative_to(KIT), source.relative_to(KIT)
    for step in portable['steps']:
        if 'decision' not in step:
            continue
        d = resolved_decision(step, recipe.parent, source)
        d.pop('project_sha256')
        descriptors = []
        if step['operation'] == 'source_assets':
            for event in d['events']:
                descriptors.extend((event['asset'], event['asset']['provenance']))
        elif step['operation'] == 'source_ending':
            descriptors.append(d['effect']['profile'])
        elif step['operation'] == 'terminal_chapter':
            descriptors.append(d['reference'])
        elif step['operation'] in ('extend_source_caption', 'extend_source_tail'):
            descriptors.append(d['transcript'])
        for descriptor in descriptors:
            p = Path(descriptor['path']).resolve(strict=True)
            require(p.is_relative_to(KIT) and str(p.relative_to(KIT)) in planned,
                    'Uncollected step dependency')
            descriptor['path'] = os.path.relpath(output/p.relative_to(KIT), output/recipe_rel.parent)
        step['decision'] = d
    verify_bindings(inputs)
    output.mkdir(parents=True, exist_ok=False)
    try:
        for rel, row in sorted(planned.items()):
            target = output/rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if rel not in (str(recipe_rel), str(plan_rel)):
                shutil.copy2(row['path'], target)
                require(sha(target) == row['sha256'], 'Copied input changed: '+rel)
        save(output/plan_rel, plan)
        portable['base']['input'] = {'path': os.path.relpath(output/plan_rel, output/recipe_rel.parent),
                                      'sha256': sha(output/plan_rel)}
        save(output/recipe_rel, portable)
        save(output/'recipes/source-catalog.json', {'schema': 'atrass-source-catalog-v1', 'entries': [{
            'id': 'packaged-source', 'variant': portable['variant'],
            'source_sha256': portable['source_sha256'],
            'recipe': {'path': os.path.relpath(output/recipe_rel, output/'recipes'),
                       'sha256': sha(output/recipe_rel)}}]})
        external = {'sha256': spec['source_sha256'], 'copied': False,
                    'instruction': 'Bind this exact raw identity with engine/bind_recipe_source.py.'}
        save(output/'SOURCE_REQUIRED.json', external)
        versions = {name: importlib.metadata.version(name) for name in PACKAGES}
        save(output/'runtime-environment.json', {'packages': versions,
             'scope': 'Exporter versions; recipient installation and execution are not yet verified.'})
        (output/'requirements-tested.txt').write_text(''.join(f'{n}=={v}\n' for n,v in versions.items()))
        (output/'README.txt').write_text(f'''아트라상 편집 결정 포함 실행 패키지

이 패키지는 동일 촬영 원본의 컷·자막·삽화·챕터·엔딩 결정을 다시 조립합니다.
원본/참고 완성본/전체 렌더/연구 전체 기록은 포함하지 않습니다.
원본 SHA가 다르면 재연결하지 않으며 새 촬영본의 대사·take·시각 결정이 필요합니다.

Python 3.12 환경에 requirements-tested.txt를 설치하고 FFmpeg를 준비합니다.
아래 명령의 원본, 작업 폴더, FFmpeg 경로를 실제 경로로 바꿉니다.
작업 폴더는 패키지 밖의 새 경로여야 합니다. 원본은 복사하지 않습니다.

    python engine/export_recipe.py verify .
    python engine/start_from_raw.py start --raw "/원본/촬영.mov" --output-dir "../작업/자동편집" --ffmpeg "/도구/ffmpeg"

위 명령은 원본의 실제 SHA로 이 패키지의 편집 결정을 자동 선택하여 조립·렌더합니다.
다른 촬영본이면 기존 컷을 적용하지 않고 새 원본 편집 요청을 남기며 종료 코드3을 반환합니다.
원본 경로만 받은 것과 새 대사의 편집 판단까지 자동으로 끝낸 것은 다릅니다.
아래는 같은 작업을 두 단계로 직접 실행할 때의 명령입니다.

    python engine/bind_recipe_source.py --recipe "{recipe_rel}" --raw "/원본/촬영.mov" --output-dir "../작업/재연결"
    python engine/run_longform.py start --raw "/원본/촬영.mov" --recipe "../작업/재연결/source.recipe.json" --output-dir "../작업/편집" --ffmpeg "/도구/ffmpeg"

이 패키지의 {len(portable['steps'])}개 후속 편집 단계와 기본 계획의 문구/자산/색/시간 결정은 recipe에 명시되어 있습니다.
중간 실패는 실패 기록을 보존합니다. 입력을 바꾸지 않았다면 run_longform.py resume을 사용합니다.
새 결정은 패키지 밖의 새 recipe에서 작업하며 원본 패키지는 보존합니다.
자산 옆 ATTRIBUTION.txt와 assets/fonts의 라이선스를 함께 전달합니다.
provenance의 과거 경로는 조사 기록입니다. 전체 근거는 비공개 원본 저장소에 있습니다.
렌더 성공을 레퍼런스 일치·음향 청취·회사 실기기 검수 완료로 해석하지 않습니다.
''', encoding='utf-8')
        verify_bindings(inputs)
        verify_bindings(list(planned.values()))
        rows = [dict(path=str(p.relative_to(output)), sha256=sha(p), bytes=p.stat().st_size)
                for p in sorted(output.rglob('*')) if p.is_file()]
        save(output/'MANIFEST.json', {'schema': SCHEMA, 'recipe': str(recipe_rel),
             'original_recipe_sha256': sha(recipe), 'external_source': external,
             'files': rows, 'raw_copied': False, 'historical_evidence_portable': False,
             'release_ready': False, 'company_execution_verified': False})
        return verify(output)
    except Exception as exc:
        save(output/'EXPORT_FAILURE.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    export = sub.add_parser('export')
    export.add_argument('--recipe', required=True)
    export.add_argument('--output-dir', required=True)
    check = sub.add_parser('verify')
    check.add_argument('directory')
    args = p.parse_args()
    result = export_recipe(args.recipe, args.output_dir) if args.command == 'export' else verify(args.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
