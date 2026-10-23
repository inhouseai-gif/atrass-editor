#!/usr/bin/env python3
"""Independent visual source mapping for Atrass A (research, uncertainty preserved).
Dependencies: Python3.10+, av, numpy, Pillow; cv2 for geometry stage.
CPU threads capped at3. Native cache and map run as separate processes.
Examples:
  video_map.py cache --input RAW.mov --out work/video-map/raw --start 0 --end 120
  video_map.py cache --input FINAL.mkv --out work/video-map/final --start 0 --end 60
  video_map.py pilot --raw-cache work/video-map/raw --final-cache work/video-map/final --output map.json
"""
import os
for _name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS']:os.environ[_name]='3'
import argparse,json,time,hashlib,csv,sys
from pathlib import Path
import numpy as np
from PIL import Image
FEATURE_SPEC=[('face',(260,55,378,202),(20,24)),('microphone_hand',(217,208,320,266),(20,12))]
DIM=sum(w*h*3 for _,_,(w,h) in FEATURE_SPEC)
SPLIT=FEATURE_SPEC[0][2][0]*FEATURE_SPEC[0][2][1]*3

def feature(image):
 return np.concatenate([np.asarray(image.crop(box).resize(size,Image.Resampling.BILINEAR),dtype=np.uint8).reshape(-1) for _,box,size in FEATURE_SPEC])
def read_json(p):
 return json.loads(Path(p).read_text())
def save_json(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name('.'+p.name+'.'+str(os.getpid())+'.tmp');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n');tmp.replace(p)
def source_identity(path):
 p=Path(path);h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 st=p.stat()
 return {'source_path':str(p.resolve()),'source_size_bytes':st.st_size,'source_mtime_ns':st.st_mtime_ns,'source_sha256':h.hexdigest()}
def feature_for_row(final,row):
 k=row['final_cache_index']
 return final['alternative_features'][k] if row.get('geometry_choice')=='alternative' else final['features'][k]
def save_map_csv(path,clips):
 p=Path(path).with_suffix('.csv');p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name('.'+p.name+'.'+str(os.getpid())+'.tmp')
 with tmp.open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['id','output_in_seconds','output_out_seconds','source_in_seconds','source_out_seconds','scale','tx','ty','status','rmse_median_8bit','rmse_p95_8bit','indexed_frame_count'])
  for r in clips:w.writerow([r['id'],r['output_in_seconds'],r['output_out_seconds'],r['source_in_seconds'],r['source_out_seconds'],r['geometry']['scale'],r['geometry']['tx'],r['geometry']['ty'],r['status'],r['fit']['rmse_median_8bit'],r['fit']['rmse_p95_8bit'],len(r.get('source_frame_indices',[]))])
 tmp.replace(p)
def cache(a):
 import av
 from fractions import Fraction
 start=time.monotonic();out=Path(a.out);out.mkdir(parents=True,exist_ok=True);features=[];pts=[];times=[];nativeidx=[];decoded=0;count=0;geometry_indices=[];alt_geometry_indices=[];alt_features=[]
 georows=read_json(a.geometry_map)['rows'] if getattr(a,'geometry_map',None) else None
 geotimes=np.array([r['final_seconds'] for r in georows]) if georows else None
 with av.open(a.input) as c:
  v=c.streams.video[0];v.thread_type='AUTO';v.codec_context.thread_count=3
  tb=str(v.time_base);fps=str(v.average_rate);width=v.width;height=v.height
  if a.start>0:c.seek(int(a.start/v.time_base),stream=v,backward=True,any_frame=False)
  for f in c.decode(v):
   decoded+=1;t=float(f.time)
   if t<a.start:continue
   if a.end is not None and t>a.end:break
   im=f.reformat(width=640,height=360,format='rgb24',src_colorspace='ITU709',dst_colorspace='ITU709').to_image()
   if a.geometry:
    m=json.loads(a.geometry);data=(m[0][0]*4/3,m[0][1]*4/3,m[0][2]/3,m[1][0]*4/3,m[1][1]*4/3,m[1][2]/3)
    im=im.transform((640,360),Image.Transform.AFFINE,data,resample=Image.Resampling.BILINEAR)
   if georows:
    right=min(len(georows)-1,int(np.searchsorted(geotimes,t)));left=max(0,right-1);candidates=sorted(set([left,right]),key=lambda k:abs(geotimes[k]-t));gi=candidates[0];ai=candidates[-1]
    def warp_by(g):
     if g is None:return im
     m=np.array(g['matrix']);data=(m[0,0]*4/3,m[0,1]*4/3,m[0,2]/3,m[1,0]*4/3,m[1,1]*4/3,m[1,2]/3)
     return im.transform((640,360),Image.Transform.AFFINE,data,resample=Image.Resampling.BILINEAR)
    primary=feature(warp_by(georows[gi]['geometry']));g1=georows[gi]['geometry'];g2=georows[ai]['geometry'];different=(g1 is None)!=(g2 is None) or (g1 and g2 and (abs(g1['scale']-g2['scale'])>.02 or abs(g1['tx']-g2['tx'])>20 or abs(g1['ty']-g2['ty'])>20))
    alternative=feature(warp_by(g2)) if different else primary;ai=ai if different else gi;features.append(primary);alt_features.append(alternative);geometry_indices.append(gi);alt_geometry_indices.append(ai)
   else:features.append(feature(im))
   pts.append(f.pts);times.append(t);nativeidx.append(decoded-1 if a.start==0 else round(t*float(v.average_rate)))
   count+=1
   if count%6000==0:print(f'cached {count} frames through {t:.3f}s',flush=True)
 np.save(out/'features.npy',np.stack(features))
 if georows:
  np.save(out/'alternative-features.npy',np.stack(alt_features));np.save(out/'geometry-indices.npy',np.array(geometry_indices,np.int32));np.save(out/'alt-geometry-indices.npy',np.array(alt_geometry_indices,np.int32))
 np.save(out/'pts.npy',np.array(pts,np.int64));np.save(out/'seconds.npy',np.array(times,np.float64));np.save(out/'index-estimate.npy',np.array(nativeidx,np.int64))
 meta={'source_path':str(Path(a.input).resolve()),'source_size_bytes':Path(a.input).stat().st_size,'start_seconds':a.start,'end_requested':a.end,'cached_count':count,'decoded_count':decoded,'first_seconds':times[0],'last_seconds':times[-1],'time_base':tb,'declared_fps':fps,'native_resolution':[width,height],'feature_resolution':[640,360],'feature_spec':FEATURE_SPEC,'feature_dimension':DIM,'source_conversion':'PyAV BT709 RGB24 at640x360','frame_index_warning':'index-estimate derived from time*average_rate is NOT authoritative for VFR; use stored exactPTS and native enumeration lookup.','cpu_threads':3,'elapsed_seconds':time.monotonic()-start,'full_human_viewing':False}
 if georows:meta.update({'geometry_path':str(Path(a.geometry_map).resolve()),'geometry_index_path':str((out/'geometry-indices.npy').resolve()),'alternative_geometry_index_path':str((out/'alt-geometry-indices.npy').resolve()),'alternative_features_path':str((out/'alternative-features.npy').resolve()),'geometry_sha256':hashlib.sha256(Path(a.geometry_map).read_bytes()).hexdigest()})
 meta.update(source_identity(a.input));meta['feature_version']='rgb640-face20x24-hand20x12-v1';meta['frame_index_kind']='native_decode_order' if a.start==0 else 'estimate_from_average_rate';meta['frame_index_warning']='Native decode order from start0 is authoritative; seeked caches retain estimated indices, exact PTS authoritative.'
 save_json(out/'metadata.json',meta);print(json.dumps(meta,ensure_ascii=False),flush=True)

