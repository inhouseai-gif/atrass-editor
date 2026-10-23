#!/usr/bin/env python3
"""Exercise real MP4 AAC endpoints at fractional-frame durations.

Synthetic audio only. Exact PyAV end samples and contiguous PTS are tested;
FFmpeg CLI may expose at most one final AAC packet of codec padding.
"""
import argparse,json,tempfile,wave,hashlib
from fractions import Fraction
from pathlib import Path
import av,numpy as np
from PIL import Image
import render

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ffmpeg',required=True);args=ap.parse_args()
    rows=[]
    with tempfile.TemporaryDirectory(prefix='atrass-mp4-clock-test-') as folder:
        root=Path(folder);Image.new('RGB',(64,36),'#305070').save(root/'still.png')
        values=np.column_stack([np.sin(np.arange(96000)*2*np.pi*f/48000)*8192 for f in (431,673)]).astype('<i2')
        with wave.open(str(root/'tone.wav'),'wb') as out:
            out.setnchannels(2);out.setsampwidth(2);out.setframerate(48000);out.writeframes(values.tobytes())
        for fps,count in [('60000/1001',1),('60000/1001',37),('24',19),('30',29)]:
            duration=float(Fraction(count,1)/Fraction(fps));expected=round(duration*48000)
            p={'width':64,'height':36,'fps':fps,'duration':duration,'media':{'still':{'path':'still.png','type':'image','role':'generated_test'},'tone':{'path':'tone.wav','role':'source_footage'}},'clips':[{'id':'picture','in':0,'out':duration,'source':'still','source_in':0}],'layers':[],'audio':[{'id':'voice','in':0,'out':duration,'source':'tone','source_in':0}]}
            project=root/f'{count}.json';project.write_text(json.dumps(p));output=root/f'{count}.mp4'
            side=render.render(project,output,draft=True,ffmpeg=args.ffmpeg)
            assert side['audio']['samples']==expected
            n=0
            with av.open(str(output)) as container:
                for frame in container.decode(audio=0):
                    assert frame.sample_rate==48000 and len(frame.layout.channels)==2
                    assert abs(float(frame.pts*frame.time_base)-n/48000)<1e-9
                    n+=frame.samples
            assert n==expected,(fps,count,n,expected)
            with av.open(str(output)) as container:
                frames=list(container.decode(video=0))
                assert len(frames)==count
                assert all(abs(float(f.pts*f.time_base)-i/float(Fraction(fps)))<1e-9 for i,f in enumerate(frames))
            raw=render.subprocess.run([args.ffmpeg,'-v','error','-i',str(output),'-map','0:a:0','-f','f32le','-ar','48000','-ac','2','pipe:1'],stdout=render.subprocess.PIPE,check=True).stdout
            cli_samples=len(raw)//8;assert expected<=cli_samples<=expected+1024
            rows.append({'fps':fps,'video_frames':count,'expected_audio_samples':expected,'PyAV_audio_samples':n,'FFmpeg_audio_samples':cli_samples,'audio_contiguous_pts':True,'video_exact_count_and_pts':True})
    print(json.dumps({'pass':True,'fixtures':rows,'renderer_sha256':hashlib.sha256(Path(render.__file__).read_bytes()).hexdigest(),'synthetic_only':True,'direct_listening_seconds':0},indent=2))

if __name__=='__main__':main()
