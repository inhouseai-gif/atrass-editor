# Research alignment worker. Does not certify cuts or listening.
from pathlib import Path
import os
import json,numpy as np
from scipy.io import wavfile
from scipy.signal import correlate,butter,sosfilt
base=Path(os.environ['ATRASS_AUDIO_OUTPUT_DIR'])
fs,raw=wavfile.read(base/'raw-mono-8k.wav');_,final=wavfile.read(base/'finalA-mono-8k.wav')
sos=butter(3,[120,3400],btype='bandpass',fs=fs,output='sos')
raw=sosfilt(sos,raw.astype(np.float32));final=sosfilt(sos,final.astype(np.float32))
coarse=json.load(open(base/'audio-align-coarse.json'))['rows'];res=[]
for r in coarse:
 q=int(r['final_start']*fs);mid=int(r['raw_start']*fs);pad=int(.20*fs);a=max(0,mid-pad);b=min(len(raw),mid+fs+pad);Q=final[q:q+fs];R=raw[a:b]
 if len(R)<len(Q):continue
 corr=correlate(R,Q,mode='valid',method='fft');cs=np.r_[0,np.cumsum(R**2)];energy=cs[len(Q):]-cs[:-len(Q)];score=corr/np.sqrt(np.maximum(1,energy*np.sum(Q**2)))
 k=int(np.argmax(score));r.update(raw_fine=round((a+k)/fs,6),correlation=round(float(score[k]),6),offset=round((a+k)/fs-r['final_start'],6));res.append(r)
(base/'audio-align-refined.json').write_text(json.dumps({'method':'Bandpass120-3400Hz 1sec waveform normalized correlation ±200ms around coarse candidate; 8kHz grid; no listening','rows':res},ensure_ascii=False,indent=2))
print('median corr',np.median([r['correlation'] for r in res]));print(res[:20])