def load_cache(p):
 p=Path(p);meta=json.loads((p/'metadata.json').read_text());result={'meta':meta,'features':np.load(p/'features.npy',mmap_mode='r'),'pts':np.load(p/'pts.npy'),'times':np.load(p/'seconds.npy')}
 if meta.get('geometry_path'):
  geos=read_json(meta['geometry_path'])['rows'];result['geometry_rows']=[geos[int(k)] for k in np.load(meta['geometry_index_path'])] if meta.get('geometry_index_path') else geos
  if meta.get('alternative_geometry_index_path'):
   result['alternative_geometry_rows']=[geos[int(k)] for k in np.load(meta['alternative_geometry_index_path'])];result['alternative_features']=np.load(meta['alternative_features_path'],mmap_mode='r')
 return result
def scores(raw,target,roi='both'):
 diff=raw.astype(np.float32)-target.astype(np.float32)
 if roi=='robust_trimmed70':
  regional=[]
  for aa,bb in [(0,SPLIT),(SPLIT,DIM)]:
   err=(diff[:,aa:bb].reshape(len(diff),-1,3)**2).mean(axis=2);keep=max(1,int(err.shape[1]*.7));regional.append(np.partition(err,keep-1,axis=1)[:,:keep].mean(axis=1))
  return .5*(regional[0]+regional[1])
 face=np.mean(diff[:,:SPLIT]**2,axis=1);hand=np.mean(diff[:,SPLIT:]**2,axis=1)
 return face if roi=='face_only' else hand if roi=='hand_only' else .5*(face+hand)
def global_candidates(raw,coarse,target):
 both=scores(raw['features'][coarse],target);selected=list(coarse[np.argsort(both)[:6]])
 if float(np.min(both))**.5>4.5:
  for roi in ['face_only','hand_only','robust_trimmed70']:selected.extend(coarse[np.argsort(scores(raw['features'][coarse],target,roi))[:4]])
 return np.array(sorted(set(int(k) for k in selected)),dtype=int)
def match_query(raw,target,inds):
 face=scores(raw['features'][inds],target,'face_only');hand=scores(raw['features'][inds],target,'hand_only');combined=.5*(face+hand);vals=combined;roi='both';fi=int(np.argmin(face));hi=int(np.argmin(hand))
 if float(np.min(combined))**.5>4.5:
  options=[]
  if float(face[fi])**.5<3.5 and float(hand[fi])**.5>8:options.append((float(face[fi]),'face_only',face))
  if float(hand[hi])**.5<2.5 and float(face[hi])**.5>8:options.append((float(hand[hi]),'hand_only',hand))
  if options:_,roi,vals=min(options,key=lambda x:x[0])
  else:
   robust=scores(raw['features'][inds],target,'robust_trimmed70')
   if float(robust.min())**.5<3.5:roi='robust_trimmed70';vals=robust
 rank=np.argsort(vals);k=int(inds[rank[0]]);best=float(vals[rank[0]]);good=inds[vals<=max(best*1.15,best+.3)]
 return {'raw_cache_index':k,'raw_seconds':float(raw['times'][k]),'raw_pts':int(raw['pts'][k]),'rmse':best**.5,'matched_roi':roi,'comparison_warning':'Largest30% residual feature pixels in each region excluded; hidden pixels not verified' if roi=='robust_trimmed70' else None,'combined_face_hand_rmse':float(combined[rank[0]])**.5,'face_rmse_at_selected':float(face[rank[0]])**.5,'hand_rmse_at_selected':float(hand[rank[0]])**.5,'runnerup_ratio':float(vals[rank[1]]/max(best,1e-9)) if len(rank)>1 else None,'face_raw_cache_index':int(inds[fi]),'hand_raw_cache_index':int(inds[hi]),'raw_time_ambiguity_bounds':[float(raw['times'][good].min()),float(raw['times'][good].max())]}

def pilot(a):
 raw=load_cache(a.raw_cache);final=load_cache(a.final_cache);audio=read_json(a.audio)['rows'] if a.audio and Path(a.audio).exists() else [];rows=[];ranges=json.loads(a.ranges);rawtime=raw['times'];coarse=np.arange(0,len(rawtime),a.global_stride,dtype=int)
 # Global search independent of audio is always included. Audio only adds candidates.
 for i,t in enumerate(final['times']):
  if not any(lo<=t<hi for lo,hi in ranges):continue
  if a.step>1 and i%a.step:continue
  geo=final.get('geometry_rows',[None]*len(final['times']))[i]
  if geo is not None and geo['geometry'] is None:
   rows.append({'final_cache_index':i,'final_seconds':float(t),'final_pts':int(final['pts'][i]),'source_offset_seconds':None,'rmse':None,'status':'no_static_geometry_match','geometry':None});continue
  feat=final['features'][i];top=global_candidates(raw,coarse,feat);seeds=[float(rawtime[k]) for k in top];global_seed_count=len(seeds)
  local_audio=sorted(audio,key=lambda r:abs(r['final_start']-t))[:4]
  seeds.extend(float(t+r['offset']) for r in local_audio if r.get('correlation',0)>.8)
  inds=np.unique(np.concatenate([np.arange(np.searchsorted(rawtime,max(rawtime[0],s-a.seed_radius)),np.searchsorted(rawtime,min(rawtime[-1]+1e-8,s+a.seed_radius)),dtype=int) for s in seeds]));m=match_query(raw,feat,inds);m.update({'final_cache_index':i,'final_seconds':float(t),'final_pts':int(final['pts'][i]),'source_offset_seconds':m['raw_seconds']-float(t),'global_seed_seconds':seeds[:global_seed_count],'audio_seeds_seconds':seeds[global_seed_count:],'candidate_native_frames_scored':len(inds),'geometry':geo['geometry'] if geo is not None else {'scale':.75,'tx':0.,'ty':0.},'status':'visual_candidate' if m['rmse']<8 else 'visual_mismatch_or_nonperson'})
  rows.append(m)
  if len(rows)%300==0:print('matched',len(rows),'through',round(float(t),3),flush=True)
 save_json(a.output,{'status':'native_visual_samples_research','raw':raw['meta'],'final':final['meta'],'ranges':ranges,'sample_step_native_frames':a.step if final['meta'].get('sampling_kind') is None else None,'sampling_interval_seconds':final['meta'].get('nominal_sampling_interval_seconds'),'features':FEATURE_SPEC,'rows':rows,'limitations':['Native feature scores are machine evidence, not full human viewing.','Source/frame boundaries remain candidate until temporal segmentation and independent held-out checks.']})
 print('saved',a.output,'queries',len(rows),flush=True)

