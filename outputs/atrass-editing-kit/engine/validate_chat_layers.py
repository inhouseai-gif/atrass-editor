#!/usr/bin/env python3
"""Independent chat source/output semantics; no change to the adapter.

python engine/validate_chat_layers.py --composites references/A-chat-composite-schema-example.json \
  --id A-chat-04 --project projects/A-chat-04-reconstruction-v2.json --self-test --output result.json
"""
from __future__ import annotations
import argparse,copy,hashlib,json,math,sys,tempfile
from fractions import Fraction
from pathlib import Path


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def require(value,message):
    if not value:raise ValueError(message)
def span(v):
    a,b=v.get('in_frame'),v.get('out_frame')
    require(type(a)is int and type(b)is int and 0<=a<b,'Invalid native interval')
    return a,b
def box(v,label):
    require(isinstance(v,list) and len(v)==4 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in v),'Invalid box '+label)
    require(0<=v[0]<v[2]<=1920 and 0<=v[1]<v[3]<=1080,'Box outside canvas '+label)
    return v
def inside(child,parent,label):
    require(parent[0]<=child[0]<child[2]<=parent[2] and parent[1]<=child[1]<child[3]<=parent[3],'Geometry outside parent '+label)
def chain(states,bounds,label):
    require(bool(states),'Missing states '+label);cursor=bounds[0]
    for s in states:
        a,b=span(s);require(a==cursor and b<=bounds[1],'State gap/overlap/outside '+label);cursor=b
    require(cursor==bounds[1],'Uncovered lifetime '+label)
def geometry_fields(v):
    return {k:x for k,x in v.items() if 'bbox' in k or k in ('text_line_origins_1080','line_top_deltas_px','line_gaps_px','corner_radius_px_approx','bubble_corner_radius_px_approx')}


def validate_source(c):
    """Independent checks use both representations rather than adapter internals."""
    require(c.get('coordinate_space')==[1920,1080],'Wrong coordinate space')
    require(c.get('interval_convention')=='half_open_zero_based_native_frames','Undeclared half-open convention')
    r=c['frame_rate'];require(type(r['num'])is int and type(r['den'])is int and r['num']>0 and r['den']>0,'Invalid rational frame rate')
    bounds=span(c);states=c['layout_states'];chain(states,bounds,c['id'])
    require(len({s['id'] for s in states})==len(states),'Duplicate state identity')
    components=[c[k] for k in ('panel','sender','avatar')]+c['messages']
    ids=[c['id']]+[v['id'] for v in components]
    require(len(ids)==len(set(ids)),'Duplicate component/message identity')
    for v in components:
        a,b=span(v);require(bounds[0]<=a<b<=bounds[1],'Component outside composite '+v['id'])
        chain(v['layout_states'],(a,b),v['id'])
        expected=[(max(a,s['in_frame']),min(b,s['out_frame'])) for s in states if max(a,s['in_frame'])<min(b,s['out_frame'])]
        require([span(s) for s in v['layout_states']]==expected,'Component stages differ from parent '+v['id'])
    for key in ('panel','sender','avatar'):require(span(c[key])==bounds,'Persistent component lifetime differs '+key)
    messages={m['id']:m for m in c['messages']}
    for m in messages.values():
        require(m.get('text_verified') and m.get('timing_verified'),'Unverified message '+m['id'])
        lines=m['text'].split('\n')
        if 'lines' in m:require(m['lines']==lines,'Canonical line text differs '+m['id'])
        if 'line_count' in m:require(m['line_count']==len(lines),'Line count differs '+m['id'])
        if len(lines)>1:
            for s in m['layout_states']:
                lbs=s.get('text_line_glyph_bboxes_1080');require(isinstance(lbs,list) and len(lbs)==len(lines),'Missing measured multiline geometry '+m['id'])
                for b in lbs:inside(box(b,m['id']),s['text_glyph_bbox_1080'],m['id']+' line')
                require(all(lbs[i][3]<=lbs[i+1][1] for i in range(len(lbs)-1)),'Multiline boxes overlap or are not top-to-bottom')
                if 'text_line_origins_1080' in s:require(s['text_line_origins_1080']==[b[:2] for b in lbs],'Line origins differ')
                if 'line_top_deltas_px' in s:require(s['line_top_deltas_px']==[lbs[i+1][1]-lbs[i][1] for i in range(len(lbs)-1)],'Line top deltas differ')
                if 'line_gaps_px' in s:require(s['line_gaps_px']==[lbs[i+1][1]-lbs[i][3] for i in range(len(lbs)-1)],'Line gaps differ')
    for stage in states:
        sb=span(stage);panel=box(stage['panel']['bbox_1080'],'panel')
        for name in ('panel','sender','avatar'):
            row=stage[name];require(span(row)==sb,'Parent-state child interval differs '+name)
            source=next(s for s in c[name]['layout_states'] if span(s)==sb)
            require(geometry_fields(row)==geometry_fields(source),'Mirrored geometry differs '+name)
        inside(box(stage['sender']['glyph_bbox_1080'],'sender'),panel,'sender')
        av=stage['avatar'];inside(box(av.get('asset_bbox_1080',av['bbox_1080']),'avatar asset'),panel,'avatar')
        active={mid for mid,m in messages.items() if m['in_frame']<=sb[0]<m['out_frame']}
        rows=stage['messages'];mids=[m['message_id'] for m in rows]
        require(len(mids)==len(set(mids)) and set(mids)==active,'Wrong active message membership')
        for row in rows:
            m=messages[row['message_id']];expected=(max(sb[0],m['in_frame']),min(sb[1],m['out_frame']))
            require(span(row)==expected,'Parent-state message interval differs '+m['id'])
            source=next(s for s in m['layout_states'] if span(s)==expected)
            require(geometry_fields(row)==geometry_fields(source),'Mirrored message geometry differs '+m['id'])
            bubble=box(row['bubble_bbox_1080'],m['id']);inside(bubble,panel,m['id'])
            inside(box(row['text_glyph_bbox_1080'],m['id']+' text'),bubble,m['id']+' text')
    photo=c.get('adjacent_photo_asset')
    if photo:
        a,b=span(photo);require(bounds[0]<=a<b<=bounds[1],'Photo outside composite');box(photo['bbox_1080'],'adjacent photo')
        require(bool(photo.get('asset')),'Missing photo asset path')
    return True


