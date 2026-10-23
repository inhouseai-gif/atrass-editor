#!/usr/bin/env python3
"""Recover a finite observed white phrase plus opaque rotated black backing.

Uses native decoded RGB images and a matching no-label clean plate. This is not
font recovery or original alpha recovery. Raster RGB is forced to neutral gray;
outside the constrained rotated backing alpha is zero, so no photographed RGB
region is copied. True author geometry/color/keyframes remain unknown.
"""
from pathlib import Path
import argparse,json,hashlib
import numpy as np,cv2
from PIL import Image

def recover(clean,plateau,roi):
    x,y,x2,y2=roi
    B=np.asarray(Image.open(clean).convert('RGB'),float)[y:y2,x:x2]
    F=np.asarray(Image.open(plateau).convert('RGB'),float)[y:y2,x:x2]
    core=((F.max(2)<25)&((B-F).mean(2)>15)).astype('uint8')
    contours,_=cv2.findContours(core,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if not contours: raise ValueError('No connected opaque black backing found')
    quad=cv2.boxPoints(cv2.minAreaRect(max(contours,key=cv2.contourArea)))
    poly=np.zeros(core.shape,np.uint8);cv2.fillConvexPoly(poly,np.round(quad).astype(np.int32),1)
    inner=cv2.erode(poly,np.ones((5,5),np.uint8)).astype(bool)
    outer=cv2.dilate(poly,np.ones((3,3),np.uint8)).astype(bool)
    # Black-over-clean estimate only at the backing edge; glyphs are inside.
    alpha=np.clip(np.sum((B-F)*B,axis=2)/np.maximum(np.sum(B*B,axis=2),1),0,1)
    alpha[~outer]=0;alpha[alpha<.04]=0;alpha[inner]=1
    gray=np.zeros(core.shape,float);gray[inner]=F.mean(2)[inner]
    gray[gray<5]=0;gray[gray>250]=255
    rgba=np.dstack([np.repeat(np.round(gray).astype('uint8')[:,:,None],3,axis=2),np.round(alpha*255).astype('uint8')])
    A=rgba[:,:,3].astype(float)/255;C=rgba[:,:,:3].astype(float)*A[:,:,None]+B*(1-A[:,:,None])
    band=outer&~inner;outside=~outer
    metrics={'plateau_recompose_mae_rgb_entire_roi':float(np.mean(abs(C-F))),
      'plateau_recompose_mae_rgb_inside_asset':float(np.mean(abs(C-F)[outer])),
      'plateau_recompose_mae_rgb_edge_band':float(np.mean(abs(C-F)[band])),
      'clean_plate_background_mae_rgb_outside_asset':float(np.mean(abs(B-F)[outside])),
      'nonzero_alpha_pixels':int(np.count_nonzero(A)),
      'opaque_alpha_pixels':int(np.count_nonzero(A==1)),
      'transparent_pixels':int(np.count_nonzero(A==0)),
      'rgb_channels_equal_everywhere':bool(np.all(rgba[:,:,0]==rgba[:,:,1]) and np.all(rgba[:,:,1]==rgba[:,:,2]))}
    return rgba,quad+np.array([x,y]),metrics,np.round(C).astype('uint8')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for k in ['clean','plateau','output','text']:ap.add_argument('--'+k,required=True)
    ap.add_argument('--roi',nargs=4,type=int,required=True)
    ap.add_argument('--clean-frame',type=int,required=True);ap.add_argument('--plateau-frame',type=int,required=True)
    ap.add_argument('--source-sha256',required=True);a=ap.parse_args()
    rgba,quad,m,_=recover(a.clean,a.plateau,a.roi);out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():raise ValueError('Output exists; choose a new path')
    Image.fromarray(rgba).save(out)
    rec={'status':'recovered_observed_phrase_and_backing_asset_not_font','text':a.text,
      'crop_bbox_1080':a.roi,'size_px':[a.roi[2]-a.roi[0],a.roi[3]-a.roi[1]],
      'source_video_id':'DvDeXNPS3Wc','source_video_sha256':a.source_sha256,
      'clean_frame':a.clean_frame,'plateau_frame':a.plateau_frame,
      'native_png_inputs':{'clean_sha256':hashlib.sha256(Path(a.clean).read_bytes()).hexdigest(),'plateau_sha256':hashlib.sha256(Path(a.plateau).read_bytes()).hexdigest()},
      'backing_quad_1080_fit':quad.tolist(),
      'method':'Largest connected native black backing fit; neutral-gray opaque glyph/backing interior; black-over-clean least-squares edge alpha confined to fitted backing +1 px; alpha below0.04 suppressed. The asset already contains the observed rotation.',
      'authored_rgba_known':False,'alpha_is_original':False,'font_recovered':False,
      'contains_background_rgb_region':False,'supports_new_text':False,
      'geometry_uncertainty_px':2,'quality':m,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
      'limitations':['Only this observed phrase is supported.','Compressed video and approximately matched background make alpha edges estimates.','Neutral gray removes residual chroma; this is not a bit-exact source asset.','Combined raster is a plateau reference. A white-glyph opacity ratio cannot be applied to the whole black/white raster in encoded RGB; use independently validated component curves. Do not rotate a second time.']}
    out.with_suffix('.provenance.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(out),'quality':m},ensure_ascii=False))
if __name__=='__main__':main()
