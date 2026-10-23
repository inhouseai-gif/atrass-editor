"""Explicit bounded rate1 fallback for a one-sample per-piece decode shortage.

This changes source resampling; it is not PCM identity or audio-quality approval.
Input and output intervals stay fixed. No zero/hold padding or source extension.
"""
from pathlib import Path
import math,subprocess,hashlib,copy
import av
import numpy as np
POLICY_ID='rate1-underflow-one-bounded-linear-v1'

def reconstruct_rate1_window(cache_path, *, source_start, source_duration,
                             output_lo, output_hi, render_start, rate,
                             decoded_samples, target_samples, sample_rate=48000):
    """Return new PCM and provenance. Cache sample n denotes time n/sample_rate.

    q_j=S+j*(E-S-1/sr)/(N-1), so difference from the unquantized output clock
    is bounded by the rounding errors at the two endpoints (<=0.5 sample).
    Only cached integer sample instants in [S,E) supply values. The first two
    in-range samples linearly extrapolate a fractional initial target if needed.
    This explicit <1-sample extrapolation is neither silence nor held padding.
    """
    vals=(source_start,source_duration,output_lo,output_hi,render_start,rate)
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in vals):raise ValueError('Nonfinite bounded resampling parameters')
    if rate!=1:raise ValueError('Bounded source reconstruction supports exact rate1 only')
    if sample_rate!=48000:raise ValueError('Bounded source reconstruction requires48k clock')
    if type(decoded_samples) is not int or type(target_samples) is not int or decoded_samples!=target_samples-1:raise ValueError('Only an exact one-sample decode shortage may use bounded reconstruction')
    sr=sample_rate;lo=output_lo;hi=output_hi;N=target_samples
    begin=round((lo-render_start)*sr);finish=round((hi-render_start)*sr)
    if finish-begin!=N or N<2 or source_start<0 or hi<=lo or abs(source_duration-(hi-lo))>1e-10:raise ValueError('Invalid bounded source/output interval')
    S=source_start*sr;E=(source_start+source_duration)*sr;first=math.ceil(S);end=math.ceil(E)
    if E-S<=1 or end-first<2:raise ValueError('Insufficient in-range source support for bounded reconstruction')
    path=Path(cache_path)
    if path.stat().st_size%8:raise ValueError('Invalid stereo float32 source cache size')
    samples=path.stat().st_size//8
    if first<0 or end>samples:raise ValueError('Source cache does not cover nominal selected source interval')
    max_bound=max(abs(begin-(lo-render_start)*sr),abs(finish-(hi-render_start)*sr))
    if max_bound>.500001:raise ValueError('Output endpoint quantization exceeds halfsample')
    step=(E-S-1)/(N-1)
    if not 0<step or not math.isfinite(step):raise ValueError('Invalid bounded linear source step')
    mapped=np.memmap(path,dtype=np.float32,mode='r').reshape(-1,2)
    out=np.empty((N,2),np.float32);max_shift=0.;extra=0;support_min=end;support_max=-1;wmin=2.;wmax=-2.
    try:
        for start in range(0,N,65536):
            js=np.arange(start,min(N,start+65536),dtype=np.float64);q=S+js*step
            # Guarantee the exact declared last endpoint independent of accumulated multiplication.
            if start+len(js)==N:q[-1]=E-1
            ideal=S+(begin+js-(lo-render_start)*sr);err=float(np.max(np.abs(q-ideal)));max_shift=max(max_shift,err)
            if err>.500001 or np.any(q<S) or np.any(q>=E):raise ValueError('Target sample times outside bounded source policy')
            idx=np.floor(q).astype(np.int64);idx=np.clip(idx,first,end-2);weight=q-idx
            if np.any(weight<=-1.000001) or np.any(weight>=2.000001):raise ValueError('Linear reconstruction exceeds one-cell support')
            x=mapped[idx].astype(np.float64);y=mapped[idx+1].astype(np.float64)
            if not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('Source cache contains nonfinite PCM')
            arr=x*(1-weight[:,None])+y*weight[:,None]
            if not np.isfinite(arr).all():raise ValueError('Reconstruction produced nonfinite PCM')
            out[start:start+len(js)]=arr.astype(np.float32);extra+=int(np.count_nonzero((weight<0)|(weight>1)));support_min=min(support_min,int(idx.min()));support_max=max(support_max,int((idx+1).max()));wmin=min(wmin,float(weight.min()));wmax=max(wmax,float(weight.max()))
    finally:mapped._mmap.close()
    return out,{'policy':POLICY_ID,'rate':1,'reason':'per-piece FFmpeg output is exactly one sample shorter than shared output interval','original_decoded_samples':decoded_samples,'target_samples':N,'sample_rate':sr,'source_nominal_interval':[source_start,source_start+source_duration],'output_sample_interval':[begin,finish],'source_time_formula':'q_j=S+j*(E-S-1/sr)/(N-1); PCM sample instants n/sr','linear_source_step_samples':step,'max_time_displacement_samples':max_shift,'max_time_displacement_seconds':max_shift/sr,'source_support_indices_inclusive':[support_min,support_max],'all_selected_cache_timestamps_inside_nominal_interval':support_min/sr>=source_start and support_max/sr<source_start+source_duration,'extrapolated_output_samples':extra,'linear_weight_range':[wmin,wmax],'zero_or_hold_padding_samples':0,'source_extension_seconds':0,'sample_identity_preserved':False,'audio_quality_approved':False,'filter_support_note':'The continuous whole-source44.1k-to48k filter naturally uses neighboring native samples. The bounded guarantee concerns selected48k cache timestamps, not zero convolution influence outside word times.'}

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