def geometry(a):
 import cv2
 cv2.setNumThreads(3)
 start=time.monotonic();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 anchor=Image.open(a.raw_anchor).convert('RGB').resize((640,360),Image.Resampling.BILINEAR);ar=np.array(anchor)
 mask=np.zeros((360,640),np.uint8)
 for l,t,r,b in [(.05,.05,.27,.76),(.78,.05,.995,.76),(.10,0,.72,.15)]:mask[int(t*360):int(b*360),int(l*640):int(r*640)]=255
 sift=cv2.SIFT_create(nfeatures=2500,contrastThreshold=.012);ka,da=sift.detectAndCompute(cv2.cvtColor(ar,cv2.COLOR_RGB2GRAY),mask);bf=cv2.BFMatcher(cv2.NORM_L2)
 samples=read_json(a.sample_pts)['samples'];rows=[];feat=[]
 for j,q in enumerate(samples):
  path=Path(a.frames)/f'frame_{q["frame"]:06d}.jpg';im=Image.open(path).convert('RGB');im=im.resize((640,360),Image.Resampling.BILINEAR);b=np.array(im);kb,db=sift.detectAndCompute(cv2.cvtColor(b,cv2.COLOR_RGB2GRAY),None);row={'sample_index':j,'frame_file':str(path),'final_pts':q['source_pts'],'final_seconds':q['source_seconds'],'status':'no_static_geometry_match','geometry':None};good=[]
  if db is not None and len(db)>1:
   fw=[x for x,y in bf.knnMatch(da,db,k=2) if x.distance<.72*y.distance];back={(x.trainIdx,x.queryIdx) for x,y in bf.knnMatch(db,da,k=2) if x.distance<.72*y.distance};good=[m for m in fw if (m.queryIdx,m.trainIdx) in back]
  if len(good)>=6:
   src=np.float32([ka[m.queryIdx].pt for m in good]);dst=np.float32([kb[m.trainIdx].pt for m in good]);M,keep=cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=1.2,maxIters=2500,confidence=.995,refineIters=20)
   if M is not None:
    valid=keep[:,0].astype(bool);scale=float(np.hypot(M[0,0],M[1,0])*.75);rot=float(np.degrees(np.arctan2(M[1,0],M[0,0])));err=np.linalg.norm(src@M[:,:2].T+M[:,2]-dst,axis=1);row.update({'mutual_matches':len(good),'inliers':int(valid.sum()),'median_inlier_error_640px':float(np.median(err[valid])),'rotation_degrees':rot})
    if valid.sum()>=7 and .60<=scale<=1.40 and abs(rot)<1 and np.median(err[valid])<.8:
     nativeM=np.column_stack([M[:,:2]*.75,M[:,2]*3]);row['status']='static_geometry_observed';row['geometry']={'scale':scale,'tx':float(M[0,2]*3),'ty':float(M[1,2]*3),'matrix':nativeM.tolist()}
     # PIL output canonical raw640 pixels -> input final640 coordinates uses the forward small-frame transform.
     data=tuple(M.reshape(-1));im=im.transform((640,360),Image.Transform.AFFINE,data,resample=Image.Resampling.BILINEAR)
  feat.append(feature(im));rows.append(row)
  if (j+1)%400==0:print('geometry',j+1,'matched',sum(r['geometry'] is not None for r in rows),flush=True)
 np.save(out/'features.npy',np.stack(feat));np.save(out/'pts.npy',np.array([r['final_pts'] for r in rows],np.int64));np.save(out/'seconds.npy',np.array([r['final_seconds'] for r in rows],float))
 save_json(out/'geometry.json',{'method':'SIFT static bookcase raw anchor, reciprocal ratio.72, RANSAC1.2px at640; no audio input','rows':rows,'elapsed_seconds':time.monotonic()-start})
 save_json(out/'metadata.json',{'source_path':str(Path(a.reference).resolve()),'feature_resolution':[640,360],'native_resolution':[1920,1080],'cached_count':len(rows),'raw_anchor_sha256':hashlib.sha256(Path(a.raw_anchor).read_bytes()).hexdigest(),'sample_pts_sha256':hashlib.sha256(Path(a.sample_pts).read_bytes()).hexdigest(),'sampling_kind':'nearest_native_to4fps','nominal_sampling_interval_seconds':.25,'sample_pts_path':str(Path(a.sample_pts).resolve()),'geometry_path':str((out/'geometry.json').resolve()),'feature_spec':FEATURE_SPEC,'feature_dimension':DIM,'full_human_viewing':False,**source_identity(a.reference)})
 print('geometry complete',len(rows),sum(r['geometry'] is not None for r in rows),flush=True)

