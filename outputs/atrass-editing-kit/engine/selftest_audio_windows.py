#!/usr/bin/env python3
"""Regression of actual711-frame boundary bug; tests assignment, never audible quality."""
import argparse,math,tempfile,wave,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import render
SR=48000

def entry(name,lo,hi,**options):
    return dict(id=name,source=name,source_in=.5,**{'in':lo,'out':hi},**options)

def run(rows,start,end,values,*,short=False,master=None):
    requests=[]
    def decode(cmd,**kwargs):
        duration=float(cmd[cmd.index('-t')+1]);rate=float(cmd[cmd.index('-af')+1].split('=')[1]) if '-af' in cmd else 1
        name=Path(cmd[cmd.index('-i')+1]).stem
        count=round(duration/rate*SR)-1 if short else math.ceil(duration/rate*SR)+4
        requests.append(cmd)
        return SimpleNamespace(stdout=np.full((max(0,count),2),values[name],np.float32).tobytes())
    p={'media':{r['source']:{'path':r['source']+'.wav'} for r in rows},'audio':rows,'audio_master':master or {}}
    with tempfile.TemporaryDirectory() as folder,patch.object(render.subprocess,'run',side_effect=decode):
        path,stats=render.render_audio(p,Path(folder),start,end,'mock-ffmpeg',Path(folder))
        return (np.fromfile(path,np.float32).reshape(-1,2) if path else None),stats,requests

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--ffmpeg',required=True);a=parser.parse_args();checks=[]
    def check(name,okay):
        if not okay:raise AssertionError(name)
        checks.append(name)
    # Independent hand-calculated boundaries from the actual711-frame reproduction.
    fps=60000/1001;bounds=[0,182/fps,428/fps,711/fps]
    pcm,stats,_=run([entry(str(i),bounds[i],bounds[i+1]) for i in range(3)],0,bounds[-1],{'0':.1,'1':.2,'2':.3})
    check('711f contiguous partitions cover569369 samples once',[(r['begin'],r['end']) for r in stats['piece_sample_windows']]==[(0,145746),(145746,342742),(342742,569369)] and len(pcm)==569369)
    check('join342742 is incoming only and last sample remains populated',np.isclose(pcm[342742,0],.3) and np.isclose(pcm[-1,0],.3))
    pcm,_,_=run([entry('a',.6/SR,5.7/SR),entry('b',3.2/SR,8.4/SR)],0,9/SR,{'a':.2,'b':.3})
    check('deliberate fractional overlap still sums',np.allclose(pcm[:,0],[0,.2,.2,.5,.5,.5,.3,.3,0]))
    pcm,_,_=run([entry('a',0,2/SR),entry('b',4/SR,6/SR)],0,6/SR,{'a':.1,'b':.2})
    check('deliberate silence gap remains',np.allclose(pcm[:,0],[.1,.1,0,0,.2,.2]))
    pcm,stats,calls=run([entry('a',0,6.9/SR),entry('b',6.9/SR,15/SR)],3.3/SR,12.7/SR,{'a':.1,'b':.2})
    check('partial range uses its own shared origin grid',np.allclose(pcm[:,0],[.1]*4+[.2]*5) and abs(float(calls[0][calls[0].index('-ss')+1])-(.5+3.3/SR))<1e-9)
    pcm,stats,_=run([entry('a',0,.2,gain_db=-6,fade_in=.05,fade_out=.05)],.03,.17,{'a':.5})
    tt=np.arange(len(pcm))/SR+.03;expected=.5*10**(-6/20)*np.clip(tt/.05,0,1)*np.clip((.2-tt)/.05,0,1)
    check('explicit gain/fade source clock preserved',np.allclose(pcm[:,0],expected,atol=1e-7))
    pcm,stats,_=run([entry('a',0,.01),entry('b',0,.01)],0,.01,{'a':.7,'b':.7},master={'sample_peak_ceiling_dbfs':-6})
    check('explicit master peak cap retained',np.allclose(pcm,10**(-6/20),atol=1e-7) and stats['samples_over_full_scale_before_master']==960)
    try:run([entry('a',0,.1)],0,.1,{'a':.1},short=True)
    except ValueError as exc:check('underflow rejects instead of fabricating silence','no implicit silence padding' in str(exc))
    else:raise AssertionError('underflow accepted')
    pcm,stats,_=run([entry('a',.1/SR,.2/SR)],0,1/SR,{'a':.1})
    check('zero sample window explicit',stats['piece_sample_windows'][0]['zero_sample_window'] and np.all(pcm==0))
    # Real FFmpeg path is verified on rate1; slowdown support is not inferred.
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);path=root/'native44100.wav';t=np.arange(44100*2)/44100
        left=np.sin(2*np.pi*330*t)*.2;right=np.sin(2*np.pi*510*t)*.15
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(44100);wav.writeframes((np.stack([left,right],axis=1)*32767).astype('<i2').tobytes())
        p={'media':{'a':{'path':str(path)}},'audio':[entry('a',0,.6)]}
        mixed,stats=render.render_audio(p,root,0,.6,a.ffmpeg,root);pcm=np.fromfile(mixed,np.float32).reshape(-1,2)
        check('actual44100Hz stereo rate1 decoder fills28800 samples',len(pcm)==28800 and not np.array_equal(pcm[:,0],pcm[:,1]) and stats['piece_sample_windows'][0]['decoded_samples']>=28800)
    print(json.dumps({'checks_passed':len(checks),'checks':checks,'scope':'Sample scheduling/mixing plus native44100 stereo rate1 decode. No click, performance, or listening approval; rate.5 underflow remains a known rejection.','direct_listening_seconds':0},ensure_ascii=False))
if __name__=='__main__':main()
