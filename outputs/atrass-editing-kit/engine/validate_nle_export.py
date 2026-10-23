#!/usr/bin/env python3
"""Semantic checks for a generated export, not a substitute for Adobe import QA.
Usage: python engine/validate_nle_export.py exports/A-pilot-40s-nle --output result.json
"""
import argparse
import copy
import json
import tempfile
import wave
from pathlib import Path
import xml.etree.ElementTree as ET
import export_nle as n


def validate(out):
    out=Path(out).resolve();m=json.loads((out/'nle-manifest.json').read_text(encoding='utf-8'))
    project=Path(m['project_path']);p=json.loads(project.read_text(encoding='utf-8'));checks=[]
    if n.renderer.sha(project)!=m['project_sha256']:raise ValueError('Source project hash changed; re-export before validation')
    n.preflight(p)
    def ok(name):checks.append({'name':name,'pass':True})
    def reject(name,f):
        try:f()
        except (ValueError,IndexError):ok(name);return
        raise AssertionError(name+' did not reject')
    n.audit(p,m['source_timeline'],m['components'],out,project.parent);ok('Unmodified XML source mapping and component intervals; reveal replay when present')
    for key,val in [('opacity',0),('rotation',1),('x_keys',[[0,0],[1,20]]),('scale',1.25)]:
        q=copy.deepcopy(p);q['layers'][0][key]=val
        reject('Unsupported '+key,lambda:n.preflight(q))
    q=copy.deepcopy(p);q['layers'][0]['character_reveal']={'duration':-1}
    reject('Invalid character reveal cannot be silently flattened',lambda:n.preflight(q))
    if any(e.get('character_reveal') is not None for e in p['layers']):
        q=copy.deepcopy(p);next(e for e in q['layers'] if e.get('character_reveal') is not None)['scale']=1.25
        reject('Reveal top-level scale cannot be silently ignored',lambda:n.preflight(q))
        ok('Character reveal fixed canvas, native sampled states and parent coverage exact')
        reveal=next(r for r in m['components'] if r.get('character_reveal_state'))
        for field in ('missing_state','parent','gap','editable','size','position','PNG','layout'):
            comps=copy.deepcopy(m['components']);r=next(r for r in comps if r['id']==reveal['id'])
            if field=='missing_state':comps.remove(r)
            elif field=='parent':r['character_reveal_state']['parent_event_id']='wrong-parent'
            elif field=='gap':r['out_frame']-=1
            elif field=='editable':r['editable_text']={'text':'unexpected native alternative'}
            elif field=='size':r['size'][0]+=1
            elif field=='position':r['position'][0]+=1
            elif field=='PNG':r['rgba_sha256']='0'*64
            else:r['reveal_layout']['canvas_size'][0]+=1
            reject('Reveal rejects corrupted '+field,lambda:n.audit_reveals(p,comps,out,project.parent))
    q=copy.deepcopy(p);q['clips'].append(copy.deepcopy(q['clips'][0]))
    reject('Duplicate clip identity',lambda:n.preflight(q))
    if p.get('audio'):
        q=copy.deepcopy(p);q['audio'][0]['rate']=.75
        reject('Unsupported audio stretch',lambda:n.preflight(q))
        q=copy.deepcopy(p);q['audio'][0]['source']='intentionally-audio-only'
        reject('Unsupported audio-only media import',lambda:n.preflight(q))
    q=copy.deepcopy(p)
    for c in q['clips']:
        if c.get('source_frame_times'):
            c['source_frame_times'].pop()
            reject('Truncated source frame array',lambda:n.renderer.validate(q,Path(m['project_path']).parent,True));break
    fixture={'fps':'60','duration':4/60,'width':1920,'media':{'raw':{'path':'/synthetic-unread'}},'clips':[{'id':'c','in':0,'out':4/60,'source':'raw','source_in':0,'source_frame_times':[0,1/60,1/60,3/60]}]}
    original=n.probe
    try:
        n.probe=lambda p:{'fps':'60','frames':1000,'width':2560,'height':1440,'duration':1000/60,'start_seconds':0}
        t=n.timeline(fixture,Path('/'))
        assert [(r['start'],r['end'],r['source_in'],r['source_out']) for r in t['runs']]==[(0,2,0,2),(2,3,1,2),(3,4,3,4)]
        ok('Equal FPS runs preserve repeated and skipped source frames')
        fixture['fps']='60000/1001';fixture['duration']=4*1001/60000;fixture['clips'][0]['out']=fixture['duration']
        assert len(n.timeline(fixture,Path('/'))['runs'])==4
        ok('Mixed FPS uses four explicit output frame clips')
    finally:n.probe=original
    for field in ('source_frame','source_identity','layer_interval'):
        with tempfile.TemporaryDirectory(prefix='atrass-nle-corrupt-') as td:
            q=Path(td);tree=ET.parse(out/'premiere.xml');base=tree.find('./sequence/media/video/track/clipitem')
            if field=='source_frame':base.find('in').text=str(int(base.findtext('in'))+1)
            elif field=='source_identity':base.find('file').set('id','intentionally-wrong-file')
            else:
                layer=tree.findall('./sequence/media/video/track')[1].find('clipitem')
                layer.find('start').text=str(int(layer.findtext('start'))+1)
            tree.write(q/'premiere.xml');(q/'after-effects.jsx').write_text((out/'after-effects.jsx').read_text(encoding='utf-8'), encoding='utf-8')
            reject('Reject corrupted '+field,lambda:n.audit(p,m['source_timeline'],m['components'],q))
    if p.get('audio'):
        with wave.open(str(out/'audio-mix.wav'),'rb') as w:
            assert (w.getnchannels(),w.getframerate(),w.getsampwidth(),w.getnframes())==(2,48000,3,m['audio_mix']['samples'])
        ok('WAV is PCM24 stereo 48k and exact declared sample count')
    assert {x['event_id'] for x in m['components']}=={x['id'] for x in p['layers']}
    ok('Every logical visual event has exported components')
    return {'pass':True,'kind':'Semantic export checks; not Adobe application verification','checks':checks,'count':len(checks)}


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('export_dir');ap.add_argument('--output');a=ap.parse_args()
    result=validate(a.export_dir)
    if a.output:n.atomic(a.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
