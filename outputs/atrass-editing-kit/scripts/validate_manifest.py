#!/usr/bin/env python3
"""Validate one edit manifest using only the Python standard library.

Default: conservative release gate. --draft: structural audit; gaps remain warnings.
No media decoding, visual perception, listening, or similarity measurement is performed.
"""
import argparse
import hashlib
import json
import math
import re
import sys
from fractions import Fraction
from pathlib import Path

LAYERS = ('shot', 'caption', 'branding', 'motion', 'graphic', 'grade',
          'audio_dialogue', 'audio_music', 'audio_sfx', 'mapping')
UNKNOWN = {'unknown', 'unverified', 'tbd', 'todo', '미확인', '미정', '?'}


def schema_errors(value, spec, root, path='$'):
    """Implement exactly the JSON Schema keywords used by the bundled schema."""
    if '$ref' in spec:
        target = root
        for part in spec['$ref'][2:].split('/'):
            target = target[part]
        return schema_errors(value, target, root, path)
    errors = []
    for keyword in ('anyOf', 'oneOf'):
        if keyword in spec:
            branches = [schema_errors(value, sub, root, path) for sub in spec[keyword]]
            count = sum(not branch for branch in branches)
            if count == 0 or (keyword == 'oneOf' and count != 1):
                # An event's discriminator gives much more useful field errors.
                chosen = next((sub for sub in spec[keyword]
                               if isinstance(value, dict) and 'kind' in value
                               and sub.get('properties', {}).get('kind', {}).get('const') == value['kind']), None)
                errors.extend(schema_errors(value, chosen, root, path) if chosen else [f'{path}: {keyword} did not match'])
            return errors
    if 'const' in spec and value != spec['const']:
        errors.append(f'{path}: expected {spec["const"]!r}')
    if 'enum' in spec and value not in spec['enum']:
        errors.append(f'{path}: not in {spec["enum"]}')
    types = {'object': lambda x: isinstance(x, dict), 'array': lambda x: isinstance(x, list),
             'string': lambda x: isinstance(x, str), 'integer': lambda x: type(x) is int,
             'number': lambda x: type(x) in (int, float) and math.isfinite(x),
             'boolean': lambda x: type(x) is bool, 'null': lambda x: x is None}
    if 'type' in spec and not types[spec['type']](value):
        return errors + [f'{path}: expected {spec["type"]}']
    if isinstance(value, dict):
        props = spec.get('properties', {})
        errors.extend(f'{path}.{k}: missing required field' for k in spec.get('required', []) if k not in value)
        if spec.get('additionalProperties') is False:
            errors.extend(f'{path}.{k}: unknown field' for k in value if k not in props)
        for k in value.keys() & props.keys():
            errors.extend(schema_errors(value[k], props[k], root, f'{path}.{k}'))
    elif isinstance(value, list):
        if len(value) < spec.get('minItems', 0) or len(value) > spec.get('maxItems', math.inf):
            errors.append(f'{path}: invalid array length {len(value)}')
        if spec.get('uniqueItems') and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            errors.append(f'{path}: duplicate array item')
        for i, item in enumerate(value):
            errors.extend(schema_errors(item, spec.get('items', {}), root, f'{path}[{i}]'))
    elif isinstance(value, str):
        if len(value) < spec.get('minLength', 0) or ('minLength' in spec and not value.strip()):
            errors.append(f'{path}: empty string')
        if 'pattern' in spec and not re.search(spec['pattern'], value):
            errors.append(f'{path}: invalid format')
    elif type(value) in (int, float):
        if value < spec.get('minimum', -math.inf) or value > spec.get('maximum', math.inf):
            errors.append(f'{path}: outside numeric bounds')
        if 'exclusiveMinimum' in spec and value <= spec['exclusiveMinimum']:
            errors.append(f'{path}: must exceed {spec["exclusiveMinimum"]}')
    return errors


