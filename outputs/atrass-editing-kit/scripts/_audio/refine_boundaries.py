# Research alignment worker. Does not certify cuts or listening.
from pathlib import Path
import os
import json,numpy as np,csv
from scipy.io import wavfile
from scipy.signal import butter,sosfilt
base=Path(os.environ['ATRASS_AUDIO_OUTPUT_DIR']);fs,r=wavfile.read(base/'raw-mono-8k.wav');_,q=wavfile.read(base/'finalA-mono-8k.wav');sos=butter(3,[120,3400],btype='bandpass',fs=fs,output='sos');r=sosfilt(sos,r.astype(float));q=sosfilt(sos,q.astype(float));gs=json.load(open(base/'audio-offset-groups.json'))['groups']
gains=[]
for g in gs:
 st=int(g['first_anchor']*fs);off=round(g['offset_median']*fs);Q=q[st:st+fs];R=r[st+off:st+off+len(Q)];gains.append(float(np.dot(Q,R)/max(1,np.dot(R,R))))
rows=[]
for i,(left,right) in enumerate(zip(gs[:-1],gs[1:])):
 a=max(0,int((left['last_anchor']-.25)*fs));b=min(len(q),int((right['first_anchor']+1.0)*fs));o0=round(left['offset_median']*fs);o1=round(right['offset_median']*fs);b=min(b,len(r)-max(o0,o1));a=max(a,-min(o0,o1));Q=q[a:b];R0=r[a+o0:b+o0]*gains[i];R1=r[a+o1:b+o1]*gains[i+1]
 if len(Q)==0:continue
 e0=(Q-R0)**2;e1=(Q-R1)**2;cost=np.cumsum(e0-e1);k=int(np.argmin(cost));t=(a+k+1)/fs
 delta=right['offset_median']-left['offset_median'];rows.append({'boundary_id':i+1,'final_seconds_candidate':round(t,6),'raw_before_end_candidate':round(t+left['offset_median'],6),'raw_after_start_candidate':round(t+right['offset_median'],6),'source_jump_seconds':round(delta,6),'previous_anchor_final':left['last_anchor'],'next_anchor_final':right['first_anchor'],'analysis_window_seconds':.08,'status':'waveform_change_point_candidate_not_video_or_listening_verified'})
out={'method':'For successive high-correlation source-offset groups, least-squares waveform switch point after separate gain fits;80ms analysis window; no certified error bound; search intervals may include crossfades/silence; false/extra groups possible. Source_jump is measured offset difference, not certified removed-speech duration. Negative jumps can represent repeats/reorder/false match.','count':len(rows),'boundaries':rows}
(base/'audio-cut-boundary-candidates.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(rows[:20]);print('negative',sum(x['source_jump_seconds']<0 for x in rows))
