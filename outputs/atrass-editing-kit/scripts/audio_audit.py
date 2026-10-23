#!/usr/bin/env python3
"""Local measurable audio audit. No upload; no claim of listening or source separation.
Requires numpy/scipy/soundfile, explicit ffmpeg, and an audio_map candidate JSON.
"""
import os
for _k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMBA_NUM_THREADS'):
    os.environ[_k]='3'
import argparse,json,hashlib,re,subprocess,sys
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy import signal
RATE=16000

def db(x): return float(20*np.log10(max(float(x),1e-12)))
def rms(x): return float(np.sqrt(np.mean(np.square(x,dtype=np.float64)))) if len(x) else 0.
def rounded(x):return round(float(x),6)
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def save(path,data):
    def scalar(obj):
        if isinstance(obj,np.generic):return obj.item()
        raise TypeError(f'Unsupported JSON value: {type(obj).__name__}')
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False,default=scalar)+'\n', encoding='utf-8')
def decode(src,dest,ff):
    subprocess.run([ff,'-v','error','-nostdin','-threads','3','-filter_threads','3','-i',str(src),'-vn','-ac','2','-ar',str(RATE),'-c:a','pcm_f32le',str(dest)],check=True)
    x,sr=sf.read(dest,dtype='float32',always_2d=True)
    assert sr==RATE
    return x

