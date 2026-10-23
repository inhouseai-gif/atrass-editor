#!/usr/bin/env python3
"""Rebind explicit asset/chapter/chat events after a source retake decision.

Source windows/text/design stay literal. Only retained take IDs and project hash
change. An event removed by the cut is rejected for explicit editorial handling.
"""
from pathlib import Path
import argparse,copy,json,os
import apply_source_assets as shared


def rebind(events,project,retake_receipt,output):
    events,project,retake_receipt=map(lambda x:Path(x).resolve(),(events,project,retake_receipt));output=Path(output).absolute();receipt_path=output.with_suffix('.rebind.json')
    shared.require(not output.exists()and not receipt_path.exists(),'Choose new event/provenance paths')
    spec,esha=shared.read(events);p,psha=shared.read(project);r,rsha=shared.read(retake_receipt)
    shared.require(spec['schema']in('atrass-source-assets-v1','atrass-source-chapters-v1','atrass-source-chat-v1'),'Unsupported event schema')
    shared.require(r['schema']=='atrass-source-retake-cuts-v1'and spec['source_sha256']==r['source_sha256'],'Retake/source identity')
    plan_path=shared.path_at(project.parent,p['plan_source']);plan,plan_sha=shared.read(plan_path)
    shared.require(plan['source_dialogue_assembly']['input_sha256']['decisions']==r['output_decisions_sha256'],'New plan is not from bound retake output')
    shared.require({m['sha256']for m in p['media'].values()if m.get('role')=='source_footage'}=={spec['source_sha256']},'New raw project identity')
    clipmap={c['id']:c for c in p['clips']};expected={child['id']:child for children in r['take_rebinding'].values()for child in children}
    shared.require(set(clipmap)==set(expected),'New project take inventory differs')
    for tid,c in clipmap.items():
        span=expected[tid]['source_range'];shared.require(c['source_in']==span[0]and c.get('rate',1)==1,'New project take source differs')
        shared.require(abs((c['out']-c['in'])-(span[1]-span[0]))<=.5/float(shared.Fraction(p['fps']))+1e-8,'New project take duration differs')
    group='panels'if spec['schema']=='atrass-source-chat-v1'else'events';result=copy.deepcopy(spec);result['project_sha256']=psha;changes=[]
    for ev in result[group]:
        ids=[];lo,hi=ev['source_range'];before=list(ev['take_ids'])
        for old in before:
            shared.require(old in r['take_rebinding'],'Unknown original event take')
            for child in r['take_rebinding'][old]:
                a,z=child['source_range']
                if lo<z and hi>a:ids.append(child['id'])
        shared.require(ids and len(ids)==len(set(ids)),'Event was entirely removed or IDs repeat: '+ev['id'])
        ev['take_ids']=ids;changes.append({'event_id':ev['id'],'old_take_ids':before,'new_take_ids':ids,'source_range':ev['source_range']})
        if 'asset'in ev:
            for row in[ev['asset'],ev['asset']['provenance']]:
                actual=shared.path_at(events.parent,row['path']);shared.require(shared.sha(actual)==row['sha256'],'Asset/provenance SHA before rebasing');row['path']=os.path.relpath(actual,output.parent.resolve())
    shared.require(shared.sha(events)==esha and shared.sha(project)==psha and shared.sha(retake_receipt)==rsha and shared.sha(plan_path)==plan_sha,'Inputs changed during source event rebinding')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8')as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    receipt={'schema':'atrass-retake-event-rebinding-v1','old_events_path':str(events),'old_events_sha256':esha,'new_project_sha256':psha,'retake_receipt_sha256':rsha,'new_events_sha256':shared.sha(output),'implementation_sha256':shared.sha(Path(__file__)),'events':changes,'source_windows_words_and_design_unchanged':True,'release_ready':False}
    with receipt_path.open('x',encoding='utf-8')as f:json.dump(receipt,f,ensure_ascii=False,indent=2);f.write('\n')
    return receipt

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for key in('events','project','retake-receipt','output'):ap.add_argument('--'+key,required=True)
    a=ap.parse_args();result=rebind(a.events,a.project,a.retake_receipt,a.output);print(json.dumps({'events':len(result['events']),'release_ready':False}))