def segment(a):
 d=read_json(a.matches);raw=load_cache(a.raw_cache);final=load_cache(a.final_cache);rows=d['rows'];clips=[];diagnostics=[]
 # Each requested span is independent; B-roll and unmatched gaps never become raw trims by interpolation.
 for lo,hi in d['ranges']:
  rr=[r for r in rows if lo<=r['final_seconds']<hi]
  if not rr:continue
  dt=d.get('sampling_interval_seconds') or 1/60;half=max(1,round(.12/dt));radius=max(1,round(.58/dt))
  t=np.array([r['final_seconds'] for r in rr]);offs=np.array([r['source_offset_seconds'] for r in rr]);rmse=np.array([r['rmse'] for r in rr]);good=rmse<8
  smooth=np.array([np.median(offs[max(0,i-half):min(len(offs),i+half+1)][good[max(0,i-half):min(len(offs),i+half+1)]]) if np.any(good[max(0,i-half):min(len(offs),i+half+1)]) else np.nan for i in range(len(offs))])
  runs=[];start=0;center=smooth[0]
  for i in range(1,len(rr)):
   if np.isnan(smooth[i]) or np.isnan(center):
    if np.isnan(smooth[i])!=np.isnan(center):runs.append((start,i));start=i;center=smooth[i]
   elif abs(smooth[i]-center)>.085:
    runs.append((start,i));start=i;center=smooth[i]
   else:center=float(np.median(smooth[start:i+1]))
  runs.append((start,len(rr)))
  # Merge tiny runs into adjacent run only when visual offset is compatible; otherwise retain uncertain interval.
  levels=[float(np.nanmedian(smooth[x:y])) for x,y in runs]
  boundaries=[0]
  for j in range(1,len(runs)):
   guess=runs[j][0];left,right=levels[j-1],levels[j]
   if not np.isfinite(left+right):boundaries.append(guess);continue
   aa=max(boundaries[-1]+1,guess-radius);bb=min(len(rr)-1,guess+radius)
   vals=[]
   for k in range(aa,bb+1):
    fi=rr[k]['final_cache_index'];feature_f=feature_for_row(final,rr[k])
    indices=np.array([min(len(raw['times'])-1,max(0,int(np.searchsorted(raw['times'],t[k]+z)))) for z in [left,right]])
    # Evaluate nearest neighbor in PTS, not only searchsorted's ceiling.
    for q in range(2):
     ii=indices[q]
     if ii>0 and abs(raw['times'][ii-1]-(t[k]+[left,right][q]))<abs(raw['times'][ii]-(t[k]+[left,right][q])):indices[q]-=1
    vals.append(scores(raw['features'][indices],feature_f,rr[k].get('matched_roi','both')))
   vals=np.array(vals);cost=np.cumsum(vals[:,0])-np.cumsum(vals[:,1]);cut=aa+int(np.argmin(np.r_[0,cost]))
   cut=max(boundaries[-1]+1,min(cut,len(rr)-1));boundaries.append(cut)
  boundaries.append(len(rr))
  for j,(x,y) in enumerate(zip(boundaries[:-1],boundaries[1:])):
   if y<=x:continue
   local=[r for r in rr[x:y] if r['rmse']<8];offset=float(np.median([r['source_offset_seconds'] for r in local])) if local else None
   oin=float(lo if x==0 else t[x]);oout=float(hi if y==len(rr) else t[y]);duration=oout-oin
   if offset is None:diagnostics.append({'output_in_seconds':oin,'output_out_seconds':oout,'status':'unmapped_nonperson_or_visual_failure'});continue
   source=oin+offset
   errors=[];phases=[]
   for r in rr[x:y]:
    ri=int(np.argmin(np.abs(raw['times']-(r['final_seconds']+offset))));error=float(scores(raw['features'][[ri]],feature_for_row(final,r),r.get('matched_roi','both'))[0]**.5);errors.append(error);phases.append(r['source_offset_seconds']-offset)
   frac=float(np.mean(np.array(errors)<8));identity_consensus=float(np.mean(np.abs(phases)<=1/30))
   status='render_candidate_visual_fit' if frac>=.9 and duration>=.15 else 'ambiguous_visual_fit'
   clip={'id':f'video-{len(clips)+1:04d}','output_in_seconds':oin,'output_out_seconds':oout,'source_in_seconds':source,'source_out_seconds':source+duration,'source_path':raw['meta']['source_path'],'geometry':{k:float(np.median([r.get('geometry',{}).get(k,v) for r in rr[x:y]])) for k,v in [('scale',.75),('tx',0.),('ty',0.)]},'status':status,'source_time_model':'rate1_offset','offset_seconds':offset,'video_only':True,'audio_mapping_separate':True,'fit':{'sample_count':y-x,'native_step':d['sample_step_native_frames'],'visible_roi_counts':{roi:sum(r.get('matched_roi','both')==roi for r in rr[x:y]) for roi in ['both','face_only','hand_only','robust_trimmed70']},'rmse_median_8bit':float(np.median(errors)),'rmse_p95_8bit':float(np.percentile(errors,95)),'fraction_rmse_lt8':frac,'individual_match_within_2_source_frames_fraction':identity_consensus,'offset_residual_p90_seconds':float(np.percentile(np.abs(phases),90)),'output_boundary_precision':'nearest stored native output PTS; derived visual switch point, not original project edit metadata'},'evidence_match_indices':[x,y-1]}
   clips.append(clip)
 report={'schema_version':'video-map-0.1','status':'research_render_candidates','source_path':raw['meta']['source_path'],'reference_path':final['meta']['source_path'],'output_native_resolution':[1920,1080],'clips':clips,'unmapped_intervals':diagnostics,'method':'Global visual seeds plus audio seeds; native face/hand feature matching. Piecewise median offset, boundary optimized by visual reconstruction error. Audio waveform mapping remains independent.','coverage':{'output_ranges':d['ranges'],'native_visual_query_count':len(rows),'raw_native_cache_frames':len(raw['times']),'human_full_viewing':False},'matches_path':str(Path(a.matches).resolve()),'limitations':['All segments are measured render candidates. No claim of original project identity or frame-perfect replica.','RGB feature fit excludes captions and graphics, which must be rendered separately.','B-roll is not sourced from the speaker recording; audio mapping must be joined separately.']}
 save_json(a.output,report)
 save_map_csv(a.output,clips)
 print(json.dumps({'output':a.output,'clips':len(clips),'unmapped':len(diagnostics)},ensure_ascii=False),flush=True)

def refine(a):
 raw=load_cache(a.raw_cache);final=load_cache(a.final_cache);seed=read_json(a.seeds);sr=[r for r in seed['rows'] if r.get('raw_seconds') is not None and r.get('rmse',999)<8];st=np.array([r['final_seconds'] for r in sr]);audio=read_json(a.audio)['rows'] if a.audio and Path(a.audio).exists() else [];at=np.array([r['final_start'] for r in audio]);rawtime=raw['times'];coarse=np.arange(0,len(rawtime),60,dtype=int);rows=[];global_fallback=0;matched=0
 def indices_from(seeds,radius):
  parts=[np.arange(np.searchsorted(rawtime,max(rawtime[0],ss-radius)),np.searchsorted(rawtime,min(rawtime[-1]+1e-8,ss+radius)),dtype=int) for ss in seeds if rawtime[0]-radius<=ss<=rawtime[-1]+radius]
  return np.unique(np.concatenate(parts)) if parts else np.array([],int)
 for i,t in enumerate(final['times']):
  base={'final_cache_index':i,'final_seconds':float(t),'final_pts':int(final['pts'][i])};ge=final['geometry_rows'][i];alt=final.get('alternative_geometry_rows',final['geometry_rows'])[i];choices=[]
  if ge['geometry'] is not None:choices.append((final['features'][i],ge['geometry'],'primary'))
  if alt is not ge and alt['geometry'] is not None:choices.append((final['alternative_features'][i],alt['geometry'],'alternative'))
  if not choices:rows.append({**base,'geometry':None,'source_offset_seconds':None,'rmse':None,'status':'no_static_geometry_match'});continue
  si=int(np.searchsorted(st,t));vs=sr[max(0,si-2):min(len(sr),si+2)];seeds=[float(t+r['source_offset_seconds']) for r in vs];ai=int(np.searchsorted(at,t)) if len(at) else 0
  seeds += [float(t+r['offset']) for r in audio[max(0,ai-2):ai+2] if r.get('correlation',0)>.9]
  inds=indices_from(seeds,.16);best=None
  for feat,geom,which in choices:
   if len(inds):m=match_query(raw,feat,inds)
   else:m={'rmse':999}
   if m['rmse']>4.5:
    global_fallback+=1;global_seeds=[float(rawtime[k]) for k in global_candidates(raw,coarse,feat)];more=indices_from(global_seeds,1.1);ii=np.unique(np.r_[inds,more]);m=match_query(raw,feat,ii);m['global_fallback']=True
   else:m['global_fallback']=False
   if best is None or m['rmse']<best['rmse']:best={**m,'geometry':geom,'geometry_choice':which}
  best.update(base);best['source_offset_seconds']=best['raw_seconds']-float(t);best['status']='native_visual_candidate' if best['rmse']<8 else 'visual_mismatch';best['seed_final_times']=[r['final_seconds'] for r in vs];rows.append(best);matched+=1
  if matched%3000==0:print('native matched',matched,'through',round(float(t),3),'global fallback',global_fallback,flush=True)
 save_json(a.output,{'status':'native_visual_map_queries','raw':raw['meta'],'final':final['meta'],'ranges':[[0,float(final['times'][-1]+1/float(__import__('fractions').Fraction(final['meta']['declared_fps'])))]],'sample_step_native_frames':1,'sampling_interval_seconds':None,'rows':rows,'coverage':{'output_native_frames_cached':len(final['times']),'native_visual_matching_queries':matched,'global_search_fallback_queries':global_fallback,'whole_native_source_feature_count':len(rawtime),'human_full_viewing':False},'seed_map':str(Path(a.seeds).resolve()),'method':'4fps independent visual-global seeds plus audio proposals, native local feature matching, full-source visual global fallback on high residual; independent audio mapping retained.'});print('native map queries complete',len(rows),matched,global_fallback,flush=True)