def loudness(src,ff):
    r=subprocess.run([ff,'-hide_banner','-nostdin','-threads','3','-filter_threads','3','-i',str(src),'-vn','-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,check=True)
    found=re.findall(r'\{\s*"input_i".*?\}',r.stderr,re.S)
    if not found:raise RuntimeError('FFmpeg loudnorm measurement missing')
    d=json.loads(found[-1]);return {k:float(d[k]) for k in ('input_i','input_tp','input_lra','input_thresh')}

def group_windows(rows,predicate,step=1.0):
    out=[]
    for r in rows:
        if not predicate(r):continue
        t=r['start']
        if out and t<=out[-1]['end']+.01:out[-1]['end']=r['end'];out[-1]['windows']+=1
        else:out.append({'start':t,'end':r['end'],'windows':1})
    return out

def profile(x,label):
    mono=x.mean(axis=1);side=(x[:,0]-x[:,1])/2
    n=(len(mono)//320)*320;blocks=mono[:n].reshape(-1,320);energies=np.sqrt(np.mean(blocks.astype(np.float64)**2,axis=1));noise=float(np.percentile(energies,10))
    rows=[]
    for i in range(0,len(mono),RATE):
        m=mono[i:i+RATE];s=side[i:i+RATE]
        if len(m)<RATE//4:continue
        freq,powr=signal.welch(m,fs=RATE,nperseg=min(1024,len(m)))
        powr=np.maximum(powr,1e-20);flat=float(np.exp(np.mean(np.log(powr)))/np.mean(powr));cent=float(np.sum(freq*powr)/np.sum(powr))
        rows.append({'start':i/RATE,'end':min((i+RATE)/RATE,len(mono)/RATE),'rms_dbfs':rounded(db(rms(m))),'peak_dbfs':rounded(db(np.max(np.abs(m)))),'side_dbfs':rounded(db(rms(s))),'side_to_mid_db':rounded(db(rms(s)/max(rms(m),1e-12))),'spectral_flatness':rounded(flat),'centroid_hz':rounded(cent)})
    cross=float(np.dot(x[:,0].astype(float),x[:,1].astype(float))/max(np.linalg.norm(x[:,0].astype(float))*np.linalg.norm(x[:,1].astype(float)),1e-20))
    return {'duration_seconds':len(mono)/RATE,'decode_rate_hz':RATE,'sample_peak_dbfs_at_16k':db(np.max(np.abs(x))),'rms_dbfs':db(rms(mono)),'low_energy_20ms_p10_dbfs':db(noise),'p50_20ms_dbfs':db(np.median(energies)),'p90_20ms_dbfs':db(np.percentile(energies,90)),'near_full_scale_samples_fraction_at_16k':float(np.mean(np.abs(x)>=.999)),'lr_waveform_correlation':cross,'side_to_mid_overall_db':db(rms(side)/max(rms(mono),1e-12)),'below_minus40dbfs_20ms_seconds':float(np.sum(energies<.01)*.02),'side_activity_candidates':group_windows(rows,lambda r:r['side_dbfs']>-55 and r['side_to_mid_db']>-35),'windows':rows,'note':'Low-energy quantiles are not certified noise-floor or speech labels. 16k sample peak differs from native true peak; native loudnorm provides dBTP.'}

def compare(raw,ref,mapping):
    x=raw.mean(axis=1);y=ref.mean(axis=1);sos=signal.butter(4,[120,3400],btype='bandpass',fs=RATE,output='sos');xf=signal.sosfiltfilt(sos,x).astype('float32');yf=signal.sosfiltfilt(sos,y).astype('float32')
    residual=np.zeros_like(y);covered=np.zeros(len(y),bool);rows=[];bands=[(80,150),(150,300),(300,600),(600,1200),(1200,2400),(2400,4000),(4000,7000)]
    for seg in mapping['segments']:
        a=max(0,float(seg['final_in'])+.12);b=min(len(y)/RATE,float(seg['final_out'])-.12)
        if b-a<.35:continue
        for start in np.arange(a,b,.75):
            end=min(start+.75,b)
            if end-start<.35:continue
            fi=int(round(start*RATE));nn=int(round((end-start)*RATE));ri=int(round((start+seg['offset_seconds'])*RATE));pad=int(.020*RATE)
            if ri-pad<0 or ri+nn+pad>len(x) or fi+nn>len(y):continue
            yy=yf[fi:fi+nn];xx=xf[ri-pad:ri+nn+pad]
            cc=signal.correlate(xx,yy,mode='valid',method='fft');energy=np.sqrt(np.maximum(signal.convolve(xx.astype(float)**2,np.ones(nn),mode='valid',method='fft'),1e-20));corr=cc/(energy*max(np.linalg.norm(yy),1e-20));j=int(np.argmax(corr));ri=ri-pad+j
            xr=x[ri:ri+nn].astype(float);yr=y[fi:fi+nn].astype(float);xr-=xr.mean();yr-=yr.mean();g=float(np.dot(xr,yr)/max(np.dot(xr,xr),1e-20));res=yr-g*xr;residual[fi:fi+nn]=res;covered[fi:fi+nn]=True
            freq,px=signal.welch(xr,fs=RATE,nperseg=1024);_,py=signal.welch(yr,fs=RATE,nperseg=1024)
            band_gain=[db(np.sqrt((py[(freq>=lo)&(freq<hi)].sum()+1e-20)/(px[(freq>=lo)&(freq<hi)].sum()+1e-20))) for lo,hi in bands]
            rows.append({'segment_id':seg['id'],'final_in':rounded(fi/RATE),'final_out':rounded((fi+nn)/RATE),'raw_in_refined':rounded(ri/RATE),'correlation_speech_band':rounded(corr[j]),'lag_correction_ms':rounded((ri/RATE-(fi/RATE+seg['offset_seconds']))*1000),'gain_db':rounded(db(abs(g))),'raw_rms_dbfs':rounded(db(rms(xr))),'final_rms_dbfs':rounded(db(rms(yr))),'residual_rms_dbfs':rounded(db(rms(res))),'residual_to_final_db':rounded(db(rms(res)/max(rms(yr),1e-12))),'band_power_gain_db':[rounded(v) for v in band_gain]})
    trustworthy=[r for r in rows if r['correlation_speech_band']>=.9 and r['raw_rms_dbfs']>-40]
    if not trustworthy:raise RuntimeError('No high-correlation voice windows; do not infer gain/EQ.')
    gains=np.array([r['gain_db'] for r in trustworthy]);bg=np.array([r['band_power_gain_db'] for r in trustworthy]);med=np.median(bg,axis=0)
    summary={'window_count':len(rows),'high_correlation_voice_window_count':len(trustworthy),'coverage_seconds':float(covered.sum()/RATE),'high_correlation_covered_seconds':rounded(sum(r['final_out']-r['final_in'] for r in trustworthy)),'gain_db_quantiles_p10_p50_p90':np.percentile(gains,[10,50,90]).tolist(),'residual_to_final_db_median_high_correlation':float(np.median([r['residual_to_final_db'] for r in trustworthy])),'bands_hz':bands,'median_band_power_gain_db':med.tolist(),'median_band_gain_relative_to_600_1200hz_db':(med-med[3]).tolist(),'method':'0.75s windows inside candidate segments,120ms edge exclusion,120-3400Hz waveform correlation with +/-20ms lag search; least-squares scalar raw-to-final gain. Residual also contains codec/EQ/compression/misalignment, not a clean music stem.','boundary_uncertainty_guarantee':None}
    suspects=[r for r in rows if r['correlation_speech_band']>=.8 and r['residual_rms_dbfs']>-38 and r['residual_to_final_db']>-15]
    summary['residual_review_candidates']=sorted(suspects,key=lambda r:r['residual_to_final_db'],reverse=True)[:60]
    return summary,rows,residual,covered

def processing_profile(p,comparison):
    gain=comparison['gain_db_quantiles_p10_p50_p90'][1];a=p['reference']['loudness_native'];b=p.get('comparison',{}).get('loudness_native')
    return {'default_master_target_lufs':-15.5,'default_true_peak_ceiling_dbtp':-1.5,'loudness_acceptance_lu':1.0,'reference_A_measured_lufs':a['input_i'],'reference_A_measured_dbtp':a['input_tp'],'reference_B_measured_lufs':b['input_i'] if b else None,'raw_to_A_voice_gain_initial_db':gain,'voice_gain_is_initial_estimate_requires_post_mix_measurement':True,'denoise':'Do not blanket-gate. Begin disabled; enable gentle denoise only after measured pause noise and speech-tail preview. Preserve breaths and final consonants.','highpass':'Start disabled for style matching. If measured rumble is present, preview 60-80Hz/12dB octave and remeasure; no automatic 120Hz cut based only on alignment bandpass.','compression':'Reference waveform amplitude transfer does not uniquely identify compressor; leave off until dynamic-range and peak checks show need. Gain+truepeak control first.','music_policy':'Use an explicit licensed music stem only after candidate review; never treat raw-fit residual as isolated reusable music. Duck through voice regions; compare short-term level and stereo-side profile.','cut_audio_policy':'Candidate boundaries are not approved edit points; inspect phoneme edges. Apply only short crossfades after confirming no repeated/lost syllable.','render_order':['voice clip gain','optional measured cleanup','approved BGM/SFX and ducking','two-pass loudnorm','native LUFS/dBTP verification'],'match_vs_delivery':'Measured references exceed0dBTP. Delivery ceiling deliberately improves headroom and is not asserted as original style value.'}

def stereo_analysis(audio,rows,regions=(),transient_range=None):
    """Balance channel gain before measuring residual stereo; no sound-source identity inferred."""
    balances={};events=[];regional=[]
    for label,x in audio.items():
        l=x[:,0].astype(float);r=x[:,1].astype(float)
        gain=float(np.dot(l,r)/max(np.dot(r,r),1e-20));side=(l-gain*r)/2;mid=(l+gain*r)/2
        balances[label]={'left_right_gain_db':db(abs(gain)),'gain_balanced_side_to_mid_db':db(rms(side)/max(rms(mid),1e-12))}
        if label!='reference':continue
        en=np.sqrt(np.mean(side[:len(side)//320*320].reshape(-1,320)**2,axis=1));times=np.arange(len(en))*.02
        lo,hi=transient_range if transient_range is not None else (0,len(x)/RATE)
        mask=(en>10**(-52/20))&(times>lo)&(times<hi)
        for i in np.flatnonzero(mask):
            t=round(float(times[i]),3);level=db(en[i])
            if events and t<=events[-1]['end']+.06:
                events[-1]['end']=round(t+.02,3);events[-1]['max_side_rms_dbfs']=max(events[-1]['max_side_rms_dbfs'],level)
            else:events.append({'start':t,'end':round(t+.02,3),'max_side_rms_dbfs':level,'event_type':'stereo_activity_candidate','confirmed_sound_identity':None,'render_asset_selected':False})
        for event in events:
            event['priority']='strong_sparse' if event['max_side_rms_dbfs']>-45 and event['end']-event['start']<=.8 else ('weak_sparse' if event['end']-event['start']<=.8 else 'continuous_activity')
        for name,start,end in regions:
            start=float(start);end=float(end)
            if start<0 or end<=start or end>len(x)/RATE:raise ValueError(f'Invalid region: {name} {start} {end}')
            y=x[int(start*RATE):int(end*RATE)];a=y[:,0].astype(float);b=y[:,1].astype(float);g=float(np.dot(a,b)/max(np.dot(b,b),1e-20));ss=(a-g*b)/2;mm=(a+g*b)/2
            rr=[z for z in rows if start<=z['final_in']<end and z['correlation_speech_band']>=.9 and z['raw_rms_dbfs']>-40]
            regional.append({'name':name,'range_seconds':[start,end],'balanced_stereo_side_rms_dbfs':db(rms(ss)),'balanced_side_to_mid_db':db(rms(ss)/max(rms(mm),1e-12)),'aligned_voice_gain_median_db':float(np.median([z['gain_db'] for z in rr])) if rr else None,'residual_to_final_median_db':float(np.median([z['residual_to_final_db'] for z in rr])) if rr else None,'aligned_windows':len(rr)})
    return {'channel_balance':balances,'events':events,'regions':regional,'method':'Least-squares left/right gain balance; 20ms side RMS > -52dBFS, merge gap <=60ms; strong sparse means peak-window RMS > -45dBFS and duration <=0.8s. Thresholds are review heuristics, not sound identity classifiers.','manual_listening':False}

def asr_mapping_consistency(raw_asr,ref_asr,mapping):
    from difflib import SequenceMatcher
    words=[w for s in raw_asr['segments'] for w in s.get('words',[])];norm=lambda t:re.sub(r'[^a-z0-9가-힣]','',t.lower());checks=[]
    for s in ref_asr['segments']:
        selected=[]
        for q in mapping['segments']:
            a=max(s['start'],q['final_in']);b=min(s['end'],q['final_out'])
            if b<=a:continue
            a+=q['offset_seconds'];b+=q['offset_seconds']
            selected.extend(w['word'] for w in words if a<=(w['start']+w['end'])/2<b)
        text=''.join(selected);ratio=SequenceMatcher(None,norm(s['text']),norm(text)).ratio()
        checks.append({'final_range':[s['start'],s['end']],'final_asr':s['text'],'mapped_raw_asr':text,'normalized_text_similarity':round(ratio,4),'status':'text_consistency_check_not_boundary_certification'})
    return checks

def transient_patterns(reference,events):
    l=reference[:,0].astype(float);r=reference[:,1].astype(float);g=float(np.dot(l,r)/max(np.dot(r,r),1e-20));side=(l-g*r)/2
    strong=[e for e in events if e['max_side_rms_dbfs']>-45 and e['end']-e['start']<=.8];clips=[];metrics=[]
    for e in strong:
        t=(e['start']+e['end'])/2;start=int((t-.4)*RATE);end=start+int(.8*RATE)
        if start<0 or end>len(side):continue
        p=signal.sosfiltfilt(signal.butter(3,100,fs=RATE,btype='highpass',output='sos'),side[start:end]);clips.append(p)
        f,ps=signal.welch(p,fs=RATE,nperseg=512);ps/=max(ps.sum(),1e-20)
        metrics.append({**e,'side_spectrum_centroid_hz':float((f*ps).sum()),'side_spectrum_peak_bin_hz':float(f[np.argmax(ps)]),'side_energy_fraction_above_2khz':float(ps[f>2000].sum())})
    matrix=[];pairs=[]
    for i,a in enumerate(clips):
        row=[]
        for j,b in enumerate(clips):
            c=signal.correlate(a,b,mode='full',method='fft');mid=len(b)-1;c=c[mid-int(.15*RATE):mid+int(.15*RATE)+1]
            val=float(np.max(np.abs(c))/max(np.linalg.norm(a)*np.linalg.norm(b),1e-20));row.append(val)
            if i<j and val>.75:pairs.append({'event_a_start':metrics[i]['start'],'event_b_start':metrics[j]['start'],'absolute_waveform_correlation':val})
        matrix.append(row)
    return {'method':'Gain-balanced stereo side; 0.8s event-centered windows,100Hz highpass; absolute normalized cross correlation within +/-150ms. Recurring waveform patterns are not sound identities.','manual_listening':False,'strong_event_count':len(metrics),'events':metrics,'correlation_matrix':matrix,'pairs_over_0_75':pairs}

def transcribe_local(audio,model_dir,output_dir,label,method='overlap'):
    """Offline optional ASR; explicit existing local MLX weights only."""
    if not (model_dir/'weights.safetensors').is_file():raise ValueError('Existing local MLX model weights required; downloads disabled')
    os.environ['HF_HUB_OFFLINE']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    import torch
    torch.set_num_threads(3)
    import mlx_whisper
    if method not in ('overlap','continuous'):raise ValueError('Unknown local ASR window method')
    mono=audio.mean(axis=1).astype('float32');duration=len(mono)/RATE;segments=[]
    if method=='continuous':
        # Whisper still uses its internal windows. Avoid restarting inference
        # around external120s boundaries when preparing a new recording.
        r=mlx_whisper.transcribe(mono,path_or_hf_repo=str(model_dir.resolve()),language='ko',word_timestamps=True,condition_on_previous_text=False,temperature=0.0,verbose=False)
        segments=r['segments']
    for start in (np.arange(0,duration,120) if method=='overlap' else []):
        left=max(0,start-2);right=min(duration,start+122)
        r=mlx_whisper.transcribe(mono[int(left*RATE):int(right*RATE)],path_or_hf_repo=str(model_dir.resolve()),language='ko',word_timestamps=True,condition_on_previous_text=False,temperature=0.0,verbose=False)
        for seg in r['segments']:
            seg['start']+=left;seg['end']+=left
            for w in seg.get('words',[]):w['start']+=left;w['end']+=left
            if start<=(seg['start']+seg['end'])/2<min(duration,start+120):segments.append(seg)
    chunk_method=('120s ownership windows/2s context overlap; midpoint ownership; boundary text not certified' if method=='overlap' else 'Single uninterrupted source inference; native internal Whisper windows; external chunking disabled.')
    doc={'engine':'mlx-whisper','model':'explicit existing local weights','duration_seconds':duration,'manual_listening':False,'network_upload':False,'segments':segments,'chunk_method':chunk_method}
    save(output_dir/f'{label}-asr.json',doc)
    (output_dir/f'{label}-asr.txt').write_text('\n'.join(f"[{s['start']:.3f}–{s['end']:.3f}] {s['text']}" for s in segments)+'\n', encoding='utf-8')
    # MLX may return numpy scalar timestamps. Use the same JSON-number types as
    # the preserved artifact before strict downstream timing validation.
    return json.loads((output_dir/f'{label}-asr.json').read_text(encoding='utf-8'))

def editorial_candidates(raw_asr,ref_asr,boundaries,raw_mono,mapping=None):
    from difflib import SequenceMatcher
    norm=lambda t:re.sub(r'[^0-9a-z가-힣]','',t.lower())
    rs=raw_asr['segments'];words=[w for s in rs for w in s.get('words',[])];rows=[];repeats=[]
    for i,s in enumerate(rs):
        aa=norm(s['text'])
        if len(aa)<10:continue
        for t in rs[i+1:i+8]:
            if t['start']-s['end']>30:break
            ratio=SequenceMatcher(None,aa,norm(t['text'])).ratio()
            if ratio>=.88:repeats.append({'raw_first':[s['start'],s['end']],'raw_repeat':[t['start'],t['end']],'similarity':round(ratio,4),'text_first':s['text'],'text_repeat':t['text'],'status':'ASR_repeat_candidate_not_listened'})
    for b in boundaries.get('boundaries',[]):
        jump=b['source_jump_seconds']
        if jump<.2:continue
        start=b['raw_before_end_candidate'];end=b['raw_after_start_candidate'];samples=raw_mono[int(start*RATE):int(end*RATE)]
        if len(samples)<320:continue
        samples=samples[:len(samples)//320*320].reshape(-1,320);en=np.sqrt(np.mean(samples.astype(float)**2,axis=1));quiet=float(np.mean(en<.01))
        within=[w['word'] for w in words if start<=(w['start']+w['end'])/2<=end];text=''.join(within).strip()
        linked=[r for r in repeats if max(start,r['raw_first'][0])<min(end,r['raw_first'][1]) or max(start,r['raw_repeat'][0])<min(end,r['raw_repeat'][1])]
        production_cues=[cue for cue in ['잠시멈춰','영상은안끊','다시하겠습니다','이건챕터'] if cue in re.sub(r'\s','',text)]
        if production_cues:reason='production_discussion_or_slate_candidate'
        elif linked:reason='repeated_or_retake_phrase_candidate'
        elif quiet>=.75:reason='pause_tightening_candidate'
        elif not text and quiet>=.5:reason='breath_or_pause_candidate'
        else:reason='spoken_material_or_ASR_boundary_ambiguity_review_required'
        rows.append({'boundary_id':b['boundary_id'],'final_boundary_candidate':b['final_seconds_candidate'],'raw_gap_candidate':[start,end],'duration_seconds':jump,'raw_under_minus40dbfs_20ms_fraction':quiet,'asr_words_in_gap':text,'reason_candidate':reason,'production_cues':production_cues,'repetition_links':linked,'approved_for_automatic_cut':False})
    lead=None
    if mapping and mapping['segments']:
        first=min(mapping['segments'],key=lambda q:q['final_in']);end=float(first['raw_in'])
        if first['final_in']==0 and end>0:
            lead={'raw_range_candidate':[0,end],'asr_words':''.join(w['word'] for w in words if (w['start']+w['end'])/2<end),'status':'leading_source_material_omitted_in_candidate_map','approved_for_automatic_cut':False}
    return {'status':'machine_editorial_review_candidates','direct_listening':False,'reason_is_author_intent':False,'positive_gap_count':len(rows),'negative_jump_candidates':[b for b in boundaries.get('boundaries',[]) if b['source_jump_seconds']<-.2],'raw_asr_repetition_candidates':repeats,'gap_candidates':rows,'leading_omission_candidate':lead,'notes':['ASR times may straddle candidate boundaries.','A source offset jump is not a certified removed speech duration.','Reasons are automated review hypotheses, not an edit-decision approval.']}

def master_cli():
    """Two-pass measured master render; writes a new file, then verifies native LUFS/dBTP."""
    ap=argparse.ArgumentParser(description=master_cli.__doc__)
    ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--ffmpeg',required=True)
    ap.add_argument('--target-lufs',type=float,default=-15.5);ap.add_argument('--true-peak',type=float,default=-1.5);ap.add_argument('--voice-gain-db',type=float,default=0)
    ap.add_argument('--start',type=float,default=0);ap.add_argument('--duration',type=float)
    a=ap.parse_args(sys.argv[2:]);src=a.input.resolve(strict=True);dst=a.output.resolve()
    if dst.exists():ap.error('Output exists; source and previous results preserved.')
    if dst.suffix.lower()!='.wav':ap.error('Master output must be WAV; re-check true peak after any later AAC/Opus encoding.')
    dst.parent.mkdir(parents=True,exist_ok=True)
    inp=[a.ffmpeg,'-hide_banner','-nostdin','-threads','3','-filter_threads','3','-ss',str(a.start)]
    if a.duration is not None:inp+=['-t',str(a.duration)]
    inp+=['-i',str(src),'-vn']
    prefix=f'volume={a.voice_gain_db}dB,'
    common=f'I={a.target_lufs}:TP={a.true_peak}:LRA=11'
    r=subprocess.run(inp+['-af',prefix+'loudnorm='+common+':print_format=json','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,check=True)
    found=re.findall(r'\{\s*"input_i".*?\}',r.stderr,re.S)
    if not found:raise RuntimeError('First-pass loudness measurement missing')
    first=json.loads(found[-1]);lf='loudnorm='+common+f":measured_I={first['input_i']}:measured_TP={first['input_tp']}:measured_LRA={first['input_lra']}:measured_thresh={first['input_thresh']}:offset={first['target_offset']}:linear=true:print_format=json"
    second=subprocess.run(inp+['-af',prefix+lf,'-ar','48000','-c:a','pcm_s24le',str(dst)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,check=True)
    after=loudness(dst,a.ffmpeg);verified=after['input_tp']<=a.true_peak+.15 and abs(after['input_i']-a.target_lufs)<=.5
    receipt={'source_name':src.name,'output_name':dst.name,'start_seconds':a.start,'duration_requested':a.duration,'voice_gain_db':a.voice_gain_db,'target_lufs':a.target_lufs,'target_true_peak_dbtp':a.true_peak,'first_pass_after_initial_gain':first,'after_native_measurement':after,'measured_acceptance_passed':verified,'pcm_format':'48000Hz PCM24','manual_listening':False,'note':'This master sets a delivery target; it does not infer the original compressor/EQ. Verify again after lossy encoding.'}
    save(dst.with_suffix('.audit.json'),receipt);print(json.dumps(receipt,ensure_ascii=False))
    if not verified:raise SystemExit('Rendered result failed numeric loudness/peak gate; receipt preserved for inspection.')

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--raw',type=Path,required=True);ap.add_argument('--reference',type=Path,required=True);ap.add_argument('--comparison',type=Path);ap.add_argument('--source-map',type=Path,required=True);ap.add_argument('--ffmpeg',required=True);ap.add_argument('--output-dir',type=Path,required=True);ap.add_argument('--asr-model-dir',type=Path);ap.add_argument('--boundaries',type=Path);ap.add_argument('--region',action='append',nargs=3,metavar=('NAME','START','END'),default=[]);ap.add_argument('--transient-range',nargs=2,type=float,metavar=('START','END'));a=ap.parse_args()
    out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    if (out/'audio-deep-analysis.json').exists():ap.error('Existing audit preserved; choose a new output directory.')
    assets={};audio={}
    for label,src in [('raw',a.raw),('reference',a.reference),('comparison',a.comparison)]:
        if not src:continue
        src=src.resolve(strict=True);cache=out/f'{label}-stereo16k.wav';x=decode(src,cache,a.ffmpeg);audio[label]=x;pf=profile(x,label);pf['source_name']=src.name;pf['source_sha256']=digest(src);pf['loudness_native']=loudness(src,a.ffmpeg);assets[label]=pf;save(out/f'{label}-window-metrics.json',pf['windows']);del pf['windows'];print(label,'measured',flush=True)
    mapping=json.loads(a.source_map.read_text(encoding='utf-8'));summary,rows,residual,coverage=compare(audio['raw'],audio['reference'],mapping);save(out/'aligned-window-metrics.json',rows)
    sf.write(out/'reference-fitted-residual-research.wav',residual,RATE,subtype='FLOAT');sf.write(out/'reference-stereo-side-research.wav',(audio['reference'][:,0]-audio['reference'][:,1])/2,RATE,subtype='FLOAT');np.save(out/'residual-coverage-mask.npy',coverage)
    doc={'schema_version':1,'status':'machine_audio_audit_not_listened','manual_listened_seconds':0,'source_uploaded':False,'cpu_threads_limit':3,'assets':assets,'aligned_comparison':summary,'processing_profile':processing_profile(assets,summary),'limitations':['Candidate waveform map does not certify video edits or removed meaning.','80ms earlier mapper window is not a guaranteed boundary-error interval.','Residual is not isolated music/SFX; codec/EQ/nonlinearity/alignment can contribute.','16k spectral measurements cover below8kHz; native LUFS/truepeak measured independently.','ASR text is added separately and is not direct listening.']}
    stereo=stereo_analysis(audio,rows,a.region,a.transient_range);save(out/'audio-event-candidates.json',stereo);doc['stereo_analysis']=stereo
    save(out/'audio-transient-patterns.json',transient_patterns(audio['reference'],stereo['events']))
    if a.asr_model_dir:
        ra=transcribe_local(audio['raw'],a.asr_model_dir,out,'raw');fa=transcribe_local(audio['reference'],a.asr_model_dir,out,'reference')
        doc['asr']={'raw_segments':len(ra['segments']),'reference_segments':len(fa['segments']),'direct_listening':False}
        checks=asr_mapping_consistency(ra,fa,mapping);save(out/'asr-mapping-consistency.json',checks)
        doc['asr_mapping_consistency']={'segment_count':len(checks),'median_normalized_text_similarity':float(np.median([q['normalized_text_similarity'] for q in checks])),'segments_ge_0_8':sum(q['normalized_text_similarity']>=.8 for q in checks),'not_boundary_certification':True}
        if a.boundaries:save(out/'editorial-audio-candidates.json',editorial_candidates(ra,fa,json.loads(a.boundaries.read_text(encoding='utf-8')),audio['raw'].mean(axis=1),mapping))
    save(out/'audio-deep-analysis.json',doc);print(json.dumps({'output':str(out),'windows':len(rows),'valid_voice':summary['high_correlation_voice_window_count'],'gain_median_db':summary['gain_db_quantiles_p10_p50_p90'][1]}))
if __name__=='__main__':
    master_cli() if len(sys.argv)>1 and sys.argv[1]=='master' else main()
