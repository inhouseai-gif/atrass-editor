#!/usr/bin/env python3
"""Execute explicit spacing/punctuation edits without changing spoken characters.
Exact old text and project SHA are required; words/digits/source/audio/time stay.
"""
from pathlib import Path
import argparse,copy,json,os,unicodedata
from apply_source_assets import read,sha,keys,require,relative
from render import validate,Composer
from compile_plan import complete_emphasis_mask
PUNCTUATION=set('.,!?“”‘’"\'')
def spoken_characters(text):return ''.join(c for c in text if not c.isspace() and c not in PUNCTUATION)
def transform(p,spec,measure):
    keys(spec,('schema','project_sha256','edits','evidence'),('schema','project_sha256','edits','evidence'),'caption copy decision')
    require(spec['schema']=='atrass-caption-copy-v1' and isinstance(spec['evidence'],str) and spec['evidence'].strip(),'Explicit copy evidence required')
    require(p['variant']=='A' and (p['width'],p['height'])==(1920,1080),'A 1080p only')
    require('source_caption_copy' not in p and spec['edits'],'New nonempty copy stage required')
    edits=spec['edits'];require(len({x['id'] for x in edits})==len(edits),'Duplicate copy IDs')
    result=copy.deepcopy(p);by={x['id']:x for x in result['layers']};records=[]
    for edit in edits:
        keys(edit,('id','old','new','reason'),('id','old','new','reason'),'copy edit')
        require(isinstance(edit['reason'],str) and edit['reason'].strip(),'Copy reason required')
        e=by[edit['id']];old=copy.deepcopy(e);text=edit['new']
        require(e['kind']=='caption' and e.get('source_dialogue_binding') and e['text']==edit['old'],'Old source caption differs')
        require(isinstance(text,str) and text==text.strip() and text and len(text)<=256 and text!=e['text'],'Genuine nonempty copy change required')
        require(unicodedata.normalize('NFC',text)==text and not any(unicodedata.category(c) in ('Cc','Cf','Cs','Zl','Zp') for c in text),'NFC single-line text required')
        require(spoken_characters(text)==spoken_characters(e['text']),'Words or digits changed; this tool permits spacing/punctuation only')
        require(not any(k in e for k in ('glyph_width','glyph_height','scale_x','scale_y','rotation','shadow','character_reveal')),'Custom caption needs a separate geometry policy')
        e['text']=text;w,h=measure(e);require(0<w<=1640 and 0<h<=64,'Copy does not fit; do not clip or shrink')
        left=round(960-(w+32)/2);e['box']=[left,938,left+w+32,1018];e['text_offset']=[16,round((80-h)/2)]
        records.append({'id':e['id'],'old_text':old['text'],'new_text':text,'reason':edit['reason'],'old_box':old['box'],'new_box':e['box'],'old_text_offset':old['text_offset'],'new_text_offset':e['text_offset'],'source_binding_unchanged':True})
    return result,records
def apply(project,decision,output):
    project,decision,output=[Path(x).absolute() for x in (project,decision,output)]
    require(output.suffix=='.json' and output.parent.resolve()==project.parent.resolve(),'New JSON must be beside input');require(not os.path.lexists(output),'Output exists')
    p,ph=read(project);s,sh=read(decision);require(ph==s['project_sha256'],'Project SHA mismatch');validate(p,project.parent,True)
    comp=Composer(p,project.parent)
    def measure(e):
        m=comp.text_mask(e['text'],e);whole=complete_emphasis_mask(comp,e['text'],e)
        require(m.size==whole.size and m.tobytes()==whole.tobytes(),'Incomplete glyph raster');return m.size
    q,records=transform(p,s,measure)
    q['source_caption_copy']={'schema':'atrass-caption-copy-v1','input_project':relative(project,output.parent),'input_sha256':ph,'decision':relative(decision,output.parent),'decision_sha256':sh,'implementation_sha256':sha(Path(__file__)),'records':records,'spoken_characters_and_timing_audio_preserved':True,'release_ready':False};q['release_ready']=False;validate(q,output.parent,True)
    require(sha(project)==ph and sha(decision)==sh,'Copy input changed')
    with output.open('x',encoding='utf-8') as f:json.dump(q,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    return {'output':str(output),'sha256':sha(output),'changed_captions':len(records),'audio_unchanged':q['audio']==p['audio'],'release_ready':False}
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for n in ('project','decision','output'):ap.add_argument('--'+n,required=True)
    a=ap.parse_args();print(json.dumps(apply(a.project,a.decision,a.output),ensure_ascii=False))
