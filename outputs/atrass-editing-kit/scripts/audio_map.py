#!/usr/bin/env python3
"""Compare provided raw/reference mono8k PCM16 WAVs. Research candidates only."""
import argparse,os,sys,subprocess,shutil,json,hashlib,wave
from pathlib import Path

def inspect(path):
    with wave.open(str(path),'rb') as w:
        if (w.getnchannels(),w.getframerate(),w.getsampwidth())!=(1,8000,2):
            raise ValueError(f"Expected mono 8000Hz PCM16 WAV: {path}")
        frames=w.getnframes()
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return {'origin':str(path),'sha256':h.hexdigest(),'samples':frames,'sample_rate':8000}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--raw-wav',required=True,type=Path)
    ap.add_argument('--reference-wav',required=True,type=Path)
    ap.add_argument('--output-dir',required=True,type=Path)
    a=ap.parse_args();out=a.output_dir.resolve()
    if out.exists() and any(out.iterdir()):ap.error('Output directory must be empty; existing analysis is preserved.')
    raw=a.raw_wav.resolve();ref=a.reference_wav.resolve();meta={'raw':inspect(raw),'reference':inspect(ref),'status':'research_candidates','audio_listened_seconds':0}
    out.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(raw,out/'raw-mono-8k.wav');shutil.copyfile(ref,out/'finalA-mono-8k.wav')
    env=os.environ.copy();env['ATRASS_AUDIO_OUTPUT_DIR']=str(out)
    workers=Path(__file__).parent/'_audio'
    for name in ['align_audio.py','refine_audio.py']:
        subprocess.run([sys.executable,str(workers/name)],env=env,check=True,stdout=subprocess.DEVNULL)
    import numpy as np
    rows=json.loads((out/'audio-align-refined.json').read_text())['rows'];valid=[r for r in rows if r['correlation']>.8];groups=[]
    for r in valid:
        if groups and abs(r['offset']-np.median([x['offset'] for x in groups[-1]]))<.01:groups[-1].append(r)
        else:groups.append([r])
    data={'groups':[{'first_anchor':g[0]['final_start'],'last_anchor':g[-1]['final_start'],'offset_median':float(np.median([r['offset'] for r in g])),'n':len(g)} for g in groups]}
    (out/'audio-offset-groups.json').write_text(json.dumps(data,indent=2))
    for name in ['map_audio_segments.py','refine_boundaries.py']:
        subprocess.run([sys.executable,str(workers/name)],env=env,check=True,stdout=subprocess.DEVNULL)
    meta.update(query_count=len(rows),high_correlation_query_count=len(valid),offset_groups=len(groups),median_correlation=float(np.median([r['correlation'] for r in rows])),limitations=['No direct listening','No exact video frame guarantee','No certified boundary error bound','Noise/music/silence and short cuts can cause false matches','Original media untouched; working copies of WAVs included'])
    (out/'alignment-run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    print(json.dumps({'status':'research_candidates','query_count':len(rows),'high_correlation_queries':len(valid),'offset_groups':len(groups),'output':str(out)},ensure_ascii=False))
if __name__=='__main__':main()
