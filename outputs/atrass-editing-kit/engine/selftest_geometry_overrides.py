#!/usr/bin/env python3
"""Real nine-record geometry fixtures and negative identity/provenance checks.

Build fixtures check project generation, not finished-video similarity. Temporary
raw/map files are existence-only test inputs and are never rendered as user work.
An optional --reference verifies the actual A file's SHA without editing it.
"""
from pathlib import Path
import argparse
import ast
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import tempfile
from types import SimpleNamespace

import build_reference_project as builder


def run(reference=None):
    kit=Path(__file__).resolve().parents[1]
    manifest=kit/'references/A-caption-geometry-overrides.json'
    captions=kit/'references/A-caption-timeline.json'
    original=json.loads(manifest.read_text(encoding='utf-8'))
    original_captions=json.loads(captions.read_text(encoding='utf-8'))
    checks=[];real_records=[]
    watched=[manifest,captions,kit/'engine/build_reference_project.py']
    before={str(p):builder.file_sha256(p) for p in watched}
    def check(name,fn):
        fn();checks.append({'name':name,'pass':True})
    def reject(fn,fragment):
        try:fn()
        except ValueError as exc:
            if fragment not in str(exc):raise AssertionError(str(exc))
            return
        raise AssertionError('Invalid fixture accepted: '+fragment)
    selected,binding,_=builder.load_geometry_overrides(manifest,captions,None,kit)
    assert len(selected)==9 and binding['reference_file_hash_checked'] is False
    check('real nine-record manifest binds exact caption bytes and evidence without reference file',lambda:None)
    assert binding['binding_method']=='caption_manifest_sha256_source_identity_and_packaged_evidence'
    check('no-reference build does not claim reference file hash verification',lambda:None)
    if reference:
        _,actual_binding,_=builder.load_geometry_overrides(manifest,captions,reference,kit)
        assert actual_binding['reference_file_hash_checked'] is True
        check('actual A media SHA verifies when explicitly supplied',lambda:None)
    else:actual_binding=None
    empty,binding0,cap0=builder.load_geometry_overrides(None,captions,None,kit)
    assert not empty and binding0 is None and cap0==original_captions
    check('absent option never auto-loads existing nine-record override file',lambda:None)
    function=next(n for n in ast.parse(watched[-1].read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='glyph_measurement')
    source=ast.get_source_segment(watched[-1].read_text(),function)+'\n'
    history=json.loads((kit/'references/A-caption-geometry-validation.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(source.encode()).hexdigest()==history['method']['current_function_sha256']
    check('historically audited glyph_measurement function remains byte-identical',lambda:None)
    with tempfile.TemporaryDirectory(prefix='atrass-geometry-') as temp:
        root=Path(temp);fixture_manifest=root/'override.json';fixture_captions=root/'captions.json'
        wrong_media=root/'wrong-reference.mkv';wrong_media.write_bytes(b'negative fixture: different video')
        check('wrong actual reference file rejected',lambda:reject(lambda:builder.load_geometry_overrides(manifest,captions,wrong_media,kit),'actual reference file SHA mismatch'))
        def negative(name,mutate,fragment,caption_mutate=None):
            data=copy.deepcopy(original);cap=copy.deepcopy(original_captions)
            if caption_mutate:
                caption_mutate(cap)
                fixture_captions.write_text(json.dumps(cap,ensure_ascii=False),encoding='utf-8')
                data['input_timeline_sha256']=builder.file_sha256(fixture_captions)
                cap_path=fixture_captions
            else:cap_path=captions
            mutate(data);fixture_manifest.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            check(name,lambda:reject(lambda:builder.load_geometry_overrides(fixture_manifest,cap_path,None,kit),fragment))
        negative('different override source SHA rejected',lambda d:d.update(source_sha256='0'*64),'source SHA mismatch')
        negative('different video ID rejected even with recomputed timeline hash',lambda d:None,'restricted to reference A',lambda d:d.update(video_id='X5r5quFzqpw'))
        negative('caption source SHA mismatch rejected',lambda d:None,'source SHA mismatch',lambda d:d.update(source_sha256='1'*64))
        negative('changed caption JSON bytes rejected',lambda d:d.update(input_timeline_sha256='2'*64),'timeline SHA mismatch')
        negative('changed exact Korean text rejected',lambda d:d['events'][0].update(text='외모도 괜찮고 성격도 나쁘지 않고!'),'text mismatch')
        negative('changed native event range rejected',lambda d:d['events'][0].update(event_frames=[194,325]),'event frame mismatch')
        negative('unknown event rejected',lambda d:d['events'][0].update(id='A-dialogue-absent'),'render-enabled dialogue')
        negative('duplicate override event rejected',lambda d:d['events'].append(copy.deepcopy(d['events'][0])),'Duplicate geometry override')
        negative('sample outside event rejected',lambda d:d['events'][0].update(sample_frame=324),'sample outside event')
        negative('PTS inconsistency rejected',lambda d:d['events'][0].update(source_pts_seconds=9.0),'PTS mismatch')
        negative('finite but unrelated PTS rejected',lambda d:d['events'][0].update(source_pts=9000,source_pts_seconds=9.0),'inconsistent with native sample')
        negative('glyph beyond selected bar rejected',lambda d:d['events'][0].update(glyph_bbox_1080=[590,954,1314,1002]),'glyph outside bar')
        negative('nonfinite geometry rejected',lambda d:d['events'][0].update(bar_bbox_1080=[float('nan'),938,1330,1018]),'Invalid bar bbox')
        negative('wrong glyph width rejected',lambda d:d['events'][0].update(glyph_width=1),'dimensions mismatch')
        negative('candidate promoted to exact rejected',lambda d:d['events'][0].update(bar_bbox_status='exact'),'candidate status missing')
        negative('missing edge uncertainty rejected',lambda d:d['events'][0].update(bar_edge_bounds_1080={}), 'edge uncertainty invalid')
        negative('selected edge outside uncertainty rejected',lambda d:d['events'][0]['bar_edge_bounds_1080'].update(right_exclusive=[1380,1384]),'edge uncertainty invalid')
        negative('tampered evidence digest rejected',lambda d:d['events'][0]['evidence'][0].update(sha256='0'*64),'evidence hash mismatch')
        negative('evidence directory traversal rejected',lambda d:d['events'][0]['evidence'][0].update(path='../outside.png'),'evidence escapes kit')
        negative('absolute evidence path rejected',lambda d:d['events'][0]['evidence'][0].update(path=str(wrong_media)),'kit-relative')
        negative('missing evidence rejected',lambda d:d['events'][0].update(evidence=[]),'evidence missing')
        negative('unreviewed target caption rejected',lambda d:None,'render-enabled dialogue',lambda d:next(e for e in d['events'] if e['id']=='A-dialogue-0003').update(render_enabled=False))

        raw=root/'raw-existence-only.mov';raw.write_bytes(b'fixture; intentionally never rendered')
        graphics=root/'graphics.json';graphics.write_text('{"events":[]}',encoding='utf-8')
        audio=root/'audio.json';audio.write_text('{"segments":[]}',encoding='utf-8')
        video_map=root/'map.json'
        hand_font=kit/'assets/fonts/SUIT-Variable.ttf'
        def build_fixture(record,with_override=True):
            start,end=record['event_frames'];lo,hi=builder.seconds(start),builder.seconds(end)
            video_map.write_text(json.dumps({'clips':[{'id':'fixture','output_in_seconds':lo,'output_out_seconds':hi,'offset_seconds':-lo,'geometry':{'scale':1,'tx':0,'ty':0},'status':'test_fixture_only'}]}),encoding='utf-8')
            out=root/(record['id']+('-selected.json' if with_override else '-default.json'))
            args=SimpleNamespace(raw=str(raw),video_map=str(video_map),audio_map=str(audio),captions=str(captions),graphics=str(graphics),hand_font=str(hand_font),output=str(out),reference=None,start_frame=start,end_frame=end,geometry_overrides=str(manifest) if with_override else None)
            with contextlib.redirect_stdout(io.StringIO()):builder.build(args)
            return json.loads(out.read_text(encoding='utf-8')),out
        for record in original['events']:
            project,project_path=build_fixture(record)
            layers=[x for x in project['layers'] if x['id']==record['id']];assert len(layers)==1
            layer=layers[0];assert layer['box']==record['bar_bbox_1080']
            assert layer['glyph_width']==record['glyph_width'] and layer['glyph_height']==record['glyph_height']
            assert layer['text_offset']==[record['glyph_bbox_1080'][0]-record['bar_bbox_1080'][0],record['glyph_bbox_1080'][1]-record['bar_bbox_1080'][1]]
            assert layer['geometry_override']['measurement']==record
            assert project['geometry_override_selection']['applied_event_ids']==[record['id']]
            assert project['release_ready'] is False and layer['status']=='reviewed_text_candidate_font'
            prov=layer['geometry_override'];base=(project_path.parent/prov['evidence_path_base']).resolve()
            for item in prov['measurement']['evidence']:assert builder.file_sha256(base/item['path'])==item['sha256']
            real_records.append({'id':record['id'],'bar_bbox_1080':layer['box'],'glyph_width':layer['glyph_width'],'glyph_height':layer['glyph_height'],'uncertainty_and_evidence_preserved':True})
            check('actual build preserves geometry, provenance and candidate status: '+record['id'],lambda:None)
        default,_=build_fixture(original['events'][0],False)
        assert 'geometry_override_selection' not in default and all('geometry_override' not in e for e in default['layers'])
        assert next(e for e in default['layers'] if e['id']=='A-dialogue-0003')['box']==[591,938,1382,1018]
        check('without option original fallback geometry and project shape remain unchanged',lambda:None)
        assert 'finite recovered rasters, not original source artwork' in ' '.join(default['limitations'])
        check('provenance limitation distinguishes raw footage from recovered graphics',lambda:None)
    assert before=={str(p):builder.file_sha256(p) for p in watched}
    check('all real inputs remain unchanged',lambda:None)
    return {'schema':'atrass-geometry-overrides-validation-v1','generated_at':datetime.now(timezone.utc).isoformat(),
            'status':'passed','check_count':len(checks),'checks':checks,'real_record_builds':real_records,
            'inputs_sha256':before,'glyph_measurement_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'actual_reference_hash_verified':actual_binding is not None,
            'reference_sha256':builder.A_REFERENCE_SHA256 if actual_binding else None,
            'source_without_reference_binding':binding,
            'limits':['Build fixtures validate generated JSON/provenance, not finished-video rendering or similarity.',
                      'Nine candidate boxes retain explicit uncertainty; no original coordinates/fonts or full-event constancy were certified.',
                      'No user media, caption timeline, or geometry override artifact was changed.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=run(args.reference)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'check_count':result['check_count'],'real_record_builds':len(result['real_record_builds']),'actual_reference_hash_verified':result['actual_reference_hash_verified']}))
