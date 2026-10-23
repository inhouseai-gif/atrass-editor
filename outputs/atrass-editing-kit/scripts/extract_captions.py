#!/usr/bin/env python3
"""Extract visible burned-in text; never substitute a speech transcript.

Python: PyAV, NumPy, Pillow. OCR: macOS Vision via vision_ocr.swift.
Times are decoded source PTS. Frame numbers are zero-based. Candidate
boundaries are pixel comparisons, not editorial or speech-cut decisions.
"""
from __future__ import annotations
import argparse, collections, difflib, hashlib, json, os, re, subprocess, sys
from pathlib import Path
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS', '1')
import av
import numpy as np
from PIL import Image

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    tmp.replace(path)

def bar_signature(gray):
    """A's regular caption bar: fixed y, variable x. Returns heuristic only."""
    h,w=gray.shape
    if (w,h)!=(1920,1080):
        gray=np.asarray(Image.fromarray(gray).resize((1920,1080)))
    edges=gray[[940,941,1015,1016],:].astype(float)
    row=np.median(edges,axis=0)
    dark=row<45
    if not dark[960]: return None, None
    left=960;right=960
    while left>0 and dark[left-1]:left-=1
    while right<1919 and dark[right+1]:right+=1
    flat=(row>=10)&(row<28)&(edges.std(axis=0)<7)
    if right-left>1880:
        # A dark B-roll can merge visually with the caption bar. The earlier
        # full-width rejection silently dropped 241.908-249.816s of reference A.
        # Prefer the flat-fill run there; this remains a candidate, not proof.
        if not flat[960]:return None,None
        left=right=960
        while left>0 and flat[left-1]:left-=1
        while right<1919 and flat[right+1]:right+=1
    if right-left<140 or right-left>1880:return None,None
    patch=gray[949:1009,left:right+1]
    glyph=(patch>175)
    if int(glyph.sum())<80:return None,None
    mask=np.zeros((30,960),dtype=bool)
    mask[:,left//2:(right+1)//2]=gray[949:1009:2,(left//2)*2:((right+1)//2)*2:2]>175
    # Tighter flat-fill bounds are separate from presence detection: compression
    # can perturb individual bar-edge rows and must not create false off states.
    measured_left,measured_right=left,right
    if flat[960]:
        measured_left=measured_right=960
        while measured_left>0 and flat[measured_left-1]:measured_left-=1
        while measured_right<1919 and flat[measured_right+1]:measured_right+=1
        if measured_right-measured_left<140:measured_left,measured_right=left,right
    return np.packbits(mask),[measured_left,938,measured_right+1,1018]

def extract(args):
    scratch=Path(args.scratch); frames=scratch/'frames';frames.mkdir(parents=True,exist_ok=True)
    records=[]; signatures=[]; next_sample=args.start; prev_sig=None; prev_present=False
    media_end=None;changes=[]
    with av.open(args.source) as container:
        stream=container.streams.video[0];stream.codec_context.thread_count=args.threads
        for idx,frame in enumerate(container.decode(stream)):
            t=float(frame.time)
            if t<args.start-.02:continue
            if t>args.end:media_end=t;break
            gray=frame.to_ndarray(format='gray')
            sig,bar=bar_signature(gray)
            present=sig is not None
            score=0.
            if present and prev_present:
                xor=np.unpackbits(np.bitwise_xor(sig,prev_sig)).sum()
                union=np.unpackbits(np.bitwise_or(sig,prev_sig)).sum()
                score=float(xor/max(1,union))
            if present!=prev_present or (present and score>args.change_threshold):
                changes.append({'frame':idx,'pts':frame.pts,'seconds':t,'bar_present':present,'difference':score,'bar_bbox_1080':bar})
            prev_sig=sig;prev_present=present
            signatures.append({'frame':idx,'pts':frame.pts,'seconds':t,'bar_present':present,'difference':round(score,5),'bar_bbox_1080':bar})
            if t+.009>=next_sample:
                dest=frames/f'f{idx:06d}_p{frame.pts:09d}.jpg'
                if not dest.exists(): frame.to_image().save(dest,quality=95,subsampling=0)
                records.append({'frame':idx,'pts':frame.pts,'seconds':t,'time_base':str(frame.time_base),'path':str(dest),'bar_bbox_1080':bar})
                next_sample+=1/args.sample_fps
            media_end=t+float(frame.duration or 0)*float(frame.time_base)
    write_json(scratch/'samples.json',records)
    write_json(scratch/'extraction-run.json',{'source':str(Path(args.source).resolve()),'start':args.start,'end':args.end,'sample_fps':args.sample_fps,'decoder_threads':args.threads,'last_native_frame_seconds':signatures[-1]['seconds'],'native_frames_scanned':len(signatures)})
    write_json(scratch/'native-bar-signatures.json',{'method':'A-specific luma bar mask and consecutive binary glyph difference','threshold':args.change_threshold,'frames':signatures,'candidates':changes})
    print(json.dumps({'phase':'extract','samples':len(records),'native_frames_scanned':len(signatures),'bar_change_candidates':len(changes),'media_end':media_end}),flush=True)
    return records,changes,signatures

def run_ocr(args,records):
    out=Path(args.scratch)/'ocr.jsonl'
    cached={}
    if out.exists():
        for line in out.read_text().splitlines():
            row=json.loads(line);cached[row.get('path')]=row
    todo=[r for r in records if r['path'] not in cached]
    if todo:
        helper=Path(args.ocr_binary)
        if not helper.exists():
            source=Path(__file__).with_name('vision_ocr.swift')
            subprocess.run(['swiftc','-O',str(source),'-o',str(helper)],check=True)
        p=subprocess.Popen([str(helper)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1)
        with out.open('a') as f:
            for i,r in enumerate(todo):
                p.stdin.write(json.dumps({'path':r['path']})+'\n');p.stdin.flush()
                line=p.stdout.readline()
                if not line:raise RuntimeError('OCR worker ended early')
                ocr=json.loads(line);ocr.setdefault('path',r['path'])
                f.write(json.dumps(ocr,ensure_ascii=False)+'\n');f.flush();cached[r['path']]=ocr
                if i%100==0:print(json.dumps({'phase':'ocr','done':i+1,'total':len(todo)}),flush=True)
        p.stdin.close();p.wait()
        if p.returncode:raise RuntimeError('OCR worker failed')
    return cached

def classify(row,bar):
    x,y,w,h=[float(v) for v in row['box']];box=[round(x*1920,2),round(y*1080,2),round((x+w)*1920,2),round((y+h)*1080,2)]
    # OCR's glyph box can be taller than the visible glyph. Keep original bbox.
    if bar and 650<=(x+w/2)*1920<=1270 and 950<=(y+h/2)*1080<=1015 and y*1080>=910 and (y+h)*1080<=1045 and h*1080<130:
        return 'dialogue',box
    if h*1080<25 or y*1080>1020:return None,box
    if y*1080>=760 and h*1080>85:return 'large_emphasis',box
    if x*1920<760 and 370<y*1080<720:
        return 'handwritten' if '?' in row['text'] else 'left_heading',box
    return 'other_graphic_text',box

def build(args,records,cached,changes,signatures):
    groups={}; active={};seq=collections.Counter();all_events=[];unresolved=[]
    for r in records:
        rows=cached[r['path']].get('rows',[]);states=collections.defaultdict(list)
        for row in rows:
            layer,box=classify(row,r['bar_bbox_1080'])
            if layer:states[layer].append({**row,'bbox_1080':box})
        current={}
        for layer,rows2 in states.items():
            rows2.sort(key=lambda q:(q['bbox_1080'][1],q['bbox_1080'][0]))
            text='\n'.join(q['text'].strip() for q in rows2)
            current[layer]=(text,rows2)
        for layer in set(active)|set(current):
            key=current.get(layer,('',[]))[0]
            old=active.get(layer)
            similar=old and key and difflib.SequenceMatcher(None,re.sub(r'\W|_','',old['text']),re.sub(r'\W|_','',key)).ratio()>=.80
            if old and (old['text']==key or (layer!='dialogue' and similar)):
                old['_samples'].append(r);old['_ocr'].extend(current[layer][1]);continue
            if old:old['_next_sample']=r;all_events.append(old);del active[layer]
            if key:
                seq[layer]+=1;active[layer]={'id':f'A-{layer}-{seq[layer]:04d}','layer':layer,'text':key,'_samples':[r],'_ocr':current[layer][1],'_previous_sample':records[records.index(r)-1] if records.index(r)>0 else None}
    all_events.extend(active.values())
    # A native glyph-mask interval is a more stable segmentation unit than OCR
    # output, which may briefly fail or change only whitespace in identical pixels.
    all_events=[e for e in all_events if e['layer']!='dialogue']
    intervals=changes+[{'frame':signatures[-1]['frame']+1,'seconds':min(args.end,signatures[-1]['seconds']+.017),'bar_present':False}]
    for a,b in zip(intervals,intervals[1:]):
        if not a['bar_present']:continue
        ss=[r for r in records if a['frame']<=r['frame']<b['frame']]
        if not ss:
            unresolved.append({'layer':'dialogue','in_frame':a['frame'],'out_frame':b['frame'],'in_seconds':a['seconds'],'out_seconds':b['seconds'],'reason':'bar-mask interval contains no sampled OCR frame','status':'unresolved_not_silence'})
            continue
        candidates=[]
        for r in ss:
            for row in cached[r['path']].get('rows',[]):
                layer,box=classify(row,r['bar_bbox_1080'])
                if layer=='dialogue':candidates.append({**row,'bbox_1080':box,'sample':r})
        if not candidates:
            unresolved.append({'layer':'dialogue','in_frame':a['frame'],'out_frame':b['frame'],'in_seconds':a['seconds'],'out_seconds':b['seconds'],'reason':'sampled frames have bar-mask but no qualifying dialogue OCR','status':'unresolved_not_silence'})
            continue
        counter=collections.Counter(re.sub(r'\s+',' ',q['text']).strip(' •') for q in candidates)
        text=counter.most_common(1)[0][0]
        good=[q for q in candidates if re.sub(r'\s+',' ',q['text']).strip(' •')==text]
        all_events.append({'id':f'A-dialogue-{len([e for e in all_events if e["layer"]=="dialogue"])+1:04d}','layer':'dialogue','text':text,'_samples':ss,'_ocr':good,'_native_in':a,'_native_out':b,'_previous_sample':None,'_next_sample':None,'ocr_alternatives':[{'text':t,'count':n} for t,n in counter.most_common(8)]})
    for ev in all_events:
        first,last=ev['_samples'][0],ev['_samples'][-1];prev=ev.get('_previous_sample');nxt=ev.get('_next_sample')
        start_lo=prev['seconds'] if prev else args.start; start_hi=first['seconds'];end_lo=last['seconds'];end_hi=nxt['seconds'] if nxt else min(args.end,signatures[-1]['seconds']+.017)
        ins=[c for c in changes if start_lo<c['seconds']<=start_hi+.001 and c['bar_present']]
        outs=[c for c in changes if end_lo<c['seconds']<=end_hi+.001]
        cin=ins[-1] if ev['layer']=='dialogue' and len(ins)==1 else None
        cout=outs[0] if ev['layer']=='dialogue' and len(outs)==1 else None
        if '_native_in' in ev:
            cin=ev['_native_in'];cout=ev['_native_out']
            start_lo=start_hi=cin['seconds'];end_lo=end_hi=cout['seconds']
        if first['frame']==0:cin={'frame':0,'seconds':0,'pts':0}
        boxes=[q['bbox_1080'] for q in ev['_ocr']]
        box=np.median(np.array(boxes),axis=0).round(2).tolist()
        bar_boxes=[s['bar_bbox_1080'] for s in ev['_samples'] if s['bar_bbox_1080']]
        bar_box=np.median(np.asarray(bar_boxes),axis=0).round().astype(int).tolist() if bar_boxes and ev['layer']=='dialogue' else None
        ev.update({'in_seconds':cin['seconds'] if cin else start_hi,'out_seconds':cout['seconds'] if cout else end_hi,'in_frame':cin['frame'] if cin else None,'out_frame':cout['frame'] if cout else None,'timing_status':'native_mask_candidate' if cin and cout else 'sample_bounds_only','timing_bounds_seconds':{'in':[start_lo,start_hi],'out':[end_lo,end_hi]},'bbox_1080':box,'bar_bbox_1080':bar_box,'font_size_approx_px':round(box[3]-box[1],1),'font_size_meaning':'OCR glyph bounding height, not font em or confirmed font size','text_verification':'machine_ocr_unreviewed','ocr_confidence_min':min(q['confidence'] for q in ev['_ocr']),'sample_count':len(ev['_samples']),'evidence':[{'frame':s['frame'],'pts':s['pts'],'seconds':s['seconds'],'path':s['path']} for s in [first,last]]})
        ev['review_required']=True;ev['render_enabled']=False
        ev['evidence_path_kind']='workspace_scratch_not_packaged; frame and PTS remain reproducible from source media'
        if cout and cout['frame']==signatures[-1]['frame']+1 and args.end<694.17:
            ev['out_frame']=None;ev['clipped_at_coverage_end']=True;ev['timing_status']='native_in_candidate_out_is_coverage_clip'
        if ev['layer']!='dialogue':
            state_votes=collections.Counter()
            states={}
            for sample in ev['_samples']:
                rows=[]
                for q in cached[sample['path']].get('rows',[]):
                    layer2,bbox2=classify(q,sample['bar_bbox_1080'])
                    if layer2==ev['layer']:rows.append({**q,'bbox_1080':bbox2})
                rows.sort(key=lambda q:(q['bbox_1080'][1],q['bbox_1080'][0]))
                key='\n'.join(q['text'] for q in rows)
                state_votes[key]+=1;states[key]=rows
            if state_votes:
                chosen=state_votes.most_common(1)[0][0];ev['text']=chosen
                ev['spans']=[{'text':q['text'],'bbox_1080':q['bbox_1080'],'ocr_confidence':q['confidence']} for q in states[chosen]]
                bs=[q['bbox_1080'] for q in states[chosen]]
                ev['bbox_1080']=[min(b[0] for b in bs),min(b[1] for b in bs),max(b[2] for b in bs),max(b[3] for b in bs)]
                ev['bbox_measurement']='union of OCR row boxes; exact visible glyph bounds unverified'
        for k in list(ev):
            if k.startswith('_'):del ev[k]
    with open(args.source,'rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    common={'version':'0.2','video_id':'DvDeXNPS3Wc','source_media':str(Path(args.source).resolve()),'source_sha256':sha,'source_kind':'finished_reference_burned_in_text','status':'machine_extraction_draft','coverage_seconds':[args.start,args.end],'frame_index_origin':0,'interval_convention':'half_open','coordinate_reference':[1920,1080],'ocr_engine':'Apple Vision VNRecognizeTextRequest ko-KR/en-US accurate languageCorrection=false','limits':['OCR is not manually verified unless event says so.','Native mask changes are caption-change candidates, not speech cuts.','Unresolved boundaries retain sample ranges; operational in/out use sampled upper bounds.','Other graphic text can contain scene text and must be reviewed before rendering.','No speech-transcript substitution.','Glyph height is not font em.'],'native_frames_scanned':len(signatures),'ocr_frames':len(records)}
    reviews=json.loads(Path(args.review_overrides).read_text()) if args.review_overrides else None
    if reviews:
        if reviews.get('source_sha256')!=sha:raise ValueError('review override media hash mismatch or missing hash')
        for correction in reviews['events']:
            hits=[e for e in all_events if e['layer']==correction['layer'] and ((e['in_frame'] is not None and e['in_frame']==correction['in_frame']) or (e['in_frame'] is None and abs(e['in_seconds']-correction.get('in_seconds',1e10))<.3))]
            if len(hits)!=1:
                unresolved.append({'reason':'review override did not match exactly one extracted event','override_layer':correction['layer'],'override_in_frame':correction['in_frame'],'matches':len(hits)})
                continue
            event=hits[0];event.update({k:v for k,v in correction.items() if k!='evidence'})
            event['review_evidence']=correction.get('evidence',[]);event['review_required']=correction.get('review_required',False);event['render_enabled']=correction.get('render_enabled',True)
            if event.get('in_frame') is not None:event['render_in_seconds_cfr_60000_1001']=event['in_frame']*1001/60000
            if event.get('out_frame') is not None:event['render_out_seconds_cfr_60000_1001']=event['out_frame']*1001/60000
        common['status']='partially_reviewed_extraction';common['review_overrides']=str(args.review_overrides)
    common['non_text_asset_layers_extracted']=False
    common['unresolved_layer_categories']=['B-roll/image identity and transforms','icons and emoji assets','chat panel/background shapes','chapter background textures','light overlays and other non-text effects']
    for name,layers in [('A-caption-timeline.json',{'dialogue'}),('A-layer-events.json',{'large_emphasis','handwritten','left_heading','other_graphic_text'})]:
        data={**common,'events':sorted([e for e in all_events if e['layer'] in layers],key=lambda q:(q['in_seconds'],q['layer'])),'unresolved_intervals':unresolved if 'dialogue' in layers else []}
        write_json(Path(args.output_dir)/name,data)
    print(json.dumps({'phase':'build','events':dict(collections.Counter(e['layer'] for e in all_events))}),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True);p.add_argument('--scratch',required=True);p.add_argument('--output-dir',required=True)
    p.add_argument('--start',type=float,default=0);p.add_argument('--end',type=float,default=694.177);p.add_argument('--sample-fps',type=float,default=4)
    p.add_argument('--threads',type=int,default=3,choices=[1,2,3]);p.add_argument('--change-threshold',type=float,default=.22)
    p.add_argument('--ocr-binary',default='work/caption-extract/vision_ocr');p.add_argument('--reuse-samples',action='store_true')
    p.add_argument('--extract-only',action='store_true')
    p.add_argument('--review-overrides',help='Apply human/agent visual corrections only when the source SHA256 matches.')
    p.add_argument('--replace-reviewed-output',action='store_true',help='Explicitly replace an existing reviewed output. Prefer a fresh output directory; OCR regeneration does not recreate later component splits or manual native timing review.')
    args=p.parse_args();scratch=Path(args.scratch)
    if not args.extract_only and not args.replace_reviewed_output:
        for name in ['A-caption-timeline.json','A-layer-events.json']:
            target=Path(args.output_dir)/name
            if target.exists():
                previous=json.loads(target.read_text())
                if any(e.get('render_enabled') for e in previous.get('events',[])) or previous.get('chat_composites'):
                    p.error(f'{target} contains reviewed data; use a fresh --output-dir. Raw extraction cannot recreate later manual component/opacity reviews. Explicit replacement requires --replace-reviewed-output.')
    if args.reuse_samples:
        run=json.loads((scratch/'extraction-run.json').read_text())
        if any(abs(float(run[k])-float(getattr(args,k)))>1e-5 for k in ['start','end','sample_fps']) or Path(run['source']).resolve()!=Path(args.source).resolve():
            raise ValueError('cached extraction source/range/rate differs; use the original arguments or a new scratch directory')
        records=json.loads((scratch/'samples.json').read_text());s=json.loads((scratch/'native-bar-signatures.json').read_text());changes=s['candidates'];signatures=s['frames']
    else:records,changes,signatures=extract(args)
    if not args.extract_only:
        cached=run_ocr(args,records);build(args,records,cached,changes,signatures)
if __name__=='__main__':main()