def expected_layers(c,origin,end,avatar_source='chat-avatar',photo_source='chat-photo'):
    """Render contract derived from measured components, without calling compiler."""
    require(type(origin)is int and type(end)is int and 0<=origin<end,'Invalid target range')
    fps=Fraction(c['frame_rate']['num'],c['frame_rate']['den']);rows=[]
    def emit(v,s,kind,b,fill=None,text=None,radius=None,z=0):
        lo=max(origin,c['in_frame'],v['in_frame'],s['in_frame']);hi=min(end,c['out_frame'],v['out_frame'],s['out_frame'])
        if lo>=hi:return
        x,y,x2,y2=b
        row={'semantic_layer_id':v['id'],'kind':kind,'source_reference_frames':[lo,hi],'in':float(Fraction(lo-origin,1)/fps),'out':float(Fraction(hi-origin,1)/fps),'position':[x,y],'z':z}
        if kind=='text':row.update(text=text,fill=fill,glyph_width=x2-x,glyph_height=y2-y)
        else:row['size']=[x2-x,y2-y]
        if kind=='rect':row.update(fill=fill,radius=radius)
        if kind=='image':row['source']=avatar_source
        rows.append(row)
    for s in c['panel']['layout_states']:emit(c['panel'],s,'rect',s['bbox_1080'],c['panel']['fill_observed_decoded_rgb'],radius=s['corner_radius_px_approx'],z=30)
    for s in c['sender']['layout_states']:emit(c['sender'],s,'text',s['glyph_bbox_1080'],c['sender']['fill_observed_decoded_rgb'],c['sender']['text'],z=33)
    for s in c['avatar']['layout_states']:emit(c['avatar'],s,'image',s.get('asset_bbox_1080',s['bbox_1080']),z=32)
    for m in c['messages']:
        for s in m['layout_states']:
            emit(m,s,'rect',s['bubble_bbox_1080'],m['bubble']['fill_observed_decoded_rgb'],radius=s['bubble_corner_radius_px_approx'],z=34)
            lines=m['text'].split('\n');boxes=s.get('text_line_glyph_bboxes_1080',[s['text_glyph_bbox_1080']])
            if len(lines)==1:boxes=[s['text_glyph_bbox_1080']]
            require(len(lines)==len(boxes),'Missing multiline boxes')
            for line,b in zip(lines,boxes):emit(m,s,'text',b,m['text_style']['fill_observed_decoded_rgb'],line,z=35)
    photo=c.get('adjacent_photo_asset')
    if photo:
        photo={**photo,'id':c['id']+'-photo'};before=len(rows);emit(photo,photo,'image',photo['bbox_1080'],z=36)
        if len(rows)>before:rows[-1]['source']=photo_source
    return rows


