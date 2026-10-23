# Research alignment worker. Does not certify cuts or listening.
from pathlib import Path
import os
import numpy as np,json,time
from scipy.io import wavfile
from scipy.signal import stft
from numpy.lib.stride_tricks import sliding_window_view
base=Path(os.environ['ATRASS_AUDIO_OUTPUT_DIR'])
def feat(p):
 fs,y=wavfile.read(p);y=y.astype(np.float32)/32768
 f,t,z=stft(y,fs=fs,nperseg=400,noverlap=0,boundary=None)
 power=abs(z)**2
 edges=np.geomspace(100,3900,33)
 a=np.array([np.log(np.maximum(power[np.maximum(np.searchsorted(f,edges[i]),0):max(np.searchsorted(f,edges[i])+1,np.searchsorted(f,edges[i+1]))].mean(0),1e-10)) for i in range(32)],np.float32)
 a=a.reshape(32,-1)
 # Per-time spectral contrast reduces gain and overall music-volume sensitivity.
 a-=a.mean(0,keepdims=True);a/=a.std(0,keepdims=True)+.5
 return a.T,t
start=time.time()
r,rt=feat(base/'raw-mono-8k.wav');q,qt=feat(base/'finalA-mono-8k.wav')
# One second patches; stride1 in source (50ms), stride10 in output (500ms).
w=20
R=sliding_window_view(r,(w,32))[:,0].reshape(-1,w*32).copy();R-=R.mean(1,keepdims=True);R/=np.linalg.norm(R,axis=1,keepdims=True)+1e-8
Q=sliding_window_view(q,(w,32))[:,0][::10].reshape(-1,w*32).copy();Q-=Q.mean(1,keepdims=True);Q/=np.linalg.norm(Q,axis=1,keepdims=True)+1e-8
print('matrix',R.shape,Q.shape,flush=True)
res=[]
for i in range(0,len(Q),100):
 scores=Q[i:i+100]@R.T
 for j,row in enumerate(scores):
  best=int(np.argmax(row));idx=(i+j)*10
  alt=row.copy();alt[max(0,best-40):best+40]=-2
  second=int(np.argmax(alt))
  res.append(dict(final_start=round(float(qt[idx]-.025),4),raw_start=round(float(rt[best]-.025),4),similarity=round(float(row[best]),5),second_similarity=round(float(alt[second]),5),raw_alternative=round(float(rt[second]-.025),4)))
 print('aligned',min(i+100,len(Q)),flush=True)
(base/'audio-align-coarse.json').write_text(json.dumps({'method':'1 second normalized 32 log-frequency-band patches; source step50ms, final query step500ms; automatic candidate match, not listening or verified editorial cuts','rows':res,'elapsed_seconds':time.time()-start},ensure_ascii=False,indent=2))
print('done',time.time()-start,flush=True)
