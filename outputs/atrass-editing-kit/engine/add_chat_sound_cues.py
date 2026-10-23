#!/usr/bin/env python3
"""Add explicitly chosen B chat-entry sound cues to a new draft project.

This preserves source voice, cuts and visuals. It never infers arrivals from
ordinary captions or copies audio from a finished reference. Hearing and final
mix approval remain separate from measured waveform correspondence.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def compile_cues(project, decisions, profile, asset_path):
    if project.get('variant') != 'B' or decisions.get('variant') != 'B' or profile.get('variant') != 'B':
        raise ValueError('This measured cue profile is B-only; A is not approved')
    if decisions.get('schema') != 'atrass-chat-sound-decisions-v1':
        raise ValueError('Unsupported explicit sound decision schema')
    if profile.get('schema') != 'atrass-authored-chat-sound-profile-v1':
        raise ValueError('Unsupported sound profile schema')
    if sha(asset_path) != profile['asset']['sha256']:
        raise ValueError('Original library sound hash differs')
    events = decisions.get('events')
    if not isinstance(events, list) or not events:
        raise ValueError('Explicit new-message events are required')
    layers = {x['id']: x for x in project['layers']}
    if len(layers) != len(project['layers']):
        raise ValueError('Ambiguous duplicate visual layer ID')
    result = copy.deepcopy(project)
    audio = result.setdefault('audio', [])
    existing_ids = {x['id'] for x in audio}
    ids = set(); arrivals = set(); added = []
    media_id = 'authored-B-chat-notification-1'
    if media_id in project['media']:
        raise ValueError('Sound media already present; use the original pre-cue project')
    for e in events:
        if e.get('trigger') != 'chat_message_enter' or not isinstance(e.get('message_id'), str) or not e['message_id']:
            raise ValueError('Expected an explicit named new-message arrival')
        if e['message_id'] in ids or e.get('layer_id') not in layers:
            raise ValueError('Duplicate message or missing visual arrival layer')
        ids.add(e['message_id'])
        layer = layers[e['layer_id']]
        if layer['kind'] not in ('rect', 'chat', 'text', 'image'):
            raise ValueError('Unsupported visual arrival binding')
        offset = e.get('offset_seconds', 0)
        duration = e.get('duration_seconds', profile['candidate_duration_seconds'])
        gain = e.get('gain_db', profile['candidate_gain_db'])
        if not number(offset) or not -.2 <= offset <= .2:
            raise ValueError('Cue offset must be explicit and within200ms')
        if not number(duration) or not .1 <= duration <= profile['maximum_duration_seconds']:
            raise ValueError('Cue duration outside measured source range')
        if not number(gain) or not -60 <= gain <= 0:
            raise ValueError('Cue gain outside supported range')
        start = layer['in'] + offset; end = start + duration
        if not 0 <= start < end <= project['duration']:
            raise ValueError('Cue outside timeline; choose an explicit shorter duration')
        sample = round(start * 48000)
        if sample in arrivals:
            raise ValueError('Multiple decisions at the same arrival; do not duplicate layered cues')
        arrivals.add(sample)
        cue_id = 'chat-sfx-' + e['message_id']
        if cue_id in existing_ids:
            raise ValueError('Cue already exists')
        cue = {'id': cue_id, 'source': media_id, 'in': start, 'out': end,
               'source_in': 0, 'rate': 1, 'gain_db': gain,
               'fade_out': .02, 'status': 'draft_original_asset_signal_supported_listening_pending',
               'sound_event_binding': {'message_id': e['message_id'], 'layer_id': e['layer_id'],
                   'visual_entry_seconds': layer['in'], 'offset_seconds': offset,
                   'trigger': e['trigger']}}
        audio.append(cue); added.append(cue_id)
    result['media'][media_id] = {'path': str(asset_path), 'type': 'audio',
       'role': 'company_authored_library_sound', 'sha256': profile['asset']['sha256'],
       'provenance': 'Original library bytes, not a reference-mix residual. Private company handoff only.'}
    result['release_ready'] = False
    result['chat_sound_cues'] = {'profile_id': profile['id'], 'added_ids': added,
       'original_audio_entries_preserved': len(project.get('audio', [])),
       'listening_approved': False, 'reference_audio_passthrough': False,
       'gain_duration_fade_status': 'Candidate mix policy; not recovered original editor settings.'}
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'decisions', 'profile', 'output'):
        ap.add_argument('--' + name, required=True, type=Path)
    a = ap.parse_args()
    if a.output.exists():
        ap.error('Choose a new output path; existing projects are never overwritten')
    decisions = json.loads(a.decisions.read_text())
    if decisions.get('project_sha256') != sha(a.project):
        ap.error('Decision project SHA does not match the actual input')
    profile = json.loads(a.profile.read_text())
    asset = (a.profile.parent / profile['asset']['path']).resolve()
    project = json.loads(a.project.read_text())
    result = compile_cues(project, decisions, profile, asset)
    for group in ('media', 'fonts'):
        for entry in result.get(group, {}).values():
            p = Path(entry['path'])
            resolved = p if p.is_absolute() else (a.project.parent / p).resolve()
            entry['path'] = os.path.relpath(resolved, a.output.parent.resolve())
    result['chat_sound_cues'].update(project_sha256=sha(a.project),
       decisions_sha256=sha(a.decisions), profile_sha256=sha(a.profile))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x', encoding='utf8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
    print(json.dumps({'output': str(a.output), 'cues': len(result['chat_sound_cues']['added_ids']),
                      'release_ready': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