def compare_layers(c,layers,origin,end,avatar_source='chat-avatar'):
    wanted=expected_layers(c,origin,end,avatar_source);known={r['semantic_layer_id'] for r in wanted}
    actual=[v for v in layers if v.get('semantic_layer_id') in known or str(v.get('id','')).startswith(c['id']+'-')]
    require(len(actual)==len(wanted),'Chat layer count differs: '+str((len(actual),len(wanted))))
    require(len({x['id'] for x in actual})==len(actual),'Duplicate compiled layer ID')
    for row in actual:
        require(row.get('character_reveal') is None and not any(k.endswith('_keys') and v for k,v in row.items()),'Static chat state unexpectedly contains animation')
    pending=actual[:]
    for e in wanted:
        found=[]
        for a in pending:
            if all((abs(a.get(k,-1)-v)<1e-9 if k in ('in','out') else a.get(k)==v) for k,v in e.items()):found.append(a)
        require(len(found)==1,'Missing/duplicated/mutated layer '+json.dumps(e,ensure_ascii=False))
        pending.remove(found[0])
    return actual


def validate_project(c,p,project_path,avatar_source='chat-avatar'):
    validate_source(c);lo,hi=p['reference_range_frames'];fps=Fraction(c['frame_rate']['num'],c['frame_rate']['den'])
    require(Fraction(p['fps'])==fps,'Project/composite frame rates differ')
    require(abs(p['duration']-float(Fraction(hi-lo,1)/fps))<1e-8,'Project frame duration differs')
    rows=compare_layers(c,p['layers'],lo,hi,avatar_source)
    asset=p.get('media',{}).get(avatar_source);require(asset is not None,'Avatar media missing')
    path=Path(asset['path']);path=path if path.is_absolute() else Path(project_path).parent/path
    require(path.is_file(),'Avatar asset file missing: '+str(path))
    prov=c['avatar'].get('asset_provenance',{})
    if prov.get('sha256'):require(sha(path)==prov['sha256'],'Avatar asset hash mismatch')
    from PIL import Image
    with Image.open(path) as im:
        require(im.mode=='RGBA','Avatar must retain explicit alpha')
        if prov.get('size_px'):require(list(im.size)==prov['size_px'],'Avatar dimensions differ from provenance')
    photo=c.get('adjacent_photo_asset')
    if photo:
        media=p.get('media',{}).get('chat-photo');require(media is not None,'Adjacent photo media missing')
        path=Path(media['path']);path=path if path.is_absolute() else Path(project_path).parent/path
        require(path.is_file(),'Adjacent photo file missing: '+str(path));provenance=photo.get('provenance',{})
        if provenance.get('sha256'):require(sha(path)==provenance['sha256'],'Adjacent photo hash differs')
        with Image.open(path) as im:
            if provenance.get('size_px'):require(list(im.size)==provenance['size_px'],'Adjacent photo dimensions differ')
    if c['panel'].get('shadow',{}).get('observed'):
        require(any(x.get('component')==c['panel']['id'] and x.get('unimplemented_property')=='shadow' for x in p.get('unresolved_properties',[])),'Unmeasured panel shadow was silently omitted')
    return rows


