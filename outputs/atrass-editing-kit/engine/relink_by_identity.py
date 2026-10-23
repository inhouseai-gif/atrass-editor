#!/usr/bin/env python3
"""Relink every media alias with a declared SHA-256 to one verified local file.

Use from the kit root, for example:
  python engine/relink_by_identity.py projects/INPUT.json \
    --source SHA256=/local/source.mov --output projects/NEW.json

This is an identity-based wrapper around relink_project.py. It does not infer
identity from filenames, change timing, or approve the original edit.
"""
from pathlib import Path
import argparse
import json
import sys

from relink_project import relink, sha


def source_pairs(values):
    result = {}
    for value in values:
        digest, sep, path = value.partition('=')
        if not sep or not path:
            raise ValueError('Use SHA256=NEW_PATH for each --source')
        digest = sha(digest)
        if digest in result:
            raise ValueError('Duplicate source identity: ' + digest)
        result[digest] = path
    if not result:
        raise ValueError('At least one source identity is required')
    return result


def relink_by_identity(project, output, sources):
    # Normalize API callers too; reject equivalent duplicate mixed-case hashes.
    sources = source_pairs([str(key) + '=' + str(value) for key, value in sources.items()])
    original = json.loads(Path(project).read_text(encoding='utf-8'))
    if not isinstance(original, dict) or not isinstance(original.get('media'), dict):
        raise ValueError('A project media dictionary is required')
    groups = {digest: [] for digest in sources}
    for media_id, item in original['media'].items():
        if not isinstance(item, dict):
            raise ValueError('Media entries must be objects')
        declared = item.get('sha256')
        if declared is not None:
            declared = sha(declared)
            if declared in groups:
                groups[declared].append(media_id)
    missing = [digest for digest, ids in groups.items() if not ids]
    if missing:
        raise ValueError('No media declares requested SHA-256: ' + ', '.join(missing))
    replacements = {key: sources[digest] for digest, ids in groups.items() for key in ids}
    expected = {key: digest for digest, ids in groups.items() for key in ids}
    # The existing create-only implementation verifies replacement bytes, any
    # available old original, all unreplaced media, and font presence.
    report = relink(project, output, replacements, expected)
    report['identity_groups'] = [{'sha256': digest, 'media_ids': ids}
                                 for digest, ids in groups.items()]
    report['source_identity_inferred_from_filename'] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--source', action='append', required=True, metavar='SHA256=NEW_PATH')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = relink_by_identity(args.project, args.output, source_pairs(args.source))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
