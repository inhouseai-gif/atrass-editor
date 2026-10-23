#!/usr/bin/env python3
"""Small byte-identity/alias tests. Fixtures are bytes, not playable media."""
from pathlib import Path
import copy
import hashlib
import json
import tempfile

from relink_by_identity import relink_by_identity, source_pairs


def main():
    checks=[]
    def check(condition,label):
        assert condition,label
        checks.append(label)
    with tempfile.TemporaryDirectory(prefix='atrass-identity-relink-') as temp:
        root=Path(temp).resolve(); source=root/'새 원본=fixture.bin'; source.write_bytes(b'synthetic identity fixture')
        other=root/'other.bin'; other.write_bytes(b'unchanged fixture')
        digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        target=digest(source); original={
            'schema':'atrass-render-v1','release_ready':False,
            'media':{'first':{'path':'missing-original.mov','sha256':target},
                     'second':{'path':'another-missing-original.mov','sha256':target.upper()},
                     'unchanged':{'path':'other.bin','sha256':digest(other)},
                     'unidentified':{'path':'other.bin'}},
            'clips':[{'media':'first','source_in':1,'in':0,'out':2}],
            'audio':[{'media':'second','source_in':3,'in':0,'out':2}],
            'fonts':{'fixture':{'path':'other.bin'}},
            'historical_evidence':{'path':'old/machine/path','approved':False}}
        project=root/'project.json'
        def save(value):project.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        save(original); before=project.read_bytes(); out=root/'relocated.json'
        report=relink_by_identity(project,out,{target:str(source)})
        expected=copy.deepcopy(original)
        for key in ['first','second']:expected['media'][key].update(path=str(source),sha256=target)
        check(json.loads(out.read_text())==expected,'Only both matching aliases path/sha fields changed')
        check(project.read_bytes()==before,'Original project bytes unchanged')
        check(source.read_bytes()==b'synthetic identity fixture','Source bytes unchanged')
        check(report['identity_groups']==[{'sha256':target,'media_ids':['first','second']}],'All aliases grouped once')
        check({x['id']for x in report['verified_media']}=={'first','second'},'Both aliases independently reported')
        check(report['source_identity_inferred_from_filename'] is False,'No filename identity inference')
        check(source_pairs([target+'='+str(source)])=={target:str(source)},'Unicode spaces and equals in path preserved')

        def fails(label,fn,output=None):
            try:fn()
            except (ValueError,OSError,KeyError,TypeError):
                check(output is None or not output.exists(),label+' leaves no output')
            else:raise AssertionError('Did not reject: '+label)
        for label,values in [('empty sources',[]),('bad digest',['bad='+str(source)]),
                             ('empty path',[target+'=']),('missing separator',[target]),
                             ('duplicate case-insensitive digest',[target+'='+str(source),target.upper()+'='+str(source)])]:
            fails(label,lambda v=values:source_pairs(v))
        fails('unknown valid digest',lambda:relink_by_identity(project,root/'unknown.json',{'0'*64:str(source)}),root/'unknown.json')
        fails('wrong replacement content',lambda:relink_by_identity(project,root/'wrong.json',{target:str(other)}),root/'wrong.json')
        fails('missing replacement',lambda:relink_by_identity(project,root/'missing.json',{target:str(root/'missing.bin')}),root/'missing.json')
        saved=out.read_bytes();fails('existing destination',lambda:relink_by_identity(project,out,{target:str(source)}))
        check(out.read_bytes()==saved,'Existing destination bytes preserved')
        moved=root/'elsewhere';moved.mkdir()
        fails('different output directory',lambda:relink_by_identity(project,moved/'out.json',{target:str(source)}),moved/'out.json')
        changed=copy.deepcopy(original);changed['media']['first']['path']='other.bin';save(changed)
        fails('existing old source contradicts stored identity',lambda:relink_by_identity(project,root/'conflict.json',{target:str(source)}),root/'conflict.json')
        changed=copy.deepcopy(original);changed['media']['unchanged']['path']='missing-unrelinked.bin';save(changed)
        fails('unrelinked media missing',lambda:relink_by_identity(project,root/'unrelinked.json',{target:str(source)}),root/'unrelinked.json')
        changed=copy.deepcopy(original);changed['fonts']['fixture']['path']='missing-font';save(changed)
        fails('font missing',lambda:relink_by_identity(project,root/'font.json',{target:str(source)}),root/'font.json')
        changed=copy.deepcopy(original);changed['schema']='unknown';save(changed)
        fails('wrong project schema',lambda:relink_by_identity(project,root/'schema.json',{target:str(source)}),root/'schema.json')
        changed=copy.deepcopy(original);changed['media']['first'].pop('sha256');changed['media']['second'].pop('sha256');save(changed)
        fails('identity absent cannot infer from old path',lambda:relink_by_identity(project,root/'undeclared.json',{target:str(source)}),root/'undeclared.json')
    print(json.dumps({'status':'passed','count':len(checks),'checks':checks,'playable_media_used':False},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