class SourceClockCache:
    """Lazily decode and verify each raw source once from sample instant PTS0."""
    def __init__(self,ffmpeg,tmp):self.ffmpeg=str(ffmpeg);self.tmp=Path(tmp);self.items={}
    def path_for(self,source,expected_sha256=None):
        source=Path(source).resolve();stat=source.stat();signature=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)
        if source in self.items:
            row=self.items[source]
            if row['source_stat']!=signature:raise ValueError('Source changed after cache decode')
            if expected_sha256 and row['source_sha256']!=expected_sha256:raise ValueError('Source cache identity mismatch')
            if Path(row['path']).stat().st_size!=row['bytes']:raise ValueError('Source cache size changed')
            return Path(row['path'])
        source_sha=digest(source)
        if expected_sha256 and source_sha!=expected_sha256:raise ValueError('Raw source SHA mismatch before continuous cache decode')
        count=frames=0
        with av.open(str(source)) as container:
            stream=container.streams.audio[0];native_rate=stream.codec_context.sample_rate
            if native_rate<=0 or stream.codec_context.channels!=2:raise ValueError('Continuous cache requires stereo native source')
            for frame in container.decode(stream):
                if frame.pts is None or frame.sample_rate!=native_rate or abs(float(frame.pts*frame.time_base)-count/native_rate)>1e-8:raise ValueError('Native source audio PTS must be contiguous from sample instant0')
                count+=frame.samples;frames+=1
        if not frames:raise ValueError('No source audio frames')
        folder=self.tmp/'source-clock-cache';folder.mkdir(exist_ok=True);dest=folder/f'source-{len(self.items):03d}.f32'
        cmd=[self.ffmpeg,'-v','error','-nostdin','-threads','2','-i',str(source),'-map','0:a:0','-vn','-ac','2','-ar','48000','-c:a','pcm_f32le','-f','f32le','pipe:1']
        with dest.open('xb') as output:result=subprocess.run(cmd,stdout=output,stderr=subprocess.PIPE,check=True)
        after=source.stat()
        if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)!=signature:raise ValueError('Source changed during continuous cache decode')
        if dest.stat().st_size%8 or dest.stat().st_size==0:raise ValueError('Invalid continuous stereo cache')
        actual_count=dest.stat().st_size//8
        if abs(actual_count-count*48000/native_rate)>1.000001:raise ValueError('Unexpected whole-source resampled length')
        self.items[source]={'path':str(dest),'bytes':dest.stat().st_size,'source_stat':signature,'source_sha256':source_sha,'cache_sha256':digest(dest),'command':cmd,'native_sample_rate':native_rate,'native_decoded_frames':frames,'native_samples_per_channel':count,'native_all_frame_pts_contiguous_from_zero':True,'cached_sample_rate':48000,'cached_samples_per_channel':actual_count,'channels':2,'origin_sample_instant_seconds':0,'resampler':'FFmpeg default whole-source44100to48000, no seek, no per-piece flush','ffmpeg_stderr':result.stderr.decode('utf-8',errors='replace')}
        return dest
    def evidence_for(self,source):
        out=copy.deepcopy(self.items[Path(source).resolve()]);out.pop('source_stat');out['cache_path_is_temporary']=True;return out