def segment_sampled(a):
 from types import SimpleNamespace
 d=read_json(a.matches);rows=d['rows'];original_ranges=d['ranges'];scenes=read_json(a.native_scenes)['candidate_frames'] if a.native_scenes else []
 def split_time(left,right):
  if not d.get('sampling_interval_seconds'):return float(right)
  choices=[c for c in scenes if left<c['seconds']<=right]
  return float(max(choices,key=lambda c:c.get('mad',0))['seconds']) if choices else float((left+right)/2)
 def usable(r):return r.get('geometry') is not None and r.get('rmse') is not None and r['rmse']<8
 chunks=[];unmapped=[];start=0;mode=None
 for i,r in enumerate(rows):
  current=usable(r)
  change=i>0 and current!=usable(rows[i-1])
  if i>0 and current and usable(rows[i-1]):
   aa,bb=r['geometry'],rows[i-1]['geometry'];change=abs(aa['scale']-bb['scale'])>.025 or abs(aa['tx']-bb['tx'])>30 or abs(aa['ty']-bb['ty'])>30
  if change:
   lo=original_ranges[0][0] if start==0 else split_time(rows[start-1]['final_seconds'],rows[start]['final_seconds']);hi=split_time(rows[i-1]['final_seconds'],r['final_seconds'])
   (chunks if usable(rows[start]) else unmapped).append([lo,hi]);start=i
 if rows:
  lo=original_ranges[0][0] if start==0 else split_time(rows[start-1]['final_seconds'],rows[start]['final_seconds']);hi=original_ranges[-1][1];(chunks if usable(rows[start]) else unmapped).append([lo,hi])
 # Save exact preprocessing so the sampled/native distinction is reproducible.
 prepared=dict(d);prepared['ranges']=chunks;prepared['rows']=[r for r in rows if usable(r)];tmp=Path(a.workdir)/'prepared-sampled-segmentation.json';save_json(tmp,prepared)
 segment(SimpleNamespace(matches=str(tmp),raw_cache=a.raw_cache,final_cache=a.final_cache,output=a.output))
 result=read_json(a.output);result['coverage']['output_ranges']=original_ranges;result['coverage']['static_geometry_visual_sample_count']=len(rows);result['coverage']['sampling_interval_seconds']=d.get('sampling_interval_seconds');result['unmapped_intervals']+=[{'output_in_seconds':lo,'output_out_seconds':hi,'status':'nonperson_or_unmatched_visual','audio_mapping_separate':True,'audio_source_map_reference':'audio-source-map.json'} for lo,hi in unmapped]
 for clip in result['clips']:
  clip['fit']['output_boundary_precision']='4fps visual anchors with native scene candidate snapping where available; requires native refinement for exact source jumps' if d.get('sampling_interval_seconds') else 'native output PTS, visual reconstruction switch-point candidate; no original project metadata'
  if clip['fit']['sample_count']<3:clip['status']='sparse_visual_candidate'
 result['status']='sampled_piecewise_visual_map_needs_native_refinement' if d.get('sampling_interval_seconds') else 'native_piecewise_visual_render_candidates';save_json(a.output,result);save_map_csv(a.output,result['clips'])
 print('sampled map',len(result['clips']),'clips, gaps',len(result['unmapped_intervals']),flush=True)

def samples(a):
 import av
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True);frames=out/'frames4fps';frames.mkdir(exist_ok=True);result=[];target=0.;prev=None;decoded=0
 with av.open(a.input) as c:
  v=c.streams.video[0];v.codec_context.thread_count=3;v.thread_type='AUTO'
  for f in c.decode(v):
   decoded+=1
   while float(f.time)>=target:
    pick=f if prev is None or abs(float(f.time)-target)<=abs(float(prev.time)-target) else prev;i=len(result)+1
    pick.reformat(width=640,height=360,format='rgb24',src_colorspace='ITU709',dst_colorspace='ITU709').to_image().save(frames/f'frame_{i:06d}.jpg',quality=95,subsampling=0)
    result.append({'frame':i,'nominal_seconds':target,'source_pts':pick.pts,'source_time_base':str(pick.time_base),'source_seconds':float(pick.time)});target+=.25
   prev=f
 save_json(out/'sample-pts.json',{'method':'Nearest native decoded frame to 4fps nominal sampling, BT709 RGB, JPEG95','native_decoded_frame_count':decoded,'native_last_frame_seconds':float(prev.time),'samples':result});print('samples',len(result),'native_decoded',decoded,flush=True)

