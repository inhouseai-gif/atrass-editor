#!/usr/bin/env python3
"""Recover one observed white graphic phrase using aligned raw/reference pixels.

This is an analytical alpha estimate, not font recovery. RGB is forced to pure
white, so photographed reference background cannot enter the resulting asset.
"""
from pathlib import Path
from fractions import Fraction
import argparse,hashlib,json,sys
import numpy as np,cv2
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from render import SourceReader

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for s in ['raw','reference','map','output','text']:ap.add_argument('--'+s,required=True)
    ap.add_argument('--roi',nargs=4,type=int,required=True);ap.add_argument('--frames',required=True);a=ap.parse_args();out=Path(a.output)
    if out.exists():raise ValueError('Output exists; choose a new asset path')
    maps=json.loads(Path(a.map).read_text());lookup={}
    for c in maps['clips']:
        st=c['source_frame_time_base'];st=Fraction(st['num'],st['den']);rt=c['output_frame_time_base'];rt=Fraction(rt['num'],rt['den'])
        for i,r,s in zip(c['output_native_frame_indices'],c['output_frame_pts'],c['source_frame_pts']):lookup[i]=(float(r*rt),float(s*st),c['geometry'])
    refs=SourceReader(a.reference);raw=SourceReader(a.raw);alphas=[];records=[];x1,y1,x2,y2=a.roi
    try:
        for i in map(int,a.frames.split(',')):
            rt,st,g=lookup[i];r,_=refs.get(rt);b,_=raw.get(st);scale=g['scale'];mat=np.array([[scale,0,g['tx']],[0,scale,g['ty']]],np.float32);b=cv2.warpAffine(b,mat,(1920,1080),flags=cv2.INTER_AREA)
            R=r[y1:y2,x1:x2].astype(float);B=b[y1:y2,x1:x2].astype(float);white_delta=255-B
            alpha=np.sum((R-B)*white_delta,axis=2)/np.maximum(np.sum(white_delta**2,axis=2),1)
            alphas.append(np.clip(alpha,0,1));records.append({'reference_frame':i,'reference_seconds':rt,'raw_seconds':st})
    finally:refs.close();raw.close()
    alpha=np.median(alphas,axis=0);alpha[alpha<.06]=0
    # Compression/color conversion leaves a weak residual; require a connected
    # region with an opaque glyph core, retaining its anti-aliased border.
    n,labels,stats,_=cv2.connectedComponentsWithStats((alpha>.06).astype('uint8'),8)
    for k in range(1,n):
        mask=labels==k
        if stats[k,cv2.CC_STAT_AREA]<3 or alpha[mask].max()<.65:alpha[mask]=0
    rgba=np.full((y2-y1,x2-x1,4),255,np.uint8);rgba[:,:,3]=np.round(alpha*255).astype('uint8');out.parent.mkdir(parents=True,exist_ok=True);Image.fromarray(rgba).save(out)
    receipt={'status':'recovered_observed_phrase_asset_not_font','text':a.text,'reference_video_id':'DvDeXNPS3Wc','roi_1080':a.roi,'rgba_rgb':[255,255,255],'alpha_method':'median of aligned least-squares white-over-raw estimates; low compression residual removed; original source alpha unknown','sample_frames':records,'contains_photographed_background_rgb':False,'supports_new_text':False,'source_reference_asset_provenance_not_original_file':True,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'limitations':['Finite phrase only; cannot render arbitrary words in this handwriting.','Compressed reference/approximate raw match means alpha edges are an estimate.']}
    out.with_suffix('.provenance.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'asset':str(out),'size':[x2-x1,y2-y1],'nonzero_alpha_pixels':int(np.count_nonzero(alpha))},ensure_ascii=False))
if __name__=='__main__':main()
