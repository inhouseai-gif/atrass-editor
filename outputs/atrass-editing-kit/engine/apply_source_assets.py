#!/usr/bin/env python3
"""Attach explicit generated-image events to a compiled source-relative project.

This applies reviewed source times; it does not infer ASR meaning, choose assets,
edit audio or approve a style. New JSON and derived RGBA assets are create-only.
"""
from pathlib import Path
from fractions import Fraction
import argparse, copy, hashlib, json, math, os, re, sys, tempfile
from PIL import Image, ImageOps

SCHEMA = 'atrass-source-assets-v1'
POLICY = {'id': 'source-asset-layout-candidate-v1', 'scale_min': .25, 'scale_max': 4.,
          'xy_limit_canvas_multiples': 2., 'z_min': 1, 'z_max': 9,
          'status': 'Implementation safety bounds; not measured Atrass author settings',
          'motion_progress': 'Output-order cumulative selected frames, including quiet-split fragments, excluding unselected gaps',
          'fit': 'Uniform source resize with central cover crop or transparent contain padding; original alpha retained'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def keys(obj, allowed, required, label):
    require(isinstance(obj, dict), label + ' must be an object')
    require(set(obj) <= set(allowed), label + ' unknown fields: ' + ','.join(sorted(set(obj) - set(allowed))))
    require(set(required) <= set(obj), label + ' missing fields: ' + ','.join(sorted(set(required) - set(obj))))


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def pair(value, label):
    require(isinstance(value, list) and len(value) == 2 and all(number(x) for x in value), label + ' requires two finite numbers')
    return value


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def stat_signature(path):
    s=Path(path).stat()
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)


def read(path):
    data = Path(path).read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def path_at(root, value):
    require(isinstance(value, str) and bool(value), 'A nonempty path is required')
    return (root / value).resolve()


def relative(path, root):
    return Path(os.path.relpath(path, root)).as_posix()


def hash_literal(value, label):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), label + ' must be lowercase SHA256')


def frame_boundary(seconds, fps):
    require(number(seconds), 'Nonfinite clip boundary')
    f = round(seconds * float(fps))
    require(abs(f - seconds * float(fps)) <= 1e-6, 'Clip boundary is not on output CFR grid')
    return f


def resource_rebase(p, original_root, output_root):
    """Rebase runtime paths only; existing clips/audio/layers remain literal."""
    result = copy.deepcopy(p)
    changes = []
    for group in ('media', 'fonts'):
        for name, row in result.get(group, {}).items():
            old = row['path']
            target = path_at(original_root, old)
            require(target.is_file(), 'Missing existing resource: ' + str(target))
            row['path'] = relative(target, output_root)
            if row['path'] != old:
                changes.append({'group': group, 'id': name, 'from': old, 'to': row['path'], 'same_resolved_path': str(target)})
    return result, changes


