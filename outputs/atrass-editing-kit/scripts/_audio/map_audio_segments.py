# Research alignment worker. Does not certify cuts or listening.
from pathlib import Path
import os
import json,numpy as np,csv
from scipy.io import wavfile
from scipy.signal import butter,sosfilt
base=Path(os.environ['ATRASS_AUDIO_OUTPUT_DIR'])
fs,r=wavfile.read(base/'raw-mono-8k.wav');_,q=wavfile.read(base/'finalA-mono-8k.wav');sos=butter(3,[120,3400],btype='bandpass',fs=fs,output='sos')
r=sosfilt(sos,r.astype(float));q=sosfilt(sos,q.astype(float))
groups=json.load(open(base/'audio-offset-groups.json'))['groups'];hop=80;win=640
starts=np.arange(0,len(q)-win+1,hop);scores=np.full((len(groups),len(starts)),-1,np.float32)
def rolling(a):
 c=np.r_[0,np.cumsum(a)];return c[win:]-c[:-win]
for i,g in enumerate(groups):
 a=max(0,int((g['first_anchor']-1)*fs)//hop*hop);b=min(len(q),int((g['last_anchor']+2)*fs));offset=round(g['offset_median']*fs);b=min(b,len(r)-offset);a=max(a,-offset)
 if b-a<win:continue
 Q=q[a:b];R=r[a+offset:b+offset];num=rolling(Q*R);den=np.sqrt(np.maximum(1,rolling(Q**2)*rolling(R**2)));v=num/den
 i0=(a+hop-1)//hop;i1=(b-win)//hop+1;ix=starts[i0:i1]-a;scores[i,i0:i1]=v[ix]
best=scores.argmax(0);strength=scores.max(0);center=(starts+win/2)/fs
# Strongest-local alignment is retained as machine evidence; mark low confidence, never call it an editorial certificate.
# Fill isolated <80ms identity fluctuations only if surrounded by same source group.
for _ in range(3):
 bounds=np.r_[0,np.where(best[1:]!=best[:-1])[0]+1,len(best)]
 for a,b in zip(bounds[:-1],bounds[1:]):
  if b-a<8 and a>0 and b<len(best) and best[a-1]==best[b]:best[a:b]=best[a-1]
bounds=np.r_[0,np.where(best[1:]!=best[:-1])[0]+1,len(best)];segs=[]
for a,b in zip(bounds[:-1],bounds[1:]):
 gi=int(best[a]);g=groups[gi];st=0 if a==0 else float(center[a]);en=len(q)/fs if b==len(best) else float(center[b]);v=scores[gi,a:b]
 segs.append({'id':len(segs)+1,'final_in':round(st,5),'final_out':round(en,5),'raw_in':round(st+g['offset_median'],5),'raw_out':round(en+g['offset_median'],5),'offset_seconds':g['offset_median'],'anchor_group':gi,'correlation_median':round(float(np.median(v)),5),'fraction_windows_ge_0_8':round(float(np.mean(v>=.8)),5),'status':'machine_alignment_needs_boundary_review'})
result={'method':'120–3400Hz normalized waveform correlation;80ms rolling window;10ms step; candidates from1226 1sec anchors; isolated <80ms excursions smoothed only if surrounded by same group. Audio mapping does not certify video cuts. Boundary positions have no certified error bound before native/ear verification; silence/crossfades can shift the estimate.','listened_seconds':0,'window_seconds':win/fs,'hop_seconds':hop/fs,'segment_count':len(segs),'segments':segs}
(base/'audio-source-map.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));np.savez_compressed(base/'audio-source-map-scores.npz',center=center,strength=strength,best=best)
with open(base/'audio-source-map.csv','w') as f:
 w=csv.DictWriter(f,fieldnames=list(segs[0]));w.writeheader();w.writerows(segs)
print('segments',len(segs));print(segs[:20]);print('highcorr fraction',np.mean(strength>=.8))
