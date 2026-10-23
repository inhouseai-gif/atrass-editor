#!/usr/bin/env python3
"""Exercise six real graphic records with Composer, plus rejecting bad inputs.

No raw footage/audio reconstruction is performed. Optional --reference supplies
native pixels only for geometry measurements. Temporary build media is an
existence-only fixture; preview images contain synthetic gray backgrounds.
"""
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone
import argparse,contextlib,copy,io,json,tempfile
import numpy as np
from PIL import Image,ImageDraw
import build_reference_project as b
from render import Composer,evaluate_keys

IDS=['A-other_graphic_text-0027','A-other_graphic_text-0028',
     'A-large_emphasis-0007','A-large_emphasis-0008','A-other_graphic_text-0030','A-large_emphasis-0009']

def run(reference=None,preview_dir=None):
    kit=Path(__file__).resolve().parents[1];source=kit/'references/A-layer-events.json'
    document=b.read(source);events={e['id']:e for e in document['events'] if e['id'] in IDS}
    checks=[];metrics={};previews=[]
    def check(name,condition=True):
        if not condition:raise AssertionError(name)
        checks.append({'name':name,'pass':True})
    def reject(name,fn,fragment):
        try:fn()
        except ValueError as exc:check(name,fragment in str(exc));return
        raise AssertionError('Accepted negative fixture: '+name)
    watched=[source,kit/'references/A-caption-timeline.json',kit/'engine/render.py',kit/'engine/character_reveal.py',kit/'engine/export_nle.py']
    watched+=sorted((kit/'projects').glob('A-chat*reconstruction*.json'))
    watched+=sorted((kit/'projects').glob('*40*.json'))
    before={str(p.relative_to(kit)):b.file_sha256(p) for p in watched}
    check('all six reviewed native-boundary records exist',len(events)==6 and all(e['render_enabled'] and e['text_verification']=='visually_verified_native_graphic_text' for e in events.values()))
    if reference:check('actual reference SHA matches A',b.file_sha256(reference)==b.A_REFERENCE_SHA256)
    for eid in IDS:
        fill,provenance=b.observed_fill(events[eid],document,kit,kit/'projects')
        check(eid+' resolves observed white',fill==[255,255,255])
        if provenance['method'].startswith('semantic'):
            check(eid+' retains actual palette record and hash',provenance['source_sha256']==b.file_sha256(kit/'evidence/A-palette-measurements.json') and provenance['record']['rgb_mode']==fill and not Path(provenance['source_path']).is_absolute())
    e=copy.deepcopy(events[IDS[2]]);e['render_hints']['fill_observed_rgb']=[251,252,253]
    check('explicit event observed RGB takes precedence',b.observed_fill(e,document,kit,kit)[0]==[251,252,253])
    e=copy.deepcopy(events[IDS[2]]);e['render_hints']['fill_role']='unknown'
    reject('unsupported color role rejected',lambda:b.observed_fill(e,document,kit,kit),'Unsupported measured fill role')
    d=copy.deepcopy(document);d['video_id']='X5r5quFzqpw'
    reject('A palette cannot be applied to B role',lambda:b.observed_fill(events[IDS[2]],d,kit,kit),'reference A source identity')
    d=copy.deepcopy(document);d['source_sha256']='0'*64
    reject('A palette cannot be applied to different source SHA',lambda:b.observed_fill(events[IDS[2]],d,kit,kit),'reference A source identity')
    start,end=b.seconds(14974),b.seconds(19061)
    line_event=events[IDS[0]];lo=b.boundary(line_event,'in');hi=b.boundary(line_event,'out')
    fill,pr=b.observed_fill(line_event,document,kit,kit)
    lines=b.multiline_graphic_layers(line_event,lo,hi,start,fill,pr)
    check('two rows preserve exact ordered text and independent IDs','\n'.join(x['text'] for x in lines)==line_event['text'] and len({x['id'] for x in lines})==2)
    check('two rows retain independent measured width and height',[(x['glyph_width'],x['glyph_height']) for x in lines]==[(221,73),(662,77)])
    check('two rows share half-open native lifetime',all(x['in']==lo-start and x['out']==hi-start for x in lines))
    e=copy.deepcopy(line_event);e['spans'][1]['text']='mismatch'
    reject('mismatched row text rejected',lambda:b.multiline_graphic_layers(e,lo,hi,start,fill,pr),'exactly match')
    e=copy.deepcopy(line_event);e['spans'][1]['bbox_1080'][1]=500
    reject('overlapping row boxes rejected',lambda:b.multiline_graphic_layers(e,lo,hi,start,fill,pr),'Overlapping')
    e=copy.deepcopy(line_event);e['spans'][1]['fill_role']='yellow'
    reject('mixed unimplemented span styles rejected',lambda:b.multiline_graphic_layers(e,lo,hi,start,fill,pr),'per-span style')
    e=copy.deepcopy(line_event);e['render_hints']['rotation_degrees_clockwise']=1
    reject('unimplemented rotated multiline rejected',lambda:b.multiline_graphic_layers(e,lo,hi,start,fill,pr),'multiline background/rotation')
    e=copy.deepcopy(line_event);e['render_hints']['opacity']={'kind':'per_character_reveal','duration':.5}
    reject('unsupported opacity effect cannot silently become static',lambda:b.multiline_graphic_layers(e,lo,hi,start,fill,pr),'Unsupported opacity kind')
    reject('malformed constant opacity rejected',lambda:b.apply_observed_opacity({'id':'fixture'},{'opacity':{'kind':'constant','value':2}},0),'Invalid constant opacity')
    reject('reversed LUT rejected',lambda:b.apply_observed_opacity({'id':'fixture'},{'opacity':{'kind':'observed_per_frame_lut','samples':[{'frame':2,'alpha':0},{'frame':1,'alpha':1}]}},0),'Unordered observed opacity')
    thought=events[IDS[1]];tlo,thi=b.boundary(thought,'in'),b.boundary(thought,'out')
    fill,pr=b.observed_fill(thought,document,kit,kit)
    fallback=b.rotated_graphic_layers(thought,tlo,thi,start,fill,pr)
    check('no-reference rotation keeps OCR approximation explicit','ocr_aabb_candidate_no_reference' in fallback[1]['geometry_provenance']['method'])
    check('OCR fallback overflow remains an explicit geometry warning',fallback[1]['geometry_provenance']['ink_box_contained_within_plate'] is False and bool(fallback[1]['geometry_provenance']['geometry_warning']))
    e=copy.deepcopy(thought);e['render_hints']['rotation_degrees_clockwise']=-1.094
    reject('opposite rotation conflicts with observed quad',lambda:b.rotated_graphic_layers(e,tlo,thi,start,fill,pr),'Quad conflicts')
    e=copy.deepcopy(thought);e.pop('bar_quad_1080_approx')
    reject('missing rotated plate geometry rejected',lambda:b.rotated_graphic_layers(e,tlo,thi,start,fill,pr),'measured quad')
    e=copy.deepcopy(thought);e['bar_quad_1080_approx']=[[x+2500,y] for x,y in e['bar_quad_1080_approx']]
    reject('off-canvas rotated quad rejected',lambda:b.rotated_graphic_layers(e,tlo,thi,start,fill,pr),'Out-of-canvas rotated quad')
    e=copy.deepcopy(thought);e['bbox_1080']=[168,264,824,328]
    reject('disconnected ink and plate rejected',lambda:b.rotated_graphic_layers(e,tlo,thi,start,fill,pr),'ink bbox conflicts')
    e=copy.deepcopy(thought);e['render_hints']['rotation_degrees_clockwise_approx']=-1.094
    reject('conflicting angle keys rejected',lambda:b.rotated_graphic_layers(e,tlo,thi,start,fill,pr),'Conflicting clockwise')
    e=copy.deepcopy(thought);e['render_hints']['opacity']={'kind':'constant','value':.5}
    reject('parent group fade is not duplicated onto overlapping components',lambda:b.rotated_graphic_layers(e,tlo,thi,start,fill,pr),'requires independent component measurements')
    e=copy.deepcopy(thought);e['render_hints']['opacity']={'kind':'observed_per_frame_lut','samples':[{'frame':15615,'alpha':0},{'frame':15619,'alpha':1}]}
    reject('parent group LUT requires measured component alpha',lambda:b.rotated_graphic_layers(e,tlo,thi,start,fill,pr),'requires independent component measurements')
    e=copy.deepcopy(line_event);e['render_hints']['opacity']={'kind':'observed_per_frame_lut','samples':[{'frame':15003,'alpha':0},{'frame':15007,'alpha':1}]}
    clipped_lo=b.seconds(15005)
    clipped=b.multiline_graphic_layers(e,clipped_lo,b.seconds(15010),clipped_lo,fill,pr)
    check('clipped LUT preserves native phase with linear interpolation',all(abs(evaluate_keys(x['opacity_keys'],0,1)-.5)<1e-8 for x in clipped))
    with tempfile.TemporaryDirectory(prefix='atrass-reference-graphics-') as tmp:
        root=Path(tmp);raw=root/'fixture.mov';raw.write_bytes(b'Existence-only fixture; never decoded or rendered.')
        def save(name,data):
            p=root/name;p.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');return str(p)
        args=SimpleNamespace(raw=str(raw),video_map=save('video.json',{'clips':[{'id':'fixture','output_in_seconds':start,'output_out_seconds':end,'offset_seconds':0,'geometry':{'scale':1},'status':'candidate'}]}),
                             audio_map=save('audio.json',{'segments':[]}),captions=save('captions.json',{'events':[]}),
                             graphics=save('graphics.json',{**document,'events':[events[k] for k in IDS]}),hand_font=str(kit/'assets/fonts/Gaegu-Regular.ttf'),
                             output=str(root/'project.json'),reference=reference,geometry_overrides=None,start_frame=14974,end_frame=19061)
        with contextlib.redirect_stdout(io.StringIO()):b.build(args)
        p=b.read(args.output);compiled=p['layers'];by_id={e['id']:e for e in compiled}
        check('actual build emits eight layers for six complete events',len(compiled)==8 and not p['omitted_events'])
        check('semantic coverage includes all six IDs',{e.get('semantic_layer_id',e['id']) for e in compiled}==set(IDS))
        check('draft remains unreleased and audio is not invented',p['release_ready'] is False and p['audio']==[])
        check('no reference image/video asset enters rendered media',set(p['media'])=={'rawA'} and p['media']['rawA']['role']=='source_footage')
        with contextlib.redirect_stdout(io.StringIO()):reject('existing project refuses overwrite',lambda:b.build(args),'Project exists')
        composer=Composer(p,root);blank=np.full((1080,1920,3),90,dtype=np.uint8)
        plate=by_id[thought['id']+'-backplate'];ink=by_id[thought['id']]
        check('rotated components use independent center anchors without global box',all(x['anchor']=='center' and x['rotation']==1.094 and 'box' not in x for x in [plate,ink]))
        check('backplate is below ink',plate['z']<ink['z'])
        plate_image,position=composer.image(plate,(plate['in']+plate['out'])/2)
        alpha=np.asarray(plate_image.getchannel('A'));ys,xs=np.where(alpha>250)
        coords=np.column_stack((xs+position[0],ys+position[1]));cov=np.cov(coords.T);eigval,eigvec=np.linalg.eigh(cov);direction=eigvec[:,np.argmax(eigval)]
        slope=float(direction[1]/direction[0]);center=coords.mean(axis=0);error=float(np.max(np.abs(center-np.array(plate['position']))))
        check('actual Composer rotates long edge clockwise',slope>0 and abs(np.degrees(np.arctan(slope))-1.094)<.1)
        check('actual expanded canvas retains plate center within one pixel',error<=1)
        metrics['rotation']={'measured_image_angle_degrees':float(np.degrees(np.arctan(slope))),'plate_center_error_px':error,'geometry':ink['geometry_provenance']}
        row_bounds=[]
        for layer in (by_id[x['id']] for x in lines):
            im,xy=composer.image(layer,(layer['in']+layer['out'])/2);bb=im.getchannel('A').getbbox();actual=[bb[0]+xy[0],bb[1]+xy[1],bb[2]+xy[0],bb[3]+xy[1]];expected=layer['geometry_provenance']['observed_bbox_1080']
            row_bounds.append(actual)
            check(layer['id']+' Composer bounds fit row without clipping',max(abs(a-b) for a,b in zip(actual,expected))<=2)
        check('actual two rows retain vertical gap',row_bounds[1][1]>row_bounds[0][3])
        metrics['multiline']={'actual_alpha_bounds_1080':row_bounds,'actual_gap_px':row_bounds[1][1]-row_bounds[0][3]}
        for eid in IDS:
            event=events[eid];subset=[x for x in compiled if x.get('semantic_layer_id',x['id'])==eid]
            n0,n1=event['in_frame'],event['out_frame']
            active=lambda frame:[x for x in subset if x['in']<=b.seconds(frame)-start<x['out']]
            check(eid+' actual frame selection is half-open',not active(n0-1) and len(active(n0))==len(subset) and len(active(n1-1))==len(subset) and not active(n1))
            arr=composer.compose(blank,subset,(subset[0]['in']+subset[0]['out'])/2)
            check(eid+' produces visible pixels via Composer',bool(np.any(arr!=blank)))
            if preview_dir:
                out=Path(preview_dir);out.mkdir(parents=True,exist_ok=True);path=out/(eid+'.png')
                if path.exists():raise ValueError('Preview exists; choose a new directory')
                Image.fromarray(arr).save(path);previews.append({'event_id':eid,'path':str(path),'sha256':b.file_sha256(path)})
        metrics['compiled_events']=[{'id':x['id'],'semantic_layer_id':x.get('semantic_layer_id',x['id']),'in':x['in'],'out':x['out'],'fill':x['fill'],'fill_provenance':x.get('fill_provenance'),'geometry_provenance':x.get('geometry_provenance')} for x in compiled]
    check('existing projects and renderer/source manifests remain byte-identical',before=={str(p.relative_to(kit)):b.file_sha256(p) for p in watched})
    return {'schema':'atrass-reference-graphics-implementation-validation-v1','created_utc':datetime.now(timezone.utc).isoformat(),
            'pass':True,'checks_passed':len(checks),'checks_failed':0,'checks':checks,'metrics':metrics,'preview_images':previews,
            'implementation_sha256':b.file_sha256(Path(b.__file__)),'selftest_sha256':b.file_sha256(Path(__file__)),
            'input_graphics_sha256':b.file_sha256(source),'palette_sha256':b.file_sha256(kit/'evidence/A-palette-measurements.json'),
            'reference_file_sha_checked':bool(reference),'reference_source_sha256':b.A_REFERENCE_SHA256,'preserved_file_hashes':before,
            'scope':'Six real event records; synthetic-gray Composer output and frame selection. No raw footage render, audio listening or NLE application execution.',
            'limitations':['Candidate fonts and measured/OCR geometry are not exact authored typography.','Palette fallback is a representative role observation, not per-event authored color.','Independent rotation layers assume the recorded static angle; original authoring transforms remain unknown.','Full 68-second source reconstruction and comparison belong to a separate run.'],
            'visual_review':{'status':'pending','scope':'Machine checks do not constitute visual review.'}}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reference');parser.add_argument('--output');parser.add_argument('--preview-dir');args=parser.parse_args()
    result=run(args.reference,args.preview_dir)
    if args.output:
        path=Path(args.output)
        if path.exists():raise ValueError('Validation exists; choose a new filename')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'pass':result['pass'],'checks_passed':result['checks_passed'],'checks_failed':0},ensure_ascii=False))
if __name__=='__main__':main()