def schedule(p, source_sha, audio_take_ids=None):
    fps = Fraction(p['fps'])
    require(fps > 0 and number(p['duration']) and p['duration'] > 0, 'Invalid project clock')
    require(type(p['width']) is int and type(p['height']) is int and p['width'] > 0 and p['height'] > 0, 'Invalid canvas')
    media_ids = {k for k, m in p['media'].items() if m.get('sha256') == source_sha and m.get('role') == 'source_footage' and m.get('type') != 'image'}
    require(media_ids, 'No source footage with the declared SHA')
    clips = sorted(p['clips'], key=lambda c: c['in'])
    require(len({c['id'] for c in clips}) == len(clips), 'Duplicate take ID')
    audios = p.get('audio', [])
    result = {}
    previous = 0
    for c in clips:
        a, b = frame_boundary(c['in'], fps), frame_boundary(c['out'], fps)
        require(a == previous and b > a, 'Base clip timeline gap/overlap')
        previous = b
        if c['source'] not in media_ids:
            continue
        require(number(c.get('source_in')) and c['source_in'] >= 0 and c.get('rate', 1) == 1, 'Source asset takes require finite rate1 source clock')
        require(not c.get('freeze_source') and c.get('source_frame_times') is None, 'Held/native-mapped take is outside source-asset scope')
        if audio_take_ids is not None and c['id'] not in audio_take_ids:
            # A later graphic pass may follow explicitly muted chapter cards.
            # Only named event takes require matching speech; all video clocks
            # are still checked, and every original audio entry stays literal.
            result[c['id']] = {'clip': c, 'frames': [a, b], 'source_range': [c['source_in'], c['source_in'] + (b - a) / float(fps)]}
            continue
        matches = [x for x in audios if x.get('source') == c['source'] and abs(x.get('in', -1) - c['in']) < 1e-9 and abs(x.get('out', -1) - c['out']) < 1e-9]
        require(len(matches) == 1, 'Source asset take needs one unambiguous matching voice piece: ' + c['id'])
        audio = matches[0]
        require(audio.get('rate', 1) == 1 and number(audio.get('source_in')) and abs(audio['source_in'] - c['source_in']) < 1e-9, 'Video/audio source clocks differ: ' + c['id'])
        result[c['id']] = {'clip': c, 'frames': [a, b], 'source_range': [c['source_in'], c['source_in'] + (b - a) / float(fps)]}
    require(previous == math.ceil(p['duration'] * float(fps) - 1e-9), 'Base clip duration mismatch')
    return fps, result