def frame_indexed(a):
 from fractions import Fraction
 d=read_json(a.map);q=read_json(a.matches);raw=load_cache(a.raw_cache);final=load_cache(a.final_cache)
 if q.get('sample_step_native_frames')!=1 or q.get('sampling_interval_seconds') is not None:raise ValueError('Frame arrays require native step1 matching, not sampled rows.')
 for name,c in [('raw',raw),('final',final)]:
  if c['meta'].get('start_seconds')!=0:raise ValueError(name+' cache must decode from start0 for native frame indices.')
 def tb(c):
  f=Fraction(c['meta']['time_base']);return {'num':f.numerator,'den':f.denominator}
 if raw['meta'].get('native_resolution')!=[2560,1440] or final['meta'].get('native_resolution')!=[1920,1080]:raise ValueError('This calibrated Atrass profile requires raw2560x1440 and reference1920x1080. Recalibrate geometry/ROIs for other resolutions.')
 rawidx=np.load(Path(a.raw_cache)/'index-estimate.npy');finalidx=np.load(Path(a.final_cache)/'index-estimate.npy')
 if not np.array_equal(rawidx,np.arange(len(rawidx))) or not np.array_equal(finalidx,np.arange(len(finalidx))):raise ValueError('Full native cache decode indices must be consecutive.')
 rows=q['rows'];times=np.array([r['final_seconds'] for r in rows]);total=0;counts={};csvrows=[]
 for clip in d['clips']:
  rr=rows[int(np.searchsorted(times,clip['output_in_seconds'])):int(np.searchsorted(times,clip['output_out_seconds']))]
  if any(r.get('raw_cache_index') is None or r.get('rmse',999)>=8 for r in rr):raise ValueError('Clip contains an unmapped native frame: '+clip['id'])
  clip.update({'output_native_frame_indices':[int(finalidx[r['final_cache_index']]) for r in rr],'output_frame_pts':[r['final_pts'] for r in rr],'output_frame_time_base':tb(final),'source_frame_indices':[int(rawidx[r['raw_cache_index']]) for r in rr],'source_frame_pts':[r['raw_pts'] for r in rr],'source_frame_times':[r['raw_seconds'] for r in rr],'source_frame_time_base':tb(raw),'frame_match_rmse_8bit':[round(r['rmse'],6) for r in rr],'frame_match_ambiguity_seconds':[r['raw_time_ambiguity_bounds'] for r in rr],'frame_match_roi':[r.get('matched_roi','both') for r in rr],'frame_mapping_status':'machine_best_native_feature_match_not_original_project_confirmation','preferred_source_time_model':'explicit_native_frame_array'})
  diffs=np.diff(clip['source_frame_indices']);errors=[r['rmse'] for r in rr];widths=[b-a for a,b in clip['frame_match_ambiguity_seconds']]
  clip['native_match_fit']={'count':len(rr),'rmse_median_8bit':float(np.median(errors)) if errors else None,'rmse_p95_8bit':float(np.percentile(errors,95)) if errors else None,'rmse_max_8bit':max(errors,default=None),'ambiguity_span_p95_seconds':float(np.percentile(widths,95)) if widths else None,'repeat_transitions':int(np.sum(diffs==0)),'forward_skip_transitions':int(np.sum(diffs>1)),'backward_transitions':int(np.sum(diffs<0)),'warning':'Repeated/skipped/backward best candidates may reflect low-motion ambiguity and do not prove intentional editorial holds or reverse playback.'}
  for r in rr:
   roi=r.get('matched_roi','both');counts[roi]=counts.get(roi,0)+1;csvrows.append([clip['id'],int(finalidx[r['final_cache_index']]),r['final_pts'],r['final_seconds'],int(rawidx[r['raw_cache_index']]),r['raw_pts'],r['raw_seconds'],r['rmse'],roi,*r['raw_time_ambiguity_bounds']])
  total+=len(rr)
 d['schema_version']='video-map-0.2';d['source_identity']={k:v for k,v in raw['meta'].items() if k.startswith('source_')};d['reference_identity']={k:v for k,v in final['meta'].items() if k.startswith('source_')}
 d['source_native_frame_count']=len(raw['times']);d['reference_native_frame_count']=len(final['times']);d['source_last_pts']=int(raw['pts'][-1]);d['reference_last_pts']=int(final['pts'][-1]);d['source_time_base']=tb(raw);d['reference_time_base']=tb(final)
 d['frame_mapping_coverage']={'output_native_frames_total':len(final['times']),'indexed_native_output_frames':total,'unmapped_native_output_frames':len(final['times'])-total,'roi_counts':counts,'full_native_source_features':len(raw['times']),'full_native_output_features':len(final['times']),'native_queries_with_geometry':q.get('coverage',{}).get('native_visual_matching_queries'),'global_search_fallback_queries':q.get('coverage',{}).get('global_search_fallback_queries'),'human_full_viewing':False,'machine_comparison_is_not_full_human_viewing':True}
 d['limitations']+=['Native source arrays are individually best visual feature candidates. Repeats, skips and backwards steps can reflect near-static ambiguity; original project decisions remain unknown.','Occluded matching pixels are not verified. face_only/hand_only and robust_trimmed70 are explicitly retained per frame.','Audio has a separate source mapping; video frame arrays must not be used as audio cut decisions.']
 d['limitations']=list(dict.fromkeys(d['limitations']));d['matches_path']=str(Path(a.matches).resolve());save_json(a.output,d);save_map_csv(a.output,d['clips'])
 fp=Path(a.output).with_suffix('.frames.csv');tmp=fp.with_name('.'+fp.name+'.tmp')
 with tmp.open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['clip_id','output_native_frame_index','output_pts','output_seconds','source_native_frame_index','source_pts','source_seconds','feature_rmse_8bit','matched_roi','source_ambiguity_start_seconds','source_ambiguity_end_seconds']);w.writerows(csvrows)
 tmp.replace(fp);print(json.dumps({'indexed_output':a.output,'native_frame_count':total,'clips':len(d['clips'])}),flush=True)

def audit(a):
 from fractions import Fraction
 from collections import Counter
 d=read_json(a.map);errors=[];warnings=[];seen=[];spans=[];all_rmse=[];back=repeat=skip=0;raw_cache=load_cache(a.raw_cache) if a.raw_cache else None;final_cache=load_cache(a.final_cache) if a.final_cache else None
 rt=Fraction(d['source_time_base']['num'],d['source_time_base']['den']);ft=Fraction(d['reference_time_base']['num'],d['reference_time_base']['den']);clips=d['clips']
 for c in clips:
  lo,hi=c['output_in_seconds'],c['output_out_seconds'];spans.append((lo,hi,c['id']))
  if not 0<=lo<hi:errors.append(c['id']+': invalid output interval')
  if not 0<=c['source_in_seconds']<c['source_out_seconds']<=float(d['source_last_pts']*rt)+1/60:errors.append(c['id']+': invalid source interval')
  keys=['output_native_frame_indices','output_frame_pts','source_frame_indices','source_frame_pts','frame_match_rmse_8bit','frame_match_roi','frame_match_ambiguity_seconds'];lengths=[len(c.get(k,[])) for k in keys]
  if not lengths[0] or len(set(lengths))!=1:errors.append(c['id']+': unequal/missing native arrays');continue
  out=c[keys[0]];src=c[keys[2]];seen+=out;all_rmse+=c['frame_match_rmse_8bit']
  if raw_cache is not None and all(0<=k<len(raw_cache['pts']) for k in src) and c['source_frame_pts']!=[int(raw_cache['pts'][k]) for k in src]:errors.append(c['id']+': source frame index/PTS mismatch')
  if final_cache is not None and all(0<=k<len(final_cache['pts']) for k in out) and c['output_frame_pts']!=[int(final_cache['pts'][k]) for k in out]:errors.append(c['id']+': output frame index/PTS mismatch')
  if 'render_source_frame_indices' in c:
   render=c['render_source_frame_indices']
   if len(render)!=len(out) or any(b<a or b-a>3 for a,b in zip(render,render[1:])):errors.append(c['id']+': render policy array length/monotonic step violation')
   if any(not 0<=k<d['source_native_frame_count'] for k in render):errors.append(c['id']+': render source frame outside media')
  if any(b-a!=1 for a,b in zip(out,out[1:])):errors.append(c['id']+': output native frame gap')
  if any(not 0<=k<d['source_native_frame_count'] for k in src):errors.append(c['id']+': source native frame out of bounds')
  if any(not lo-1e-8<=float(p*ft)<hi-1e-8 for p in c['output_frame_pts']):errors.append(c['id']+': output PTS outside clip')
  if any(not 0<=p<=d['source_last_pts'] for p in c['source_frame_pts']):errors.append(c['id']+': source PTS out of bounds')
  if any(not np.isfinite(e) or e>=8 for e in c['frame_match_rmse_8bit']):errors.append(c['id']+': invalid visual feature residual')
  delta=np.diff(src);back+=int(np.sum(delta<0));repeat+=int(np.sum(delta==0));skip+=int(np.sum(delta>1))
 for i,g in enumerate(d['unmapped_intervals']):spans.append((g['output_in_seconds'],g['output_out_seconds'],f'gap-{i}'))
 spans.sort()
 if not spans or spans[0][0]!=d['coverage']['output_ranges'][0][0] or abs(spans[-1][1]-d['coverage']['output_ranges'][-1][1])>1e-7:errors.append('Overall output coverage endpoints inconsistent')
 for (_,end,left),(start,_,right) in zip(spans,spans[1:]):
  if abs(start-end)>1e-7:errors.append(f'Coverage gap/overlap {left}->{right}: {start-end:.9f}s')
 if len(set(seen))!=len(seen):errors.append('Duplicate output native frame assignments')
 if len(seen)!=d['frame_mapping_coverage']['indexed_native_output_frames']:errors.append('Coverage count inconsistent')
 comparisons=[]
 if a.anchors:
  anchors=read_json(a.anchors)['matches'];index={of:sf for c in clips for of,sf in zip(c['output_native_frame_indices'],c['source_frame_indices'])}
  for r in anchors:
   got=index.get(r['final_frame_index']);bounds=r['empirical_candidate_frame_bounds'];comparisons.append({'output_frame':r['final_frame_index'],'previous_fullpixel_best_source_frame':r['best_raw_frame'],'new_feature_best_source_frame':got,'source_frame_difference':got-r['best_raw_frame'] if got is not None else None,'within_previous_empirical_candidate_envelope':got is not None and bounds[0]<=got<=bounds[1],'previous_fullpixel_empirical_envelope':bounds,'independence_warning':'Separate fullpixel/Laplacian scoring method and human comparison sheet; same media and some audio seed information. Not a blind holdout.'})
 if back:warnings.append(f'{back} within-clip backwards source steps retain ambiguous best candidates; they do not confirm reverse playback.')
 if d['unmapped_intervals']:warnings.append('Unmapped spans remain explicit; captions, graphics, B-roll and audio require their own reconstruction.')
 warnings.append('Passing this structural audit validates candidate map integrity, not original project equivalence or full automation release.')
 result={'status':'candidate_map_structural_pass' if not errors else 'candidate_map_structural_fail','release_certified':False,'errors':errors,'warnings':warnings,'map_sha256':hashlib.sha256(Path(a.map).read_bytes()).hexdigest(),'coverage':d['frame_mapping_coverage'],'mapped_seconds':sum(c['output_out_seconds']-c['output_in_seconds'] for c in clips),'unmapped_seconds':sum(g['output_out_seconds']-g['output_in_seconds'] for g in d['unmapped_intervals']),'clip_status_counts':dict(Counter(c['status'] for c in clips)),'effective_feature_rmse_8bit_percentiles':{str(p):float(np.percentile([e for e in all_rmse if np.isfinite(e)],p)) if any(np.isfinite(e) for e in all_rmse) else None for p in [0,50,90,95,99,100]},'native_source_step_counts':{'repeat':repeat,'skip_forward':skip,'backward':back},'previous_fullpixel_anchor_comparison':comparisons,'human_full_viewing':False}
 save_json(a.output,result);print(json.dumps({k:result[k] for k in ['status','errors','mapped_seconds','unmapped_seconds','clip_status_counts']}),flush=True)
 if errors:raise SystemExit(1)