def semantic_tests(c,p,project_path):
    import chat_layers as adapter
    import render
    require(c['id']=='A-chat-04','Fixed corruption/self-test fixture targets A-chat-04; use validation without --self-test for other composites')
    lo,hi=p['reference_range_frames'];fps=Fraction(p['fps']);tests=[];findings=[]
    def good(name,fn):fn();tests.append({'name':name,'pass':True})
    def reject(name,fn):
        try:fn()
        except ValueError:tests.append({'name':name,'pass':True});return
        raise AssertionError(name+' was accepted')
    layers,review=adapter.compile_chat(c,lo,hi,'chat-avatar')
    good('Adapter output matches independent measured-layer contract',lambda:compare_layers(c,layers,lo,hi))
    for a,b in ((lo+111,lo+250),(lo-3,hi+4),(hi+2,hi+4)):
        part,_=adapter.compile_chat(c,a,b,'chat-avatar');good('Target-window clipping '+str((a-lo,b-lo)),lambda:compare_layers(c,part,a,b))
    if c['id']=='A-chat-04':
        checkpoints=[(0,1,339),(112,1,339),(113,2,297),(247,2,297),(248,3,255),(456,3,255),(457,0,None)]
        for f,count,y in checkpoints:
            active=[v for v in layers if v['in']<=float(Fraction(f,1)/fps)<v['out']]
            texts=[v for v in active if v['kind']=='text' and '-message-' in v['semantic_layer_id']]
            avatars=[v for v in active if v['kind']=='image']
            require(len(texts)==count,'Half-open message accumulation differs at '+str(f))
            require((len(avatars)==1 and avatars[0]['position']==[117,y] and avatars[0]['size']==[57,57]) if count else not avatars,'Avatar asset bbox differs at '+str(f))
        tests.append({'name':'A-chat-04 fixed f112/113 and f247/248 accumulation, avatar y339/297/255, exclusive f457','pass':True})
    mutations={}
    q=copy.deepcopy(c);q['in_frame']+=1;mutations['parent lifetime contradiction']=q
    q=copy.deepcopy(c);q['messages'][1]['id']=q['messages'][0]['id'];mutations['duplicate message identity']=q
    q=copy.deepcopy(c);q['panel']['layout_states'][1]['in_frame']-=1;mutations['component layout overlap']=q
    q=copy.deepcopy(c);q['messages'][0]['layout_states'][0]['out_frame']-=1;mutations['message layout gap']=q
    q=copy.deepcopy(c);q['layout_states'][1]['messages'].pop();mutations['missing retained message in parent state']=q
    q=copy.deepcopy(c);q['layout_states'][2]['messages'][0]=copy.deepcopy(q['layout_states'][0]['messages'][0]);mutations['mirrored message interval from wrong parent stage']=q
    q=copy.deepcopy(c);q['layout_states'][0]['avatar']['out_frame']-=1;mutations['mirrored avatar interval mismatch']=q
    q=copy.deepcopy(c);q['frame_rate']['num']=-60000;mutations['negative frame rate']=q
    q=copy.deepcopy(c)
    for a in (q['messages'][0]['layout_states'][0],q['layout_states'][0]['messages'][0]):a['text_glyph_bbox_1080']=[1,1,50,20]
    mutations['message text outside its bubble']=q
    for name,q in mutations.items():
        reject('Independent validator rejects '+name,lambda:validate_source(q))
        try:adapter.compile_chat(q,lo,hi,'chat-avatar')
        except ValueError:result='explicit_ValueError'
        except Exception as e:result=type(e).__name__
        else:result='accepted'
        findings.append({'mutation':name,'adapter_result':result,'independent_validator_result':'explicit_ValueError'})
    reject('Missing avatar source rejected by adapter',lambda:adapter.compile_chat(c,lo,hi,None))
    q=copy.deepcopy(p);q['media']['chat-avatar']['path']='/intentionally-missing-avatar.png'
    reject('Missing avatar file rejected by full renderer validation',lambda:render.validate(q,Path(project_path).parent,True))
    reject('Missing avatar file rejected independently',lambda:validate_project(c,q,project_path))
    q=copy.deepcopy(p);q['media'].pop('chat-avatar')
    reject('Missing avatar media identity',lambda:validate_project(c,q,project_path))
    q=copy.deepcopy(p);next(l for l in q['layers'] if l.get('semantic_layer_id')==c['avatar']['id'])['position']=[119,342]
    reject('Circle bbox cannot replace padded asset bbox',lambda:validate_project(c,q,project_path))
    q=copy.deepcopy(p);next(l for l in q['layers'] if l.get('semantic_layer_id')==c['messages'][0]['id'] and l['kind']=='text')['text']='altered message'
    reject('Message text identity preserved',lambda:validate_project(c,q,project_path))
    q=copy.deepcopy(p);next(l for l in q['layers'] if l.get('semantic_layer_id')==c['panel']['id'])['out']+=1/float(fps)
    reject('Compiled one-frame overlap rejected',lambda:validate_project(c,q,project_path))
    q=copy.deepcopy(p);next(l for l in q['layers'] if l.get('semantic_layer_id')==c['sender']['id'])['character_reveal']={}
    reject('Static layout cannot silently become a character animation',lambda:validate_project(c,q,project_path))
    return {'checks':tests,'count':len(tests),'all_independent_checks_pass':True,'adapter_negative_fixture_results':findings,'adapter_remaining_accepted_invalid_cases':[x['mutation'] for x in findings if x['adapter_result']=='accepted']}


