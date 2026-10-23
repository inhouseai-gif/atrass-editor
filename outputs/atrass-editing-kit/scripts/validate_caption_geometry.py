#!/usr/bin/env python3
"""Audit reviewed dialogue geometry with one native decode pass.

Only the sampled native frame per event is measured. This does not certify every
frame, original font identity, OCR wording, or authored bar opacity. The finished
video is read-only measurement input, never a source for a rendered reconstruction.
Dependencies: av, numpy, opencv-python-headless, Pillow.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

# Limit numerical workers before imports; decoder threads are separately bounded.
for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(_name, '1')
import av
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
cv2.setNumThreads(1)

LEGACY_SOURCE = '''def glyph_measurement(arr,e):
    ys=[940,943,946,1010,1013,1015]
    stable=(np.max(np.abs(arr[ys].astype(float)-17),axis=2)<12).sum(axis=0)>=5
    left=right=960
    while left>0 and stable[left-1]:left-=1
    while right<1919 and stable[right+1]:right+=1
    if right-left<40:raise ValueError('Caption bar could not be measured '+e['id'])
    box=[left,938,right+1,1018]
    c=arr[938:1018,left:right+1]
    mask=(c[:,:,0]>200)&(c[:,:,1]>200)&(c[:,:,2]>150)
    yy,xx=np.where(mask)
    if not len(xx):raise ValueError('Empty glyph mask '+e['id'])
    return box,[int(xx.min()),int(yy.min())],int(xx.max()-xx.min()+1),int(yy.max()-yy.min()+1)
'''


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def bind_function(source, filename):
    scope = {'np': np, 'cv2': cv2}
    exec(compile(source, filename, 'exec'), scope)
    return scope['glyph_measurement']


def read_current_function(builder):
    source = builder.read_text()
    tree = ast.parse(source, filename=str(builder))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name == 'glyph_measurement']
    if len(nodes) != 1:
        raise ValueError('Expected exactly one glyph_measurement function')
    body = ast.get_source_segment(source, nodes[0]) + '\n'
    return bind_function(body, str(builder)), body


def measurement(fn, arr, event):
    try:
        box, offset, width, height = fn(arr, event)
        return {'ok': True, 'bar_bbox_1080': box, 'glyph_offset': offset,
                'glyph_width': width, 'glyph_height': height,
                'glyph_bbox_1080': [box[0]+offset[0], box[1]+offset[1],
                                     box[0]+offset[0]+width,
                                     box[1]+offset[1]+height]}
    except (ValueError, IndexError) as exc:
        return {'ok': False, 'error': str(exc)}


def compare(row):
    old, new = row['legacy'], row['current']
    flags = []
    if not new['ok']:
        flags.append('current_function_exception')
    if not old['ok']:
        flags.append('legacy_function_exception')
    if old['ok'] and new['ok']:
        old_w = old['bar_bbox_1080'][2] - old['bar_bbox_1080'][0]
        new_w = new['bar_bbox_1080'][2] - new['bar_bbox_1080'][0]
        row['old_to_current_delta'] = {
            'bar_width_px': new_w-old_w,
            'glyph_width_px': new['glyph_width']-old['glyph_width'],
            'glyph_height_px': new['glyph_height']-old['glyph_height'],
            'left_edge_px': new['bar_bbox_1080'][0]-old['bar_bbox_1080'][0],
            'right_edge_px': new['bar_bbox_1080'][2]-old['bar_bbox_1080'][2]}
        if new_w-old_w > max(12, .05*new_w):
            flags.append('legacy_significant_bar_truncation_candidate')
        if new['glyph_width']-old['glyph_width'] > max(12, .05*new['glyph_width']):
            flags.append('legacy_significant_glyph_truncation_candidate')
        if old_w-new_w > max(12, .05*new_w):
            flags.append('current_significant_bar_contraction_candidate')
        if old['glyph_width']-new['glyph_width'] > max(12, .05*new['glyph_width']):
            flags.append('current_significant_glyph_contraction_candidate')
    observed = row.get('reviewed_bar_bbox_1080')
    if new['ok'] and observed:
        delta = [new['bar_bbox_1080'][i]-observed[i] for i in range(4)]
        row['current_minus_reviewed_bar_edges_px'] = delta
        if max(abs(delta[0]), abs(delta[2])) > max(12, .05*(observed[2]-observed[0])):
            flags.append('current_significant_reviewed_edge_disagreement')
    row['machine_flags'] = flags
    return row


def save_evidence(arr, row, cache, evidence, font):
    tag = f"{row['id']}_f{row['sample_frame']:06d}"
    crop = Image.fromarray(arr[918:1038])
    crop_path = cache / 'crops' / (tag+'.png')
    crop.save(crop_path)
    row['sample_crop'] = str(crop_path.resolve())
    if not row['machine_flags']:
        return
    context_path = evidence / (tag+'_context.png')
    Image.fromarray(arr).save(context_path)
    row['anomaly_native_context'] = str(context_path.resolve())
    panel = Image.new('RGB', (1920, 384), (240,240,240))
    panel.paste(crop, (0,90))
    panel.paste(crop, (0,258))
    d = ImageDraw.Draw(panel)
    d.text((16,4), f"{row['id']}  native {row['sample_frame']}  PTS {row['pts_seconds']:.3f}s", font=font, fill='black')
    d.text((16,30), ' / '.join(row['machine_flags']), font=font, fill='black')
    for result, origin_y, color, label in [
            (row['legacy'],90,'#FF6A34','OLD strict 5/6'),
            (row['current'],258,'#16BB59','CURRENT loaded builder function')]:
        if result['ok']:
            box = result['bar_bbox_1080']
            d.rectangle([box[0], origin_y+20, box[2]-1, origin_y+99], outline=color, width=2)
            b = result['glyph_bbox_1080']
            d.rectangle([b[0], origin_y+b[1]-918, b[2]-1, origin_y+b[3]-919], outline='#4992FF', width=1)
            label += f"  bar={box[2]-box[0]}  glyph={result['glyph_width']}x{result['glyph_height']}"
        else:
            label += '  '+result['error']
        d.text((16,origin_y-25),label,font=font,fill='black')
    panel_path=evidence/(tag+'_comparison.png')
    panel.save(panel_path)
    row['anomaly_comparison'] = str(panel_path.resolve())


def apply_review(report, journal_path):
    journal=json.loads(journal_path.read_text())
    if report.get('schema')!='atrass-caption-geometry-validation-v1':
        raise ValueError('Expected a caption geometry validation report')
    if journal['source_sha256']!=report['source_sha256']:
        raise ValueError('Review journal source mismatch')
    if journal.get('measurement_function_sha256_at_review') != report['method']['current_function_sha256']:
        raise ValueError('Review classifies a different measurement function; retain it as historical evidence')
    review={r['id']:r for r in journal['events']}
    if len(review)!=len(journal['events']):raise ValueError('Duplicate reviewed event')
    known={r['id'] for r in report['events']}
    if set(review)-known:raise ValueError('Review contains unknown event')
    for row in report['events']:
        if row['id'] in review:
            r=review[row['id']]
            if r['sample_frame']!=row['sample_frame']:raise ValueError('Review frame mismatch')
            row['direct_visual_review_status']=r['status'];row['direct_visual_review']=r
    report['coverage']['direct_visual_review_count']=len(review)
    report['coverage']['decoded_frames_visually_reviewed']=len({r['sample_frame'] for r in review.values()})
    report['review_journal']={'path':str(journal_path.resolve()),'sha256':sha(journal_path)}
    report['status']='machine_scan_complete_outliers_reviewed' if not report['coverage']['missing_event_ids'] else 'incomplete'
    report['review_classification_counts']=dict(Counter(r['classification'] for r in review.values()))
    report['accuracy_note']='Function returned without error is not a geometry accuracy pass. Directly reviewed classifications supersede machine candidate flags.'
    return report


def self_test(fn):
    arr=np.full((1080,1920,3),130,dtype=np.uint8)
    arr[938:1018,700:1220]=17
    arr[954:1002,716:1203]=[249,252,222]
    event={'id':'synthetic','layer':'dialogue','bar_bbox_1080':[700,938,1220,1018]}
    old=bind_function(LEGACY_SOURCE,'<legacy>')
    assert fn(arr,event)[0]==[700,938,1220,1018]
    assert old(arr,event)[0]==[700,938,1220,1018]
    assert fn(arr,event)[2:]==(487,48)
    noisy=arr.copy();noisy[[940,943],1084:1088]=50
    assert old(noisy,event)[0][2]==1084
    assert fn(noisy,event)[0][2]==1220
    gap=arr.copy();gap[list(range(941,948))+list(range(1008,1015)),1084:1086]=50
    assert fn(gap,event)[0][2]==1220
    conflict=dict(event,bar_bbox_1080=[800,938,1120,1018])
    assert not measurement(fn,arr,conflict)['ok']
    empty=arr.copy();empty[938:1018,700:1220]=17
    assert not measurement(fn,empty,event)['ok']
    row={'legacy':measurement(old,noisy,event),'current':measurement(fn,noisy,event),'reviewed_bar_bbox_1080':event['bar_bbox_1080']}
    assert 'legacy_significant_bar_truncation_candidate' in compare(row)['machine_flags']
    assert 'legacy_significant_glyph_truncation_candidate' in row['machine_flags']
    print(json.dumps({'self_test':'passed','assertions':10}))


def main():
    kit=Path(__file__).resolve().parents[1]
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kit-dir',type=Path,default=kit)
    ap.add_argument('--reference',type=Path)
    ap.add_argument('--captions',type=Path)
    ap.add_argument('--builder',type=Path)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--cache-dir',type=Path)
    ap.add_argument('--evidence-dir',type=Path)
    ap.add_argument('--review-journal',type=Path)
    ap.add_argument('--review-only',action='store_true',help='Attach a matching visual journal to the existing report without decoding again')
    ap.add_argument('--threads',type=int,default=3,choices=(1,2,3))
    ap.add_argument('--self-test',action='store_true')
    a=ap.parse_args();kit=a.kit_dir.resolve()
    output=(a.output or kit/'references/A-caption-geometry-validation.json').resolve()
    if a.review_only:
        if not a.review_journal:raise ValueError('--review-only requires --review-journal')
        report=apply_review(json.loads(output.read_text()),a.review_journal)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'output':str(output),'coverage':report['coverage'],'review_classification_counts':report['review_classification_counts']}))
        return 0
    captions=(a.captions or kit/'references/A-caption-timeline.json').resolve()
    builder=(a.builder or kit/'engine/build_reference_project.py').resolve()
    current,current_source=read_current_function(builder)
    if a.self_test:
        self_test(current);return 0
    data=json.loads(captions.read_text())
    reference=(a.reference or Path(data['source_media'])).resolve()
    cache=(a.cache_dir or output.parent/'caption-geometry-cache').resolve()
    evidence=(a.evidence_dir or kit/'evidence/A-caption-geometry').resolve()
    if output in (captions,builder,reference):
        raise ValueError('Output cannot overwrite an input')
    for p in (cache/'crops',evidence,output.parent):p.mkdir(parents=True,exist_ok=True)
    hashes={str(p):sha(p) for p in (reference,captions,builder)}
    if data.get('source_sha256') and hashes[str(reference)]!=data['source_sha256']:
        raise ValueError('Reference SHA256 does not match reviewed caption timeline')
    events=[e for e in data['events'] if e['layer']=='dialogue']
    targets=defaultdict(list)
    for e in events:
        f=min(e['in_frame']+12,e['out_frame']-1)
        if f<e['in_frame']:raise ValueError('Invalid native interval '+e['id'])
        targets[f].append(e)
    legacy=bind_function(LEGACY_SOURCE,'<legacy_strict_5_of_6>')
    font=ImageFont.load_default(size=17)
    rows=[];decoded=0;started=time.monotonic();last_progress=started
    with av.open(str(reference)) as container:
        stream=container.streams.video[0]
        stream.codec_context.thread_count=a.threads
        stream.codec_context.thread_type='AUTO'
        stream_info={'codec':stream.codec_context.name,'width':stream.width,'height':stream.height,'time_base':str(stream.time_base),'average_rate':str(stream.average_rate),'thread_count':a.threads}
        if (stream.width,stream.height)!=(1920,1080):
            raise ValueError('This A geometry audit requires native 1920x1080 media')
        for i,frame in enumerate(container.decode(stream)):
            decoded=i+1
            if i in targets:
                arr=frame.to_ndarray(format='rgb24')
                for e in targets[i]:
                    row={'id':e['id'],'text':e['text'],'event_frames':[e['in_frame'],e['out_frame']],
                         'sample_frame':i,'pts':frame.pts,'pts_seconds':float(frame.pts*frame.time_base),
                         'sample_offset_frames':i-e['in_frame'],'reviewed_bar_bbox_1080':e.get('bar_bbox_1080'),
                         'historical_ocr_bbox_1080':e.get('bbox_1080'),
                         'legacy':measurement(legacy,arr,e),'current':measurement(current,arr,e),
                         'direct_visual_review_status':'not_reviewed_in_this_audit'}
                    if not row['current']['ok']:
                        unguarded=dict(e);unguarded.pop('bar_bbox_1080',None)
                        row['current_without_reviewed_bbox_guard_diagnostic_only']=measurement(current,arr,unguarded)
                    compare(row);save_evidence(arr,row,cache,evidence,font);rows.append(row)
            if time.monotonic()-last_progress>20:
                print(json.dumps({'decoded_frames':decoded,'measured_events':len(rows),'target_events':len(events),'elapsed_seconds':round(time.monotonic()-started,1)}),flush=True)
                last_progress=time.monotonic()
    found={r['id'] for r in rows};missing=[e['id'] for e in events if e['id'] not in found]
    flags=Counter(f for r in rows for f in r['machine_flags'])
    report={'schema':'atrass-caption-geometry-validation-v1','generated_at':datetime.now(timezone.utc).isoformat(),
            'status':'machine_scan_complete' if not missing else 'incomplete',
            'source_media':str(reference),'source_sha256':hashes[str(reference)],
            'inputs_sha256':hashes,'decoder':stream_info,
            'method':{'sample_rule':'min(in_frame+12,out_frame-1), zero-based native decode index; PTS recorded separately',
                      'current_function_path':str(builder),'current_function_source':current_source,
                      'current_function_sha256':hashlib.sha256(current_source.encode()).hexdigest(),
                      'legacy_function_source':LEGACY_SOURCE,
                      'legacy_provenance':'Exact dialogue branch of prior builder: six rows [940,943,946,1010,1013,1015], deviation<12 in at least five; no morphology or reviewed-box guard.',
                      'large_difference_threshold':'greater than max(12 px,5% current width), independently for bar and glyph width',
                      'reviewed_bbox_note':'Historical bar bounds are first/native boundary measurements; small compression/edge differences allowed. Historical OCR glyph bounds are not exact ink bounds.'},
            'coverage':{'event_total':len(events),'machine_measured_events':len(rows),'unique_sample_frames':len({r['sample_frame'] for r in rows}),
                        'native_frames_decoded':decoded,'decoded_frames_visually_reviewed':0,'all_events_visually_reviewed_in_this_audit':False,
                        'direct_visual_review_count':0,'audio_listened_seconds':0,'missing_event_ids':missing},
            'summary':{'current_success':sum(r['current']['ok'] for r in rows),'legacy_success':sum(r['legacy']['ok'] for r in rows),
                       'success_field_meaning':'Returned without exception; not an accuracy approval.',
                       'flag_counts':dict(flags),'flagged_event_count':sum(bool(r['machine_flags']) for r in rows)},
            'events':rows,
            'limitations':['One native sample per event, not every caption frame.',
                          'A measurement difference is a candidate until directly inspected.',
                          'No text, timing, font, original opacity, or release approval is granted by this audit.',
                          'RGB17 bar measurement is output geometry evidence, not a new authored-opacity determination.'],
            'elapsed_seconds':round(time.monotonic()-started,3)}
    if a.review_journal:
        apply_review(report,a.review_journal)
    report['inputs_changed_during_run']=[p for p,h in hashes.items() if sha(p)!=h]
    report['snapshot_stable']=not report['inputs_changed_during_run']
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(output),'summary':report['summary'],'coverage':report['coverage'],'snapshot_stable':report['snapshot_stable']},ensure_ascii=False))
    return 0 if not missing else 1


if __name__=='__main__':
    sys.exit(main())