def walk(value, path='$', skip_disabled=False):
    if isinstance(value, dict):
        yield path, value
        if skip_disabled and value.get('enabled') is False:
            return
        for key, item in value.items():
            yield from walk(item, f'{path}.{key}', skip_disabled)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from walk(item, f'{path}[{i}]', skip_disabled)


def gaps(intervals, total):
    end, missing = 0, []
    for start, stop in sorted(intervals):
        if start > end:
            missing.append([end, start])
        end = max(end, stop)
    if end < total:
        missing.append([end, total])
    return missing


def pair(item):
    return item['interval']['start_frame'], item['interval']['end_frame']


def validate(data, schema, base, draft=False):
    errors, warnings = schema_errors(data, schema, schema), []
    if errors:
        return errors, warnings
    def pending(message):
        (warnings if draft else errors).append(message)
    def index(items, label):
        result = {}
        for item in items:
            if item['id'] in result:
                errors.append(f'{label}: duplicate id {item["id"]}')
            result[item['id']] = item
        return result
    media = index(data['media'], 'media')
    evidence = index(data['evidence'], 'evidence')
    colors = index(data['colors'], 'colors')
    events = index(data['events'], 'events')
    timeline, coverage = data['timeline'], data['coverage']
    total = timeline['frame_count'] or 0
    fps = Fraction(timeline['fps']['num'],timeline['fps']['den']) if timeline['fps'] else None
    if timeline['metadata_status']=='unverified' or any(timeline[k] is None for k in ('width','height','fps','time_base','frame_count')):
        pending(f'timeline: metadata incomplete ({timeline["metadata_reason"]})')
    hashes = {}
    def check_file(path, sha, label, size=None):
        file = Path(path) if Path(path).is_absolute() else base / path
        if not file.is_file():
            errors.append(f'{label}: missing file {file}')
            return
        if size is not None and file.stat().st_size != size:
            errors.append(f'{label}: byte_size mismatch')
        if sha is None:
            pending(f'{label}: sha256 unknown')
            return
        if file not in hashes:
            digest = hashlib.sha256()
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            hashes[file] = digest.hexdigest()
        if hashes[file] != sha:
            errors.append(f'{label}: sha256 mismatch')
    for ident, item in media.items():
        if item['path'] is None:
            pending(f'media.{ident}: local file unavailable ({item["reason"]})')
        else:
            check_file(item['path'], item['sha256'], f'media.{ident}', item['byte_size'])
        if item['byte_size'] is None:
            pending(f'media.{ident}: byte_size unknown')
        if item['video'] is None and item['audio'] is None and item['role'] in ('reference','raw_source','render'):
            pending(f'media.{ident}: audiovisual metadata missing')
    reference = media.get(timeline['reference_media_id'])
    if not reference or reference['role'] != 'reference':
        errors.append('timeline.reference_media_id: must identify reference media')
    elif reference['video']:
        for field in ('width','height','fps','time_base','frame_count','color'):
            a,b=reference['video'][field],timeline[field]
            if field in ('fps','time_base') and a and b:
                a,b=Fraction(a['num'],a['den']),Fraction(b['num'],b['den'])
            elif field=='color':
                a,b={k:v['value'] for k,v in a.items()},{k:v['value'] for k,v in b.items()}
            if a != b:
                errors.append(f'timeline.{field}: disagrees with reference metadata')
    for ident, item in evidence.items():
        check_file(item['path'], item['sha256'], f'evidence.{ident}')
        source = media.get(item['media_id'])
        if not source:
            errors.append(f'evidence.{ident}: unknown media_id')
        if item['frame'] is None and item['time_tick'] is None and item['time_seconds'] is None:
            errors.append(f'evidence.{ident}: frame, time_tick or time_seconds required')
        if item['time_seconds'] is not None and item['time_uncertainty_seconds'] is None:
            pending(f'evidence.{ident}: timestamp uncertainty unknown')
        if item['frame'] is not None and source and source['video'] and item['frame'] >= source['video']['frame_count']:
            errors.append(f'evidence.{ident}: frame outside source')
    for path, node in walk(data):
        for key in ('evidence_refs','replication_evidence_refs'):
            for ident in node.get(key, []):
                if ident not in evidence:
                    errors.append(f'{path}.{key}: unknown evidence {ident}')
        for key in ('asset_media_id','font_asset_media_id','look_asset_media_id','source_media_id'):
            ident = node.get(key)
            if ident is not None and ident not in media:
                errors.append(f'{path}.{key}: unknown media {ident}')
        for key in ('color_ids','fill_color_ids','sample_color_ids'):
            for ident in node.get(key, []):
                if ident not in colors:
                    errors.append(f'{path}.{key}: unknown color {ident}')
        if node.get('color_id') is not None and node['color_id'] not in colors:
            errors.append(f'{path}.color_id: unknown color {node["color_id"]}')
        if 'start_frame' in node and 'end_frame' in node:
            if not 0 <= node['start_frame'] < node['end_frame'] or total and node['end_frame'] > total:
                errors.append(f'{path}: invalid [start,end) frame interval')
    for path, node in walk(data, skip_disabled=True):
        if node.get('status') == 'unverified':
            pending(f'{path}: unverified')
        if node.get('status') in ('verified','observed','inferred') and 'evidence_refs' in node and not node['evidence_refs']:
            pending(f'{path}: declared status without evidence')
        if 'value' in node and (node['value'] is None or isinstance(node['value'], str) and node['value'].strip().lower() in UNKNOWN):
            pending(f'{path}: unknown value ({node.get("reason", "")})')
        if 'uncertainty' in node and node['uncertainty'] is None:
            pending(f'{path}: measurement uncertainty unknown')
        for field, length in {'position_px':2,'bounds_px':4,'offset_px':2,'padding_px':4,
                              'crop_ltrb_px':4,'reframe_position_px':2,'reframe_scale_xy':2,'anchor_px':2}.items():
            value = node.get(field, {}).get('value')
            if value is not None and len(value) != length:
                errors.append(f'{path}.{field}: expected {length} components')
    for ident, color in colors.items():
        if any(color[k] is None for k in ('sample_rgba','recipe_rgba','rgb_uncertainty_8bit','alpha_uncertainty_8bit')) or color['alpha_basis'] == 'unknown':
            pending(f'colors.{ident}: incomplete color/alpha measurement')
    for ident, event in events.items():
        start, stop = pair(event)
        detail = event['data']
        if event['kind'] == 'caption':
            text = detail['text']
            spans = sorted((s['char_start'], s['char_end']) for s in detail['spans'])
            if any(a >= b or b > len(text) for a,b in spans) or gaps(spans, len(text)):
                errors.append(f'events.{ident}: caption spans must cover every Unicode character')
            if any(spans[i][0] < spans[i-1][1] for i in range(1,len(spans))):
                errors.append(f'events.{ident}: overlapping caption spans')
            if any(x <= 0 or x >= len(text) for x in detail['line_breaks']):
                errors.append(f'events.{ident}: invalid line break position')
            for span in detail['spans']:
                if not span['font_candidates'] or not span['fill_color_ids']:
                    pending(f'events.{ident}: missing font candidates or text fill colors')
                if span['selected_font']['value'] and span['selected_font']['value'] not in [x['family'] for x in span['font_candidates']]:
                    errors.append(f'events.{ident}: selected font not among candidates')
                if span['font_asset_media_id'] is None:
                    pending(f'events.{ident}: font asset not portable')
                for part in ('stroke','shadow','bar'):
                    if span[part]['enabled'] and span[part]['color_id'] is None:
                        pending(f'events.{ident}.{part}: enabled component has unknown color')
        if event['kind'] == 'motion':
            offsets = [k['offset_frame'] for k in detail['keyframes']]
            if offsets != sorted(set(offsets)) or any(x >= stop-start for x in offsets):
                errors.append(f'events.{ident}: keyframes out of order or outside event')
            for target in detail['target_event_ids']:
                if target not in events:
                    errors.append(f'events.{ident}: missing motion target {target}')
            for key in detail['keyframes']:
                if key['interpolation'] == 'unknown' or key['interpolation'] == 'bezier' and key['bezier_xy'] is None:
                    pending(f'events.{ident}: unresolved interpolation')
        if event['kind'] == 'branding' and detail['asset_media_id'] is None and detail['text'] is None:
            pending(f'events.{ident}: logo/header/watermark content unknown')
        if event['kind'] == 'audio':
            expected = 'audio_' + detail['role']
            if detail['role'] != 'silence' and event['layer'] != expected:
                errors.append(f'events.{ident}: audio role/layer mismatch')
            if max(detail['fade_in_frames'],detail['fade_out_frames']) > stop-start:
                errors.append(f'events.{ident}: fade exceeds event')
        if event['kind'] == 'mapping':
            source = media.get(detail['source_media_id'])
            if detail['mapping_status']=='unverified':
                pending(f'events.{ident}: raw-source mapping is unverified')
            if detail['source_start_tick'] >= detail['source_end_tick']:
                errors.append(f'events.{ident}: source interval is empty/reversed')
            if detail['mapping_type'] == 'reference_identity' or not source or source['role'] != 'raw_source':
                pending(f'events.{ident}: no verified raw-source mapping')
            if source and source['video']:
                video = source['video']; tb = Fraction(video['time_base']['num'],video['time_base']['den'])
                source_duration = Fraction(video['frame_count']*video['fps']['den'],video['fps']['num'])
                if detail['source_end_tick'] * tb > source_duration + tb:
                    errors.append(f'events.{ident}: mapping exceeds source duration')
                if fps and detail['mapping_type'] in ('cut','retime','reference_identity'):
                    mapped = (detail['source_end_tick']-detail['source_start_tick'])*tb / Fraction(detail['speed']['num'],detail['speed']['den'])
                    if abs(mapped - Fraction(stop-start,1)/fps) > 1/fps:
                        errors.append(f'events.{ident}: mapping duration/speed mismatch')
    for frame in coverage['sampled_frames']:
        if total and frame >= total:
            errors.append('coverage.sampled_frames: frame outside timeline')
        if not any(e['frame']==frame and e['media_id']==timeline['reference_media_id'] and e['kind'] in ('frame','frame_sequence') for e in evidence.values()):
            errors.append(f'coverage.sampled_frames: frame {frame} lacks frame evidence')
    for channel in ('visual','audio'):
        ranges = coverage[f'{channel}_reviewed_ranges']
        missing = gaps([pair(x) for x in ranges], total)
        if missing:
            pending(f'coverage.{channel}: unreviewed intervals {missing[:8]}')
    if coverage['visual_review_mode'] != 'frame_by_frame':
        pending('coverage.visual: full-frame review not recorded; samples/playback are not full-frame review')
    if coverage['audio_review_mode'] != 'listened':
        pending('coverage.audio: full listening not recorded')
    for layer in LAYERS:
        ranges = [r for r in coverage['layer_ranges'] if r['layer'] == layer]
        ordered = sorted(pair(r) for r in ranges)
        if any(ordered[i][0] < ordered[i-1][1] for i in range(1,len(ordered))):
            errors.append(f'coverage.{layer}: overlapping classification intervals')
        if gaps([pair(r) for r in ranges], total):
            pending(f'coverage.{layer}: layer has uncovered timeline')
        for entry in ranges:
            a,b = pair(entry)
            referenced = [events[i] for i in entry['event_ids'] if i in events]
            if len(referenced) != len(entry['event_ids']):
                errors.append(f'coverage.{layer}: unknown event id')
            if any(e['layer'] != layer for e in referenced):
                errors.append(f'coverage.{layer}: event from wrong layer')
            if entry['presence'] == 'present' and gaps([(max(a,pair(e)[0])-a,min(b,pair(e)[1])-a) for e in referenced if pair(e)[0]<b and pair(e)[1]>a], b-a):
                errors.append(f'coverage.{layer}: present range not covered by its events')
            if entry['presence'] == 'absent' and any(e['layer']==layer and pair(e)[0]<b and pair(e)[1]>a for e in events.values()):
                errors.append(f'coverage.{layer}: absent claim conflicts with event')
            if entry['presence'] == 'unknown':
                pending(f'coverage.{layer}: unknown presence')
            if not entry['evidence_refs']:
                pending(f'coverage.{layer}: presence/absence claim lacks evidence')
        for ident,event in events.items():
            if event['layer'] != layer:
                continue
            a,b=pair(event)
            claims=[(max(a,pair(r)[0])-a,min(b,pair(r)[1])-a) for r in ranges
                    if r['presence']=='present' and ident in r['event_ids'] and pair(r)[0]<b and pair(r)[1]>a]
            if gaps(claims,b-a):
                pending(f'coverage.{layer}: event {ident} is not fully audited')
        if layer in ('shot','mapping') and gaps([pair(e) for e in events.values() if e['layer']==layer], total):
            pending(f'events.{layer}: must cover whole output timeline')
    if not coverage['design_color_inventory_complete']:
        pending('coverage: design color inventory incomplete')
    if data['analysis_status'] != 'ready_for_handoff' or data['purpose'] != 'reconstruction':
        pending('release: requires ready_for_handoff reconstruction, not a research draft')
    if not any(x['role']=='raw_source' for x in media.values()):
        pending('release: raw source absent')
    for item in data['uncertainties']:
        if item['state']=='open' or not item['resolution'] or not item['evidence_refs']:
            pending(f'uncertainties: unresolved {item["path"]}')
    checks = data['validation']
    if checks['replication_test'] != 'passed' or not checks['replication_evidence_refs'] or not checks['reviewer'] or not checks['comparison_criteria'] or any(not x['passed'] or x['measured'] is None for x in checks['comparison_criteria']):
        pending('release: replication comparison incomplete or failed')
    for check in checks['comparison_criteria']:
        if check['measured'] is not None:
            a,b=check['measured'],check['threshold']
            actual={'le':a<=b,'ge':a>=b,'eq':a==b}[check['operator']]
            if check['passed'] != actual:
                errors.append(f'validation.{check["metric"]}: passed flag contradicts threshold')
    if not any(evidence.get(i,{}).get('kind')=='render_comparison' for i in checks['replication_evidence_refs']):
        pending('release: render comparison evidence missing')
    if not any(x['role']=='render' for x in media.values()):
        pending('release: reconstructed render file absent')
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--draft', action='store_true', help='structure audit only; never certifies release')
    parser.add_argument('--base-dir', type=Path, help='file path root; default manifest parent')
    parser.add_argument('--schema', type=Path, default=Path(__file__).resolve().parents[1]/'schemas/edit-event.schema.json')
    args = parser.parse_args()
    try:
        schema = json.loads(args.schema.read_text(encoding='utf-8'))
        data = json.loads(args.manifest.read_text(encoding='utf-8'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('non-finite JSON number '+x)))
        errors,warnings = validate(data,schema,args.base_dir or args.manifest.parent,args.draft)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'result':'INVALID_INPUT','error':str(exc)},ensure_ascii=False,indent=2))
        return 2
    result = 'FAILED' if errors else 'DRAFT_STRUCTURE_VALID_NOT_RELEASED' if args.draft else 'RELEASE_RECORD_VALID'
    print(json.dumps({'result':result,'release_ready':not args.draft and not errors,'errors':errors,'warnings':warnings},ensure_ascii=False,indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
