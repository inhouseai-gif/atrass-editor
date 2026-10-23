#!/usr/bin/env python3
"""Copy a render project/edit plan with verified media paths, preserving evidence.

The output must be a new sibling of the input so relative fonts and evidence
retain their meaning. Only media[].path/sha256 may change. A missing old file
requires a digest already stored in media or supplied from trusted provenance.
"""
from pathlib import Path
import argparse, copy, hashlib, json, os, re, sys, tempfile
from audio_policy import validate_audio_policy


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', value):
        raise ValueError('Expected a 64-character SHA-256 digest')
    return value.lower()


def pairs(values):
    out = {}
    for value in values:
        key, sep, target = value.partition('=')
        if not sep or not key or not target or key in out:
            raise ValueError('Use unique MEDIA_ID=VALUE arguments')
        out[key] = target
    return out


def relink(project, output, replacements, expected_hashes=None):
    project, output = Path(project).resolve(strict=True), Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new output path; existing files are preserved')
    if output.parent != project.parent:
        raise ValueError('Output must be a sibling of the input to preserve relative fonts/evidence')
    original = json.loads(project.read_text(encoding='utf-8'))
    if original.get('schema') not in ('atrass-render-v1', 'atrass-edit-plan-v1'):
        raise ValueError('Only atrass-render-v1 and atrass-edit-plan-v1 are supported')
    validate_audio_policy(original)
    media = original.get('media')
    if not isinstance(media, dict) or not replacements:
        raise ValueError('A media dictionary and at least one replacement are required')
    expected_hashes = expected_hashes or {}
    if set(replacements) - set(media) or set(expected_hashes) - set(replacements):
        raise ValueError('Unknown media ID or a digest without a replacement')
    result = copy.deepcopy(original)
    verified = []
    cache = {}

    def hash_file(path):
        if not path.is_file():
            raise ValueError('Missing media file: ' + str(path))
        if path not in cache:
            cache[path] = digest(path)
        return cache[path]

    for key, item in media.items():
        old = Path(item['path'])
        old = (project.parent / old).resolve() if not old.is_absolute() else old.resolve()
        if key not in replacements:
            current = hash_file(old) if item.get('sha256') else None
            if not old.is_file():
                raise ValueError('Unrelinked media is missing: ' + key)
            if current and current != sha(item['sha256']):
                raise ValueError('Unrelinked media digest mismatch: ' + key)
            continue
        new = Path(replacements[key]).expanduser().resolve(strict=True)
        expected = []
        if item.get('sha256') is not None:
            expected.append(('stored_media_sha256', sha(item['sha256'])))
        if key in expected_hashes:
            expected.append(('explicit_trusted_sha256', sha(expected_hashes[key])))
        if old.is_file():
            expected.append(('existing_original_file_sha256', hash_file(old)))
        if not expected:
            raise ValueError('Old source unavailable and no trusted SHA-256 for ' + key)
        if len({value for _, value in expected}) != 1:
            raise ValueError('Conflicting expected/original SHA-256 values for ' + key)
        actual = hash_file(new)
        if actual != expected[0][1]:
            raise ValueError('Replacement SHA-256 mismatch for ' + key)
        result['media'][key]['path'] = str(new)
        result['media'][key]['sha256'] = actual
        verified.append({'id': key, 'old_path': item['path'], 'new_path': str(new),
                         'sha256': actual, 'verified_against': [x[0] for x in expected]})
    for key, font in original.get('fonts', {}).items():
        path = Path(font['path'])
        if not (project.parent / path).is_file():
            raise ValueError('Font is missing: ' + key + '; copy the complete kit first')
    # Link a completely written temporary file into place without replacing any file.
    fd, temporary = tempfile.mkstemp(prefix='.relink-', suffix='.json', dir=output.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'input': str(project), 'input_sha256': digest(project), 'output': str(output),
            'output_sha256': digest(output), 'verified_media': verified,
            'modified_fields': ['media.' + x['id'] + '.' + field for x in verified for field in ('path', 'sha256')],
            'historical_evidence_paths_unchanged': True, 'release_status_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--output', required=True)
    parser.add_argument('--media', action='append', required=True, metavar='ID=NEW_PATH')
    parser.add_argument('--expected-sha256', action='append', default=[], metavar='ID=HEX')
    args = parser.parse_args()
    try:
        report = relink(args.project, args.output, pairs(args.media), pairs(args.expected_sha256))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
