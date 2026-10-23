#!/usr/bin/env python3
"""Compare specified native reference/output frames; emit evidence, not approval."""
import argparse,json
from pathlib import Path
import av,numpy as np
from PIL import Image,ImageDraw

def frames(path,indices):
    c=av.open(str(path));s=c.streams.video[0];s.codec_context.thread_count=2;out={}
    for i,f in enumerate(c.decode(s)):
        if i in indices:out[i]=f.to_ndarray(format='rgb24')
        if i>=max(indices):break
    c.close()
    if set(indices)-set(out):raise ValueError('Missing frames: '+str(sorted(set(indices)-set(out))))
    return out

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--reference',required=True);ap.add_argument('--render',required=True);ap.add_argument('--reference-start-frame',type=int,default=0);ap.add_argument('--frames',required=True);ap.add_argument('--output-dir',required=True);ap.add_argument('--roi',action='append',default=[],help='Additional diagnostic ROI: name,x0,y0,x1,y1');ap.add_argument('--detail-roi',default='500,930,1420,1025');a=ap.parse_args()
    ids=sorted(set(map(int,a.frames.split(','))));out=Path(a.output_dir)
    if out.exists():raise ValueError('Choose a new comparison directory')
    out.mkdir(parents=True);refs=frames(a.reference,[i+a.reference_start_frame for i in ids]);renders=frames(a.render,ids);rows=[]
    rois={'face':[820,180,1120,520],'static_left_background':[30,50,180,350],'caption_band':[400,938,1520,1018],'left_question':[200,465,720,600],'large_emphasis':[420,795,1520,965]}
    for spec in a.roi:
        name,*coords=spec.split(',')
        if len(coords)!=4:raise ValueError('ROI requires name and four coordinates')
        rois[name]=list(map(int,coords))
    for name,(x0,y0,x1,y1) in rois.items():
        if not (0<=x0<x1<=1920 and 0<=y0<y1<=1080):raise ValueError('ROI outside 1080 canvas: '+name)
    dx0,dy0,dx1,dy1=map(int,a.detail_roi.split(','))
    if not (0<=dx0<dx1<=1920 and 0<=dy0<dy1<=1080):raise ValueError('Detail ROI outside 1080 canvas')
    detail_height=round(640*(dy1-dy0)/(dx1-dx0))
    for i in ids:
        r=refs[i+a.reference_start_frame];d=renders[i]
        if r.shape!=d.shape:raise ValueError('Frame dimensions differ')
        delta=np.abs(r.astype(float)-d.astype(float));m={'render_frame':i,'reference_frame':i+a.reference_start_frame,'whole_frame_mae_8bit':float(delta.mean()),'roi_mae_8bit':{k:float(delta[y1:y2,x1:x2].mean()) for k,(x1,y1,x2,y2) in rois.items()}}
        sheet=Image.new('RGB',(1920,max(500,435+detail_height)),'#142224');draw=ImageDraw.Draw(sheet)
        for col,(label,arr) in enumerate([('REFERENCE',r),('RAW RECONSTRUCTION',d),('ABS DIFFERENCE x4',np.minimum(delta*4,255).astype('uint8'))]):
            draw.text((col*640+8,8),f'{label} | reference f{m["reference_frame"]} | render f{i}',fill='white');sheet.paste(Image.fromarray(arr).resize((640,360)),(col*640,32));sheet.paste(Image.fromarray(arr[dy0:dy1,dx0:dx1]).resize((640,detail_height)),(col*640,405))
        fn=f'frame-{i:05d}.jpg';sheet.save(out/fn,quality=94);m['evidence']=fn;rows.append(m)
    receipt={'status':'measured_render_candidate_not_approved','reference_start_frame':a.reference_start_frame,'source_reference_passthrough':False,'roi_definitions':rois,'detail_roi':[dx0,dy0,dx1,dy1],'notes':['Pixel differences include encoding, font shape, mapping and unmodeled effects.','ROIs are diagnostic; neither low global error nor one matching frame certifies full style.'],'frames':rows}
    (out/'comparison.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n', encoding='utf-8');print(json.dumps({'frames':len(rows),'output':str(out),'face_mae_max':max(x['roi_mae_8bit']['face'] for x in rows)},ensure_ascii=False))
if __name__=='__main__':main()