def smooth(a):
 """Practical render proposal; preserve the independent per-frame research winners."""
 from fractions import Fraction
 d=read_json(a.map);q=read_json(a.matches);raw=load_cache(a.raw_cache);final=load_cache(a.final_cache);byframe={r['final_cache_index']:r for r in q['rows']};fps=float(Fraction(raw['meta']['declared_fps']));summary=[]
 for c in d['clips']:
  rr=[byframe[i] for i in c['output_native_frame_indices']];candidates=[];emissions=[];backpointers=[];cost=None
  for j,r in enumerate(rr):
   prediction=r['final_seconds']+c['offset_seconds'];center=int(np.argmin(np.abs(raw['times']-prediction)));winner=r['raw_cache_index']
   inds=np.unique(np.clip(np.r_[np.arange(center-2,center+3),np.arange(winner-2,winner+3)],0,len(raw['times'])-1)).astype(int)
   emission=scores(raw['features'][inds],feature_for_row(final,r),r.get('matched_roi','both'));candidates.append(inds);emissions.append(emission)
   if cost is None:cost=emission.copy();backpointers.append(None)
   else:
    steps=inds[None,:]-candidates[j-1][:,None];expected=(r['final_seconds']-rr[j-1]['final_seconds'])*fps;transition=a.cadence_penalty*(steps-expected)**2;transition[(steps<0)|(steps>3)]=np.inf
    joint=cost[:,None]+transition;prev=np.argmin(joint,axis=0);cost=emission+joint[prev,np.arange(len(inds))];backpointers.append(prev)
   if not np.any(np.isfinite(cost)):raise ValueError('No monotonic path in clip '+c['id'])
   cost-=float(np.min(cost))
  k=int(np.argmin(cost));chosen=[0]*len(rr);errors=[0.]*len(rr)
  for j in range(len(rr)-1,-1,-1):
   chosen[j]=int(candidates[j][k]);errors[j]=float(emissions[j][k])**.5
   if j:k=int(backpointers[j][k])
  indices=chosen;delta=np.diff(indices);changed=sum(k!=r['raw_cache_index'] for k,r in zip(indices,rr));s={'clip_id':c['id'],'count':len(indices),'changed_from_research_best':changed,'backward_steps':int(np.sum(delta<0)),'repeat_steps':int(np.sum(delta==0)),'forward_skip_steps':int(np.sum(delta>1)),'rmse_p95_8bit':float(np.percentile(errors,95)),'rmse_max_8bit':max(errors)};summary.append(s)
  c['render_source_frame_indices']=indices;c['render_source_frame_pts']=[int(raw['pts'][k]) for k in indices];c['render_source_frame_times']=[float(raw['times'][k]) for k in indices];c['render_match_rmse_8bit']=[round(e,6) for e in errors];c['render_policy_metrics']=s
 d['render_policy']={'name':'monotonic_visual_with_rate1_cadence_regularizer','source_arrays_preserved':True,'cadence_penalty_mse_units':a.cadence_penalty,'candidates':'Union of ±2 source frames around per-frame visual winner and rate1 clip-offset prediction.','objective':'Effective visual feature MSE plus cadence_penalty*(source frame step - output PTS step*source fps)^2, source step constrained0..3. Dynamic programming per clip.','scope':'Practical reconstruction proposal for this reference only, not evidence of original holds, retiming, or new-raw edit rules.','new_raw_policy':'For a new source, infer content cuts independently, retain normal source playback cadence; do not copy this reference individual repeats/skips.','selection':'Renderer should explicitly prefer render_source_frame_times for practical playback. source_frame_indices/PTS remain the unmodified research winner arrays.','metrics':{'frames':sum(x['count'] for x in summary),'changed_from_research_best':sum(x['changed_from_research_best'] for x in summary),'backward_steps':sum(x['backward_steps'] for x in summary),'repeat_steps':sum(x['repeat_steps'] for x in summary),'forward_skip_steps':sum(x['forward_skip_steps'] for x in summary),'max_clip_p95_rmse_8bit':max(x['rmse_p95_8bit'] for x in summary)},'release_certified':False}
 save_json(a.output,d);save_map_csv(a.output,d['clips']);print(json.dumps(d['render_policy']['metrics']),flush=True)