def extended_tests(path,base_project,project_path):
    """Six measured chats, three multiline messages and the photo hard cut."""
    import chat_layers as adapter
    import render
    data=json.loads(Path(path).read_text(encoding='utf-8'));items=data['chat_composites'];tests=[];findings=[];compiled={};kit=Path(__file__).resolve().parents[1]
    def ok(name):tests.append({'name':name,'pass':True})
    def rejected(fn):
        try:fn()
        except ValueError:return 'explicit_ValueError'
        except Exception as e:return type(e).__name__
        return 'accepted'
    multiline=[]
    for c in items:
        validate_source(c);lo,hi=span(c);layers,review=adapter.compile_chat(c,lo,hi,'chat-avatar',photo_source='chat-photo' if c.get('adjacent_photo_asset') else None)
        compare_layers(c,layers,lo,hi);compiled[c['id']]={'layers':layers,'review':review};ok(c['id']+' source contract and all compiled static layers')
        for m in c['messages']:
            if '\n' not in m['text']:continue
            multiline.append(m['id'])
            for s in m['layout_states']:
                expected=m['text'].split('\n');actual=[e for e in layers if e.get('semantic_layer_id')==m['id'] and e['kind']=='text' and e['source_reference_frames']==[s['in_frame'],s['out_frame']]]
                actual.sort(key=lambda e:e['position'][1]);require([e['text'] for e in actual]==expected,'Multiline text lost or reordered')
                require([e['glyph_height'] for e in actual]==[26,26] and actual[1]['position'][1]-actual[0]['position'][1]==40,'Measured line heights/top delta changed')
                require(s['line_gaps_px']==[14],'Measured14px line gap changed')
            ok(m['id']+' all placements retain two lines,26px heights,40px top delta,14px gap')
    require(sorted(multiline)==['A-chat-05-message-01','A-chat-05-message-02','A-chat-06-message-03'],'Expected measured multiline fixture set changed')
    c=next(x for x in items if x['id']=='A-chat-05');lo,hi=span(c);layers=compiled[c['id']]['layers'];fps=Fraction(c['frame_rate']['num'],c['frame_rate']['den']);photo=c['adjacent_photo_asset']
    require(span(photo)==(32739,32977),'Photo measured hard-cut interval changed')
    for f,want in ((32738,0),(32739,1),(32976,1),(32977,0)):
        t=float(Fraction(f-lo,1)/fps);active=[e for e in layers if e.get('semantic_layer_id')==c['id']+'-photo' and e['in']<=t<e['out']]
        require(len(active)==want,'Photo hard cut wrong at '+str(f))
        if active:require(active[0]['position']==[1215,272] and active[0]['size']==[536,536] and not any(k.endswith('_keys') for k in active[0]),'Photo hard cut/geometry changed')
    ok('Photo absent32738, present32739..32976, absent32977, no fade/interpolation')
    require(rejected(lambda:adapter.compile_chat(c,lo,hi,'chat-avatar'))=='explicit_ValueError','Adapter omitted missing photo silently');ok('Missing photo source explicitly rejected by adapter')
    p=copy.deepcopy(base_project);p['duration']=float(Fraction(hi-lo,1)/fps);p['reference_range_frames']=[lo,hi];p['clips']=[{'id':'fixture','in':0,'out':p['duration'],'source':'rawA','source_in':0}];p['layers']=layers;p['audio']=[];p['unresolved_properties']=compiled[c['id']]['review'];p['media']['chat-photo']={'path':str(kit/photo['asset']),'type':'image'}
    validate_project(c,p,project_path);render.validate(p,Path(project_path).parent,True);ok('Photo path/hash/dimensions and complete synthetic compile project')
    p['media']['chat-photo']['path']='/intentionally-missing-photo.png'
    require(rejected(lambda:validate_project(c,p,project_path))=='explicit_ValueError','Independent validator accepted absent photo')
    require(rejected(lambda:render.validate(p,Path(project_path).parent,True))=='explicit_ValueError','Renderer accepted absent photo');ok('Missing photo file explicitly rejected by independent and renderer validation')
    mutations={}
    for label,operation in [
        ('missing per-line boxes',lambda s:s.pop('text_line_glyph_bboxes_1080')),
        ('wrong per-line box count',lambda s:s['text_line_glyph_bboxes_1080'].pop()),
        ('reversed line order',lambda s:s['text_line_glyph_bboxes_1080'].reverse()),
    ]:
        q=copy.deepcopy(c);operation(q['messages'][0]['layout_states'][0]);operation(q['layout_states'][0]['messages'][0]);mutations[label]=q
    q=copy.deepcopy(c);q['layout_states'][0]['messages'][0]['text_line_glyph_bboxes_1080'][1][0]+=1;mutations['conflicting parent per-line geometry']=q
    q=copy.deepcopy(c);q['messages'][0]['lines'][0]='contradictory line text';mutations['canonical lines disagree with text']=q
    q=copy.deepcopy(c);q['adjacent_photo_asset']['out_frame']+=1;mutations['photo interval outside parent']=q
    q=copy.deepcopy(c);q['adjacent_photo_asset']['bbox_1080'][2]=q['adjacent_photo_asset']['bbox_1080'][0];mutations['zero-width photo geometry']=q
    for label,q in mutations.items():
        result=rejected(lambda:validate_source(q));require(result=='explicit_ValueError','Independent negative fixture did not fail explicitly '+label);ok('Independent validator rejects '+label)
        findings.append({'mutation':label,'adapter_result':rejected(lambda:adapter.compile_chat(q,lo,hi,'chat-avatar',photo_source='chat-photo')),'independent_validator_result':result})
    return {'checks':tests,'count':len(tests),'compiled_layer_counts':{k:len(v['layers']) for k,v in compiled.items()},'multiline_message_ids':multiline,'multiline_placement_count':5,'all_independent_checks_pass':True,'adapter_negative_fixture_results':findings,'adapter_remaining_accepted_invalid_cases':[x['mutation'] for x in findings if x['adapter_result']=='accepted']}


