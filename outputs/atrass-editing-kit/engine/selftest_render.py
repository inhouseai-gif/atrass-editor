#!/usr/bin/env python3
"""Integration smoke test: nonsequential source cuts and important input guards.
Creates its own tiny source; never needs or modifies personal footage.
"""
import argparse,copy,json,tempfile
from pathlib import Path
from fractions import Fraction
import av,numpy as np
from render import render,validate

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ffmpeg',default='ffmpeg');a=ap.parse_args();checks=[]
    with tempfile.TemporaryDirectory(prefix='atrass-engine-test-') as d:
        d=Path(d);src=d/'source.mp4';out=av.open(str(src),'w');s=out.add_stream('libx264',rate=60);s.width=64;s.height=36;s.pix_fmt='yuv420p'
        for i in range(120):
            arr=np.zeros((36,64,3),np.uint8);arr[:]=[200,20,20] if i<60 else [20,200,20]
            f=av.VideoFrame.from_ndarray(arr,format='rgb24');f.pts=i;f.time_base=Fraction(1,60)
            for pkt in s.encode(f):out.mux(pkt)
        for pkt in s.encode():out.mux(pkt)
        out.close()
        p={'width':64,'height':36,'fps':'60','duration':1,'media':{'raw':{'path':str(src),'role':'source_footage'}},'clips':[{'id':'later','in':0,'out':.5,'source':'raw','source_in':1},{'id':'earlier','in':.5,'out':1,'source':'raw','source_in':0}],'layers':[]}
        f=d/'project.json';f.write_text(json.dumps(p), encoding='utf-8');r=render(f,d/'out.mp4',draft=True,ffmpeg=a.ffmpeg)
        with av.open(str(d/'out.mp4')) as c:frames=[x.to_ndarray(format='rgb24') for x in c.decode(video=0)]
        assert len(frames)==60 and r['frame_count']==60
        for idx in [0,29]:assert np.median(frames[idx][:,:,1])>180 and np.median(frames[idx][:,:,0])<40
        for idx in [30,59]:assert np.median(frames[idx][:,:,0])>180 and np.median(frames[idx][:,:,1])<40
        checks+=['60 decoded output frames','backward source seek preserves selected shot','native frame 30 switches shots','reference passthrough false']
        variants=[]
        q=copy.deepcopy(p);q['media']['raw']['role']='reference';variants.append(('reference video blocked',q))
        q=copy.deepcopy(p);q['media']['ref']={'path':str(src),'role':'reference'};q['audio']=[{'id':'bad','in':0,'out':1,'source':'ref','source_in':0}];variants.append(('reference audio blocked',q))
        q=copy.deepcopy(p);q['clips'][0]['source_frame_times']=[0];variants.append(('incomplete native source frame map blocked',q))
        q=copy.deepcopy(p);q['clips'][1]['in']=.55;variants.append(('uncovered timeline blocked',q))
        q=copy.deepcopy(p);q['clips'][1]['source_in']=-1;variants.append(('negative source time blocked',q))
        q=copy.deepcopy(p);q['audio']=[{'id':'bad','in':0,'out':2,'source':'raw','source_in':0}];variants.append(('audio outside timeline blocked',q))
        q=copy.deepcopy(p);q['layers']=[{'id':'same','kind':'rect','in':0,'out':1}]*2;variants.append(('duplicate layer cache keys blocked',q))
        for name,q in variants:
            try:validate(q,d,True)
            except ValueError:checks.append(name)
            else:raise AssertionError(name)
        try:validate(p,d,False)
        except ValueError:checks.append('unverified project blocked in release mode')
        else:raise AssertionError('release gate')
    print(json.dumps({'status':'passed','checks':checks,'check_count':len(checks)},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