def event_spec(e, event_root, p, fps, takes, verified):
    keys(e, ['id','source_range','take_ids','asset','layout','motion'], ['id','source_range','take_ids','asset','layout'], 'event')
    require(isinstance(e['id'], str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,95}', e['id']), 'Event ID must be a safe unique identifier')
    source_a, source_b = pair(e['source_range'], 'source_range')
    require(0 <= source_a < source_b, 'Source range must be positive half-open')
    ids = e['take_ids']
    require(isinstance(ids, list) and ids and all(isinstance(x, str) for x in ids) and len(set(ids)) == len(ids), 'Explicit unique take_ids required')
    require(all(x in takes for x in ids), 'Unknown or unsupported take_id')
    fragments = []
    for ident in ids:
        m = takes[ident]
        c = m['clip']; lo, hi = m['frames']
        a = max(source_a, m['source_range'][0]); b = min(source_b, m['source_range'][1])
        require(a < b, 'Listed take has no source-range intersection: ' + ident)
        first = max(lo, math.ceil((lo / float(fps) + a - c['source_in']) * float(fps) - 1e-8))
        last = min(hi, math.ceil((lo / float(fps) + b - c['source_in']) * float(fps) - 1e-8))
        require(first < last, 'Source intersection collapses on CFR boundaries: ' + ident)
        fragments.append({'take_id': ident, 'source_intersection': [a,b], 'frames': [first,last],
                          'start_quantization_seconds': first / float(fps) - (c['in'] + a - c['source_in']),
                          'end_quantization_seconds': last / float(fps) - (c['in'] + b - c['source_in'])})
    fragments.sort(key=lambda x: x['frames'])
    # Repeated selected source occurrences have distinct explicit take IDs and are
    # allowed; omitted take_ids never silently acquire an event. Duplicated IDs fail.
    keys(e['asset'], ['path','sha256','provenance','role'], ['path','sha256','provenance'], 'asset')
    require(e['asset'].get('role','generated_source_event_candidate') in ('generated_source_event_candidate','licensed_source_photo_candidate'), 'Unsupported asset role')
    keys(e['asset']['provenance'], ['path','sha256'], ['path','sha256'], 'asset provenance')
    ap = path_at(event_root, e['asset']['path']); pp = path_at(event_root, e['asset']['provenance']['path'])
    for path, expected in ((ap,e['asset']['sha256']), (pp,e['asset']['provenance']['sha256'])):
        hash_literal(expected, 'resource sha')
        require(path.is_file(), 'Missing event asset/provenance: ' + str(path))
        if path not in verified:
            verified[path] = sha(path)
        require(verified[path] == expected, 'Asset/provenance SHA mismatch: ' + str(path))
    layout = e['layout']
    keys(layout, ['fit','position','size','z'], ['fit','position','size','z'], 'layout')
    require(layout['fit'] in ('cover','contain'), 'fit must be cover or contain')
    position = pair(layout['position'], 'center position'); size = pair(layout['size'], 'size')
    require(all(type(x) is int and 1 <= x <= 8192 for x in size), 'size requires integer dimensions1..8192')
    require(type(layout['z']) is int and 1 <= layout['z'] <= 9, 'Image z must be1..9, below source caption templates')
    require(all(layout['z'] < row.get('z',0) for row in p['layers'] if row.get('kind')=='caption'), 'New asset would not remain below existing dialogue captions')
    require(all(abs(v) <= 2 * p[k] for v,k in zip(position,('width','height'))), 'Center position exceeds declared two-canvas policy bounds')
    motion = e.get('motion', {'scale_start':1.,'scale_end':1.,'xy_start':[0.,0.],'xy_end':[0.,0.]})
    keys(motion, ['scale_start','scale_end','xy_start','xy_end'], ['scale_start','scale_end','xy_start','xy_end'], 'motion')
    require(all(number(motion[k]) and .25 <= motion[k] <= 4 for k in ('scale_start','scale_end')), 'Motion scale must be finite.25..4; zero/negative unsupported')
    for key in ('xy_start','xy_end'):
        xy = pair(motion[key], key)
        require(all(abs(v) <= 2 * p[k] for v,k in zip(xy,('width','height'))), 'Motion offset exceeds declared two-canvas bounds')
    with Image.open(ap) as image:
        require(image.width > 0 and image.height > 0 and getattr(image,'n_frames',1) == 1, 'Static nonempty source image required')
        original_size = list(image.size)
    return {'event': copy.deepcopy(e), 'asset_path': ap, 'provenance_path': pp, 'fragments': fragments,
            'motion': copy.deepcopy(motion), 'original_size': original_size,
            'selected_frames': sum(x['frames'][1]-x['frames'][0] for x in fragments)}


def fitted_image(path, size, mode):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGBA')
        if mode == 'cover':
            return ImageOps.fit(image, tuple(size), method=Image.Resampling.LANCZOS, centering=(.5,.5))
        scaled = ImageOps.contain(image, tuple(size), method=Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', tuple(size), (0,0,0,0))
        # Paste preserves source alpha; compositing would also preserve it against
        # transparent pixels, but no mask argument may apply alpha a second time.
        canvas.paste(scaled, ((size[0]-scaled.width)//2, (size[1]-scaled.height)//2))
        return canvas


def apply_assets(project, events, output):
    project, events, output = [Path(x).resolve() for x in (project,events,output)]
    require(not output.exists(), 'Output exists; choose a new project path')
    require(output.parent.is_dir(), 'Output parent must already exist')
    folder = output.with_name(output.stem + '.source-assets')
    require(not folder.exists(), 'Derived-asset directory exists; preserve it')
    p, psha = read(project); spec, specsha = read(events)
    keys(spec, ['schema','project_sha256','source_sha256','events','audio_clock_scope'], ['schema','project_sha256','source_sha256','events'], 'events document')
    require(spec['schema'] == SCHEMA, 'Unsupported source-assets schema')
    hash_literal(spec['project_sha256'], 'project_sha256'); hash_literal(spec['source_sha256'], 'source_sha256')
    require(spec['project_sha256'] == psha, 'Input project SHA mismatch')
    require(isinstance(spec['events'], list) and spec['events'], 'At least one explicit event required')
    require(len({x.get('id') for x in spec['events'] if isinstance(x,dict)}) == len(spec['events']), 'Duplicate/malformed event ID')
    scope = spec.get('audio_clock_scope','all_source_takes')
    require(scope in ('all_source_takes','event_takes_only'), 'Unsupported audio clock scope')
    audio_take_ids = None
    if scope == 'event_takes_only':
        require(all(isinstance(e,dict) and isinstance(e.get('take_ids'),list) and e['take_ids'] and all(isinstance(t,str) for t in e['take_ids']) for e in spec['events']), 'Explicit event take IDs required for scoped audio checks')
        audio_take_ids = {t for e in spec['events'] for t in e['take_ids']}
    fps, takes = schedule(p, spec['source_sha256'], audio_take_ids)
    source_paths = {path_at(project.parent, m['path']) for m in p['media'].values() if m.get('sha256') == spec['source_sha256'] and m.get('role') == 'source_footage'}
    require(len(source_paths) == 1, 'Source identity resolves to multiple files; relink explicitly first')
    raw = next(iter(source_paths))
    require(raw.is_file(), 'Source file missing')
    raw_stat=stat_signature(raw)
    require(sha(raw) == spec['source_sha256'] and stat_signature(raw)==raw_stat, 'Actual source SHA mismatch or source changed during hash')
    verified = {raw: spec['source_sha256']}
    prepared = [event_spec(e, events.parent, p, fps, takes, verified) for e in spec['events']]
    result, rebases = resource_rebase(p, project.parent, output.parent)
    existing_ids = {e['id'] for e in p['layers']}
    source_ids = set(p['media'])
    planned_ids = []
    for item in prepared:
        ident = item['event']['id']
        media_id = 'source-asset-' + ident
        require(media_id not in source_ids, 'Asset media ID collision')
        source_ids.add(media_id)
        for i in range(len(item['fragments'])):
            layer_id = 'source-asset-' + ident + '-part%03d' % (i+1)
            require(layer_id not in existing_ids and layer_id not in planned_ids, 'Layer ID collision')
            planned_ids.append(layer_id)
    # All validation is complete before the create-only output transaction begins.
    created = []
    directory_created = False
    temp_name = None
    try:
        folder.mkdir()
        directory_created = True
        journal = []
        for item in prepared:
            event = item['event']; ident = event['id']; layout = event['layout']; motion = item['motion']
            dest = folder / (ident + '.png')
            with dest.open('xb') as f:
                created.append(dest)
                fitted_image(item['asset_path'], layout['size'], layout['fit']).save(f, format='PNG')
            media_id = 'source-asset-' + ident
            result['media'][media_id] = {'path': relative(dest, output.parent), 'type': 'image', 'role': event['asset'].get('role','generated_source_event_candidate'),
                'sha256': sha(dest), 'provenance': relative(item['provenance_path'], output.parent),
                'provenance_sha256': event['asset']['provenance']['sha256'],
                'source_asset_path': relative(item['asset_path'], output.parent), 'source_asset_sha256': event['asset']['sha256'],
                'original_reference_asset_identity': False, 'fit': layout['fit'], 'source_size': item['original_size'],
                'derived_size': layout['size'], 'alpha_policy': 'Original image alpha and transparent contain padding retained'}
            cursor = 0
            for i, fragment in enumerate(item['fragments']):
                lo, hi = fragment['frames']; length = hi-lo
                denominator = max(1, item['selected_frames']-1)
                start_p, end_p = cursor/denominator, (cursor+length-1)/denominator
                def at(key, progress):
                    return motion[key+'_start'] + (motion[key+'_end']-motion[key+'_start']) * progress
                layer = {'id': 'source-asset-' + ident + '-part%03d' % (i+1), 'kind': 'image', 'source': media_id,
                         'in': float(Fraction(lo,1)/fps), 'out': float(Fraction(hi,1)/fps), 'anchor': 'center',
                         'position': list(layout['position']), 'z': layout['z'], 'status': 'explicit_source_asset_candidate_not_style_approved'}
                last_time = float(Fraction(length-1,1)/fps)
                scale0, scale1 = at('scale',start_p), at('scale',end_p)
                if scale0 != 1 or scale1 != 1:
                    layer['scale_keys'] = [[0.,scale0]] if length == 1 else [[0.,scale0],[last_time,scale1]]
                for axis,key in enumerate(('x_keys','y_keys')):
                    initial = layout['position'][axis] + motion['xy_start'][axis] + (motion['xy_end'][axis]-motion['xy_start'][axis])*start_p
                    final = layout['position'][axis] + motion['xy_start'][axis] + (motion['xy_end'][axis]-motion['xy_start'][axis])*end_p
                    if initial != layout['position'][axis] or final != layout['position'][axis]:
                        layer[key] = [[0.,initial]] if length == 1 else [[0.,initial],[last_time,final]]
                layer['source_asset_binding'] = {'event_id': ident, 'source_sha256': spec['source_sha256'],
                    'take_id': fragment['take_id'], 'requested_source_range': event['source_range'],
                    'source_intersection': fragment['source_intersection'], 'output_frames': [lo,hi],
                    'event_selected_frame_offset': cursor, 'event_selected_frames': item['selected_frames'],
                    'progress_endpoints': [start_p,end_p], 'events_sha256': specsha}
                result['layers'].append(layer)
                cursor += length
            journal.append({'id': ident, 'selected_frames': item['selected_frames'], 'fragments': item['fragments'],
                            'asset_sha256': event['asset']['sha256'], 'derived_sha256': result['media'][media_id]['sha256'],
                            'motion': motion, 'layout': layout})
        require(result['clips'] == p['clips'] and result.get('audio') == p.get('audio') and result.get('audio_master') == p.get('audio_master'), 'Internal error: source/audio mutated')
        require(result['layers'][:len(p['layers'])] == p['layers'], 'Internal error: existing layers changed')
        # Refuse mutation between read/verification and publish. The raw was hashed
        # once; other input bytes are inexpensive to recheck before publication.
        require(sha(project) == psha and sha(events) == specsha, 'Project/events changed during application')
        require(stat_signature(raw)==raw_stat, 'Source changed during asset application')
        for path, expected in verified.items():
            if path != raw:
                require(sha(path) == expected, 'Event asset/provenance changed during application')
        result['release_ready'] = False
        if 'source_asset_application' in p:
            result['source_asset_application_history'] = copy.deepcopy(p.get('source_asset_application_history',[])) + [copy.deepcopy(p['source_asset_application'])]
        result['source_asset_application'] = {'schema': SCHEMA, 'input_project_path': relative(project, output.parent),
            'input_project_sha256': psha, 'events_path': relative(events, output.parent), 'events_sha256': specsha,
            'source_sha256': spec['source_sha256'], 'implementation_sha256': sha(Path(__file__)),
            'policy': POLICY, 'events': journal, 'existing_resource_path_rebases': rebases,
            'audio_clock_scope': scope, 'audio_clock_checked_take_ids': sorted(audio_take_ids) if audio_take_ids is not None else sorted(takes),
            'preserved_literal_fields': ['clips','audio','audio_master','all_preexisting_layers'],
            'source_sha_verified_once': True, 'existing_project_mutated': False, 'render_completed': False,
            'render_completed_is_build_time_status': True, 'direct_listening_seconds': 0,
            'not_supported_here': ['ASR or story inference','automatic asset choice','NLE export or joining this new metadata without a compatible adapter','audio/video mismatched clocks','rate change','native source frame maps']}
        fd, temp_name = tempfile.mkstemp(prefix='.'+output.name+'.', suffix='.tmp', dir=output.parent)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.link(temp_name, output)  # Atomic create-only publish; never replaces a peer file.
        created.append(output)
        os.unlink(temp_name);temp_name=None
        return {'output': str(output), 'sha256': sha(output), 'events': len(journal), 'new_image_layers': len(result['layers'])-len(p['layers']),
                'derived_assets': len(journal), 'input_project_sha256': psha, 'release_ready': False, 'render_started': False}
    except BaseException:
        if temp_name is not None and Path(temp_name).exists():
            Path(temp_name).unlink()
        for path in reversed(created):
            if path.exists():
                path.unlink()
        if directory_created:
            try:
                folder.rmdir()
            except OSError:
                pass  # A peer-created file is never removed to force cleanup.
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('project','events','output'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(apply_assets(args.project,args.events,args.output),ensure_ascii=False,indent=2))
        return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