def build(a):
 import subprocess
 script=str(Path(__file__).resolve());work=Path(a.workdir);work.mkdir(parents=True,exist_ok=True)
 def run(*parts):subprocess.run([sys.executable,script,*map(str,parts)],check=True)
 def reusable(cachepath,source):
  if not a.reuse_caches or not (cachepath/'metadata.json').exists():return False
  m=read_json(cachepath/'metadata.json');identity=source_identity(source)
  if any(m.get(k)!=identity[k] for k in ['source_path','source_size_bytes','source_sha256']):raise ValueError('Refusing stale cache: '+str(cachepath))
  if m.get('feature_spec')!=json.loads(json.dumps(FEATURE_SPEC)):raise ValueError('Cache feature specification mismatch: '+str(cachepath))
  return True
 rawcache=Path(a.raw_cache) if a.raw_cache else work/'raw-native';reference_samples=Path(a.samples) if a.samples else work/'reference-samples';geocache=Path(a.geometry_cache) if a.geometry_cache else work/'reference-geometry';nativecache=Path(a.native_cache) if a.native_cache else work/'reference-native'
 if not reusable(rawcache,a.raw):run('cache','--input',a.raw,'--out',rawcache)
 if not a.samples:run('samples','--input',a.reference,'--out',reference_samples)
 anchor=Path(a.raw_anchor) if a.raw_anchor else work/'raw-anchor.png'
 if not a.raw_anchor:
  code="import av,sys; c=av.open(sys.argv[1]);v=c.streams.video[0];v.codec_context.thread_count=3;c.seek(int(5/v.time_base),stream=v);f=next(f for f in c.decode(v) if float(f.time)>=5);f.reformat(width=640,height=360,format='rgb24',src_colorspace='ITU709',dst_colorspace='ITU709').to_image().save(sys.argv[2])"
  subprocess.run([sys.executable,'-c',code,a.raw,str(anchor)],check=True)
 georeuse=reusable(geocache,a.reference)
 if georeuse:
  gm=read_json(geocache/'metadata.json')
  if gm.get('raw_anchor_sha256')!=hashlib.sha256(anchor.read_bytes()).hexdigest() or gm.get('sample_pts_sha256')!=hashlib.sha256((reference_samples/'sample-pts.json').read_bytes()).hexdigest():raise ValueError('Geometry cache anchor or sampling manifest changed; rebuild cache.')
 if not georeuse:run('geometry','--raw-anchor',anchor,'--frames',reference_samples/'frames4fps','--sample-pts',reference_samples/'sample-pts.json','--reference',a.reference,'--out',geocache)
 nativereuse=reusable(nativecache,a.reference)
 if nativereuse and read_json(nativecache/'metadata.json').get('geometry_sha256')!=hashlib.sha256((geocache/'geometry.json').read_bytes()).hexdigest():raise ValueError('Native cache geometry changed; rebuild cache.')
 if not nativereuse:run('cache','--input',a.reference,'--out',nativecache,'--geometry-map',geocache/'geometry.json')
 nm=read_json(nativecache/'metadata.json');end=nm['last_seconds']+1/float(__import__('fractions').Fraction(nm['declared_fps']))
 matches=work/'full-sampled-matches.json';parts=['pilot','--raw-cache',rawcache,'--final-cache',geocache,'--ranges',json.dumps([[0,end]]),'--global-stride',60,'--seed-radius',1.1,'--output',matches,'--audio',a.audio or '']
 run(*parts)
 native=work/'full-native-matches.json';run('refine','--raw-cache',rawcache,'--final-cache',nativecache,'--seeds',matches,'--audio',a.audio or '','--output',native)
 segmented=work/'segmented-native-map.json';parts=['segment-sampled','--matches',native,'--raw-cache',rawcache,'--final-cache',nativecache,'--workdir',work,'--output',segmented]
 if a.native_scenes:parts+=['--native-scenes',a.native_scenes]
 run(*parts);run('index','--map',segmented,'--matches',native,'--raw-cache',rawcache,'--final-cache',nativecache,'--output',a.output);run('audit','--map',a.output,'--output',Path(a.output).with_name(Path(a.output).stem+'-validation.json'))
 render=Path(a.output).with_name(Path(a.output).stem+'-render.json');run('smooth','--map',a.output,'--matches',native,'--raw-cache',rawcache,'--final-cache',nativecache,'--output',render);run('audit','--map',render,'--raw-cache',rawcache,'--final-cache',nativecache,'--output',render.with_name(render.stem+'-validation.json'))
 print('BUILD_COMPLETE research candidate map, practical render proposal, structural validations:',a.output,render,flush=True)

def main():
 p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter);s=p.add_subparsers(dest='command',required=True)
 c=s.add_parser('cache');c.add_argument('--input',required=True);c.add_argument('--out',required=True);c.add_argument('--start',type=float,default=0);c.add_argument('--end',type=float);c.add_argument('--geometry');c.add_argument('--geometry-map');c.set_defaults(fn=cache)
 c=s.add_parser('pilot');c.add_argument('--raw-cache',required=True);c.add_argument('--final-cache',required=True);c.add_argument('--audio',default='work/raw/audio-align-refined.json');c.add_argument('--ranges',default='[[0,3.237],[30.397,60]]');c.add_argument('--step',type=int,default=1);c.add_argument('--global-stride',type=int,default=30);c.add_argument('--seed-radius',type=float,default=.6);c.add_argument('--output',required=True);c.set_defaults(fn=pilot)
 c=s.add_parser('geometry');c.add_argument('--raw-anchor',required=True);c.add_argument('--frames',required=True);c.add_argument('--sample-pts',required=True);c.add_argument('--reference',required=True);c.add_argument('--out',required=True);c.set_defaults(fn=geometry)
 c=s.add_parser('segment');c.add_argument('--matches',required=True);c.add_argument('--raw-cache',required=True);c.add_argument('--final-cache',required=True);c.add_argument('--output',required=True);c.set_defaults(fn=segment)
 c=s.add_parser('segment-sampled');c.add_argument('--matches',required=True);c.add_argument('--raw-cache',required=True);c.add_argument('--final-cache',required=True);c.add_argument('--output',required=True);c.add_argument('--native-scenes');c.add_argument('--workdir',default='work/video-map');c.set_defaults(fn=segment_sampled)
 c=s.add_parser('samples');c.add_argument('--input',required=True);c.add_argument('--out',required=True);c.set_defaults(fn=samples)
 c=s.add_parser('build');c.add_argument('--raw',required=True);c.add_argument('--reference',required=True);c.add_argument('--workdir',required=True);c.add_argument('--output',required=True);c.add_argument('--samples',help='Existing directory with sample-pts.json and frames4fps');c.add_argument('--raw-anchor');c.add_argument('--audio');c.add_argument('--native-scenes');c.add_argument('--reuse-caches',action='store_true');c.add_argument('--raw-cache');c.add_argument('--geometry-cache');c.add_argument('--native-cache');c.set_defaults(fn=build)
 c=s.add_parser('refine');c.add_argument('--raw-cache',required=True);c.add_argument('--final-cache',required=True);c.add_argument('--seeds',required=True);c.add_argument('--audio',default='work/raw/audio-align-refined.json');c.add_argument('--output',required=True);c.set_defaults(fn=refine)
 c=s.add_parser('index');c.add_argument('--map',required=True);c.add_argument('--matches',required=True);c.add_argument('--raw-cache',required=True);c.add_argument('--final-cache',required=True);c.add_argument('--output',required=True);c.set_defaults(fn=frame_indexed)
 c=s.add_parser('smooth');c.add_argument('--map',required=True);c.add_argument('--matches',required=True);c.add_argument('--raw-cache',required=True);c.add_argument('--final-cache',required=True);c.add_argument('--output',required=True);c.add_argument('--cadence-penalty',type=float,default=.5);c.set_defaults(fn=smooth)
 c=s.add_parser('audit');c.add_argument('--map',required=True);c.add_argument('--output',required=True);c.add_argument('--anchors');c.add_argument('--raw-cache');c.add_argument('--final-cache');c.set_defaults(fn=audit)
 a=p.parse_args();a.fn(a)
if __name__=='__main__':main()