def load_composite(path,ident):
    d=json.loads(Path(path).read_text(encoding='utf-8'));items=d.get('chat_composites',d.get('composites',[d.get('example',d)]))
    return next(c for c in items if c and c['id']==ident)
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--composites',required=True);ap.add_argument('--id',required=True);ap.add_argument('--project',required=True);ap.add_argument('--output',required=True);ap.add_argument('--self-test',action='store_true');ap.add_argument('--suite-composites',help='Also test all six measured chats, multiline and photo fixtures');a=ap.parse_args()
    paths=[Path(a.composites).resolve(),Path(a.project).resolve(),Path(__file__).with_name('chat_layers.py')];before={str(p):sha(p) for p in paths}
    if a.suite_composites:before[str(Path(a.suite_composites).resolve())]=sha(a.suite_composites)
    c=load_composite(a.composites,a.id);p=json.loads(Path(a.project).read_text(encoding='utf-8'));rows=validate_project(c,p,Path(a.project).resolve())
    result={'schema':'atrass-chat-layer-validation-v1','structural_pass':True,'reference_id':c['id'],'project_path':str(Path(a.project).resolve()),'input_hashes':before,'native_reference_range':p['reference_range_frames'],'chat_layers':len(rows),'message_identities':len(c['messages']),'layout_states':len(c['layout_states']),'visual_pixel_match_verified':False,'complete_automation_release':False}
    if a.self_test:result['semantic_tests']=semantic_tests(c,p,Path(a.project).resolve())
    if a.suite_composites:result['extended_tests']=extended_tests(a.suite_composites,p,Path(a.project).resolve())
    require(all(sha(path)==digest for path,digest in before.items()),'Inputs or adapter changed during validation; rerun before finalizing')
    result['inputs_unchanged_during_validation']=True
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix(out.suffix+'.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n', encoding='utf-8');tmp.replace(out)
    print(json.dumps({'structural_pass':True,'chat_layers':len(rows),'tests':result.get('semantic_tests',{}).get('count'),'adapter_remaining_accepted_invalid_cases':result.get('semantic_tests',{}).get('adapter_remaining_accepted_invalid_cases'),'report':str(out)},ensure_ascii=False,indent=2))
if __name__=='__main__':
    try:main()
    except (ValueError,AssertionError) as e:print(str(e),file=sys.stderr);sys.exit(1)
