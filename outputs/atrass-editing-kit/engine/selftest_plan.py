#!/usr/bin/env python3
"""Check template replacement, unknown-style guards and a real still-source render."""
import argparse,json,tempfile
from pathlib import Path
from PIL import Image
from compile_plan import compile_plan
from render import render,Composer

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ffmpeg',default='ffmpeg');a=ap.parse_args();checks=[]
    with tempfile.TemporaryDirectory(prefix='atrass-plan-test-') as td:
        d=Path(td);Image.new('RGB',(1920,1080),'#304050').save(d/'source.png')
        p={'schema':'atrass-edit-plan-v1','variant':'A','media':{'raw':{'path':'source.png','type':'image','role':'source_footage'}},'takes':[{'source':'raw','source_in':0,'source_out':1.001,'geometry':{'scale':1}}],'events':[{'id':'body','template':'dialogue','in':0,'out':1.001,'text':'검증용 자막입니다'},{'id':'answer','template':'large_emphasis','in':.3,'out':.7,'text':'강조입니다'}]}
        fp=d/'plan.json';fp.write_text(json.dumps(p), encoding='utf-8');compile_plan(fp,d/'project.json');q=json.loads((d/'project.json').read_text(encoding='utf-8'))
        captions=[e for e in q['layers'] if e['kind']=='caption'];assert [(e['in'],e['out']) for e in captions]==[(0,.3),(.7,1.001)];checks.append('large emphasis removes and restores only dialogue')
        assert len({e['id'] for e in q['layers']})==len(q['layers']);checks.append('split layer IDs remain unique')
        assert not q['release_ready'];checks.append('compile does not claim release approval')
        r=render(d/'project.json',d/'render.mp4',draft=True,width=320,ffmpeg=a.ffmpeg);assert r['frame_count']==60;checks.append('compiled still-source timeline renders 60 frames')
        p['events']=[{'id':'body','template':'dialogue','in':0,'out':1.001,'text':'별도 제목과 함께 남는 자막'},{'id':'heading','template':'primitive','kind':'text','font':'bold','z':20,'in':.3,'out':.7,'text':'제목','position':[80,200]}]
        fp.write_text(json.dumps(p), encoding='utf-8');compile_plan(fp,d/'heading.json');q=json.loads((d/'heading.json').read_text(encoding='utf-8'))
        assert [(e['in'],e['out']) for e in q['layers'] if e['kind']=='caption']==[(0,1.001)];checks.append('independent bold heading does not suppress dialogue')
        p['events']=[{'id':'body','template':'dialogue','in':0,'out':1.001,'text':'명언과 대사는 별도 레이어입니다'},{'id':'quote','template':'quote_reveal','font':'body','in':0,'out':1.001,'text':'첫째 줄\n둘째 줄'}]
        fp.write_text(json.dumps(p), encoding='utf-8');compile_plan(fp,d/'quote.json');q=json.loads((d/'quote.json').read_text(encoding='utf-8'));comp=Composer(q,d);layer=next(e for e in q['layers'] if e['id']=='quote')
        frames=[comp.image(layer,t) for t in (0,.2,.8,1)]
        assert len({(im.size,xy) for im,xy in frames})==1 and frames[0][0].getchannel('A').getbbox() is None
        assert frames[2][0].tobytes()==frames[3][0].tobytes();checks.append('character reveal has fixed canvas/anchor and stable completed frame')
        assert len([e for e in q['layers'] if e['kind']=='caption'])==1
        r=render(d/'quote.json',d/'quote.mp4',draft=True,width=320,ffmpeg=a.ffmpeg);assert r['frame_count']==60;checks.append('quote and independent dialogue render together for 60 frames')
        p['variant']='B';p['logo_decision']='synthetic test only';p['events']=[{'id':'chat','template':'chat_panel','in':0,'out':1.001,'bubbles':[]}]
        fp.write_text(json.dumps(p), encoding='utf-8');compile_plan(fp,d/'bchat.json');q=json.loads((d/'bchat.json').read_text(encoding='utf-8'));assert q['layers'][0]['background']=='#A8BDCF';checks.append('B chat uses measured B color rather than A color')
        p.pop('logo_decision')
        for i,(template,variant) in enumerate([('unknown','A'),('question','A'),('dialogue','B')]):
            p['variant']=variant;p['events']=[{'template':template,'in':0,'out':1.001,'text':'확인'}];fp.write_text(json.dumps(p), encoding='utf-8')
            try:compile_plan(fp,d/f'bad{i}.json')
            except ValueError:checks.append('guard '+template+' '+variant)
            else:raise AssertionError('Missing style/asset guard')
    print(json.dumps({'status':'passed','checks':checks,'check_count':len(checks)},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
