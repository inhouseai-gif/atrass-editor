#!/usr/bin/env python3
"""Join adjacent reference-bound render projects into a NEW draft timeline.

No input project/media is modified. This is a project join, not encoded-video
concatenation. Source timestamps and entity-local animation keys are unchanged.
Audio uses an explicitly selected NEW global master; previous segment loudness
is not preserved. NLE export and unknown/global animation contracts are excluded.

Manifest schema: atrass-reference-join-v1
  reference_sha256: SHA of the same reference used by every part
  parts: [{project: relative/path.json, sha256: project SHA,
           reference_evidence: {path: relative/evidence.json, sha256: SHA,
                                pointer: /field/containing/reference/SHA},
           render_record: {path: relative/render.json, sha256: SHA} (optional)}]
Paths in the manifest resolve against the manifest directory. Evidence pointers
are explicit RFC6901-style object/list paths; no searching for coincidental SHAs.
The input project itself MUST already contain reference_range_frames.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math, os, re, tempfile
from fractions import Fraction
from pathlib import Path
from render import validate
from audio_policy import validate_audio_policy, policy_value, STRICT, POLICIES

POLICY='new-global-master-candidate'
CLIP={'id','in','out','source','source_in','geometry','status','rate','freeze_source','source_frame_times'}
LAYER={'id','kind','in','out','z','font','font_size','weight','text','tracking','stroke_width','stroke_fill','fill','glyph_width','glyph_height','scale_x','line_gap','padding','box','background','radius','text_offset','align','shadow','size','crop','source','stops','points','width','stroke','character_reveal','opacity','opacity_keys','fade_in','fade_out','scale_keys','rotation','rotation_keys','reveal_keys','x_keys','y_keys','position','anchor','status','blend_mode'}
AUDIO={'id','in','out','source','source_in','gain_db','rate','fade_in','fade_out','status'}
GEOMETRY={'scale','scale_x','scale_y','tx','ty','scale_keys','tx_keys','ty_keys'}
GEOMETRY_META={'coordinate_convention','geometry_status','last_key_output_frame','zoom_factor','reference_motion_source','status','zoom_native_knots','policy'}
# These are evidence/annotations, not renderer inputs. Preserve them under each
# joined element's source_metadata with an explicit original project path base.
META={'semantic_layer_id','source_reference_frames','supports_new_text','text_semantics','text_verification','fill_provenance','geometry_measurement','geometry_override','geometry_provenance','color_status','font_candidate_search','frame_mapping_selection','frame_mapping_status','mapping_evidence','native_output_start_frame','reference_native_range','source_native_indices','correlation_median','mapping_record','opacity_evidence','rotation_already_baked_into_asset','span_style_override','source_layer_id','source_event_id','geometry_warning','font_provenance','motion_provenance','candidate_provenance','render_hints','geometry_limits','measurement','native_interval','z_evidence'}
# Body20 annotations are not Composer inputs. Actual z/geometry/source fields
# remain in the explicit execution allowlists above; retain these only as data.
META.update({'reference_derived_background','project_local_geometry_candidate','progressive_role'})
KINDS={'caption','text','label','image','rect','gradient','polyline'}

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def number(v):return type(v) in (int,float) and math.isfinite(v)
def path_at(root,value):
    if not isinstance(value,str) or not value:raise ValueError('Expected nonempty resource path')
    p=Path(value);return (root/p).resolve() if not p.is_absolute() else p.resolve()
def relative(path,root):return os.path.relpath(path,root)
def sha_value(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('Expected lowercase SHA-256')
    return value

def pointer(document,value):
    if not isinstance(value,str) or not value.startswith('/'):raise ValueError('Explicit JSON pointer required')
    for token in value[1:].split('/'):
        token=token.replace('~1','/').replace('~0','~')
        try:document=document[int(token)] if isinstance(document,list) else document[token]
        except (ValueError,KeyError,IndexError,TypeError):raise ValueError('Unresolvable evidence JSON pointer '+value)
    return document

def bound_record(record,root,hash_file):
    if not isinstance(record,dict) or set(record)-{'path','sha256','pointer'}:raise ValueError('Invalid bound record')
    p=path_at(root,record.get('path'))
    if hash_file(p)!=sha_value(record.get('sha256')):raise ValueError('Bound file SHA mismatch: '+str(p))
    return p,read(p)

def native_frame(t,fps):
    if not number(t):raise ValueError('Nonfinite native timeline time')
    f=t*float(fps);i=round(f)
    if abs(f-i)>1e-6:raise ValueError('Clip/layer boundary is not on the native frame grid')
    return i

def check_keys(obj,allowed):
    for key in allowed:
        if key.endswith('_keys') and key in obj:
            rows=obj[key]
            if not isinstance(rows,list) or not rows:raise ValueError('Empty/invalid local key array '+key)
            last=-math.inf
            for row in rows:
                if not isinstance(row,list) or len(row) not in (2,3) or not all(number(x) for x in row[:2]) or row[0]<=last or (len(row)==3 and row[2] not in ('linear','hold','smoothstep')):raise ValueError('Invalid entity-local key '+key)
                last=row[0]

def element(e,allowed,prefix,offset_frames,fps,duration,origin,kind):
    if not isinstance(e,dict) or set(e)-allowed-META:raise ValueError('Unsupported '+kind+' fields: '+str(sorted(set(e)-allowed-META)))
    if not isinstance(e.get('id'),str) or not e['id']:raise ValueError('Missing '+kind+' ID')
    if not all(number(e.get(x)) for x in ('in','out')) or not 0<=e['in']<e['out']<=duration+1e-9:raise ValueError('Invalid '+kind+' interval')
    if 'render_hints' in e:raise ValueError('Uncompiled render_hints are not supported by join')
    out={key:copy.deepcopy(value) for key,value in e.items()if key in allowed}
    if kind=='audio':
        offset=float(Fraction(offset_frames,1)/fps)
        out['in']=e['in']+offset;out['out']=e['out']+offset
    else:
        for key in ('in','out'):out[key]=float(Fraction(native_frame(e[key],fps)+offset_frames,1)/fps)
    out['id']=prefix+e['id']
    for key in ('source','font'):
        if key in out:out[key]=prefix+out[key]
    out['join_origin']={**origin,'original_id':e['id']}
    metadata={key:copy.deepcopy(value) for key,value in e.items()if key not in allowed}
    if metadata:out['source_metadata']=metadata
    # A semantic ID is not a Composer cache key. Retain its original identity.
    for key in ('semantic_layer_id','source_reference_frames','native_interval','supports_new_text','text_semantics','text_verification','source_native_indices','reference_native_range','native_output_start_frame','frame_mapping_selection','frame_mapping_status'):
        if key in e:out[key]=copy.deepcopy(e[key])
    check_keys(out,allowed)
    if kind=='clip':
        geo=out.get('geometry',{})
        if not isinstance(geo,dict) or set(geo)-GEOMETRY-GEOMETRY_META:raise ValueError('Unsupported clip geometry/global motion')
        gm={k:v for k,v in geo.items()if k in GEOMETRY_META}
        if gm:out.setdefault('source_metadata',{})['geometry_annotations']=copy.deepcopy(gm)
        geo={k:v for k,v in geo.items()if k in GEOMETRY};out['geometry']=geo
        check_keys(geo,GEOMETRY)
        for key,v in geo.items():
            if not key.endswith('_keys') and not number(v):raise ValueError('Invalid clip geometry '+key)
    if kind=='layer':
        if out.get('kind') not in KINDS:raise ValueError('Unsupported nested layer kind')
        if out.get('anchor','top_left') not in ('top_left','top_center','center'):raise ValueError('Unsupported layer anchor')
        if out.get('shadow') is not None and (not isinstance(out['shadow'],dict) or set(out['shadow'])-{'blur','color','opacity','dx','dy'}):raise ValueError('Unsupported shadow fields')
    return out

def join(manifest,output,*,audio_policy,sample_peak_ceiling_dbfs,sample_underflow_policy=None):
    manifest=Path(manifest).resolve(strict=True);output=Path(output).resolve()
    if output.exists():raise ValueError('Output exists; choose a new path')
    if audio_policy!=POLICY:raise ValueError('Explicit audio policy '+POLICY+' is required')
    if sample_underflow_policy is not None:policy_value(sample_underflow_policy)
    if not number(sample_peak_ceiling_dbfs) or not -30<=sample_peak_ceiling_dbfs<=0:raise ValueError('Explicit finite global sample-peak ceiling required (-30..0 dBFS)')
    manifest_bytes=manifest.read_bytes();manifest_sha=hashlib.sha256(manifest_bytes).hexdigest();data=json.loads(manifest_bytes.decode('utf-8'))
    if data.get('schema')!='atrass-reference-join-v1' or set(data)-{'schema','reference_sha256','parts'}:raise ValueError('Unsupported join manifest')
    reference=sha_value(data.get('reference_sha256'));parts=data.get('parts')
    if not isinstance(parts,list) or len(parts)<2:raise ValueError('At least two adjacent parts required')
    cache={};signatures={}
    def signature(p):
        st=p.stat();return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
    def hash_file(p):
        if not p.is_file():raise ValueError('Missing resource: '+str(p))
        if p not in cache:
            before=signature(p);cache[p]=digest(p)
            if signature(p)!=before:raise ValueError('Resource changed while hashing: '+str(p))
            signatures[p]=before
        return cache[p]
    joined={'schema':'atrass-render-v1','title':'Joined reference reconstruction — draft','release_ready':False,'media':{},'fonts':{},'clips':[],'layers':[],'audio':[],'audio_master':{'sample_peak_ceiling_dbfs':sample_peak_ceiling_dbfs},'limitations':['Joined source-footage/generated-asset draft, not full original-artwork/font reconstruction.','Explicit new global audio master; prior fragment loudness/encoded samples are not preserved.','48kHz global placement can differ by one sample from separately quantized fragments.','Global float timeline subtraction can change pixel rounding at animation ties; compare actual rendered boundary/effect frames before approval.','No listening, LUFS/true-peak or Adobe/NLE approval is implied by joining.']}
    if sample_underflow_policy is not None:
        joined['audio_master']['sample_underflow_policy']=sample_underflow_policy
    sources=[];base=None;last=None;seen_paths=set();initial=None
    for index,part in enumerate(parts):
        if not isinstance(part,dict) or set(part)-{'project','sha256','reference_evidence','render_record'}:raise ValueError('Unsupported join part fields')
        pp=path_at(manifest.parent,part.get('project'))
        if pp in seen_paths:raise ValueError('Repeated source project')
        seen_paths.add(pp);expected=sha_value(part.get('sha256'))
        if hash_file(pp)!=expected:raise ValueError('Project SHA mismatch: '+str(pp))
        p=read(pp)
        if p.get('schema')!='atrass-render-v1':raise ValueError('Only compiled atrass-render-v1 projects supported')
        input_underflow_policy=validate_audio_policy(p)
        if input_underflow_policy!=STRICT and sample_underflow_policy is None:
            raise ValueError('Explicit --sample-underflow-policy is required when joining a bounded input; its audio policy must not be silently dropped')
        validate(p,pp.parent,True)
        ref=p.get('reference_range_frames')
        if not isinstance(ref,list) or len(ref)!=2 or any(type(x)is not int for x in ref) or not 0<=ref[0]<ref[1]:raise ValueError('Input project must already have a valid reference_range_frames')
        ep,ed=bound_record(part.get('reference_evidence'),manifest.parent,hash_file)
        if pointer(ed,part['reference_evidence'].get('pointer'))!=reference:raise ValueError('Reference identity mismatch')
        fps=Fraction(p['fps']);count=ref[1]-ref[0]
        if abs(p['duration']-float(Fraction(count,1)/fps))>1e-9:raise ValueError('Reference range/duration mismatch')
        config=(fps,p['width'],p['height'],p.get('variant'))
        if initial is None:
            initial=config;base=ref[0];joined.update(width=p['width'],height=p['height'],fps=str(fps),variant=p.get('variant'))
        elif config!=initial:raise ValueError('Mismatched fps/canvas/variant')
        if last is not None and ref[0]!=last:raise ValueError('Reference ranges must be exactly adjacent and ordered')
        last=ref[1];offset_frames=ref[0]-base;prefix='p%03d__'%index
        origin={'part_index':index,'project_path':relative(pp,output.parent),'project_sha256':expected,'original_project_directory':relative(pp.parent,output.parent),'offset_frames':offset_frames}
        resource_records=[]
        for group in ('media','fonts'):
            for key,value in p.get(group,{}).items():
                if not isinstance(key,str) or not key:raise ValueError('Invalid resource ID')
                item=copy.deepcopy(value);rp=path_at(pp.parent,item['path']);actual=hash_file(rp)
                if item.get('sha256') is not None and item['sha256']!=actual:raise ValueError('Resource SHA mismatch '+key)
                item['path']=relative(rp,output.parent);item['sha256']=actual
                # Media provenance path follows its source project, not manifest/kit.
                if item.get('provenance') is not None:
                    prov=path_at(pp.parent,item['provenance']);ps=hash_file(prov)
                    if item.get('provenance_sha256') is not None and item['provenance_sha256']!=ps:raise ValueError('Media provenance SHA mismatch '+key)
                    item['provenance']=relative(prov,output.parent);item['provenance_sha256']=ps
                if group=='fonts' and item.get('license') is not None:
                    license_path=path_at(pp.parent,item['license']);license_sha=hash_file(license_path)
                    if item.get('license_sha256') is not None and item['license_sha256']!=license_sha:raise ValueError('Font license SHA mismatch '+key)
                    item['license']=relative(license_path,output.parent);item['license_sha256']=license_sha
                joined[group][prefix+key]=item;resource_records.append({'kind':group,'id':key,'path':relative(rp,output.parent),'sha256':actual})
        for group,allowed,kind in [('clips',CLIP,'clip'),('layers',LAYER,'layer'),('audio',AUDIO,'audio')]:
            elements=p.get(group,[]);ids=[e.get('id')for e in elements]
            if len(ids)!=len(set(ids)):raise ValueError('Duplicate '+group+' ID inside source project')
            joined[group].extend(element(e,allowed,prefix,offset_frames,fps,p['duration'],origin,kind)for e in elements)
        rr=None
        if part.get('render_record') is not None:
            rp,record=bound_record(part['render_record'],manifest.parent,hash_file)
            if record.get('project_sha256')!=expected:raise ValueError('Historical render record belongs to a different project version')
            rr={'path':relative(rp,output.parent),'sha256':hash_file(rp),'audio':record.get('audio'),'output_sha256':record.get('output_sha256'),'record_only_not_applied_to_gain':True}
        sources.append({**origin,'reference_range_frames':ref,'reference_evidence':{'path':relative(ep,output.parent),'sha256':hash_file(ep),'pointer':part['reference_evidence']['pointer']},'audio_master_original':p.get('audio_master'),'historical_render_record':rr,'resources':resource_records,'original_project_metadata':{k:copy.deepcopy(v)for k,v in p.items()if k not in ('media','fonts','clips','layers','audio')},'metadata_path_base':relative(pp.parent,output.parent),'original_resources':{'media':copy.deepcopy(p.get('media',{})),'fonts':copy.deepcopy(p.get('fonts',{}))}})
    joined.update(duration=float(Fraction(last-base,1)/initial[0]),reference_range_frames=[base,last],reference_source_sha256=reference)
    joined['reference_range_seconds']=[float(Fraction(f,1)/initial[0])for f in (base,last)]
    joined['join_provenance']={'schema':'atrass-reference-join-result-v1','manifest_path':relative(manifest,output.parent),'manifest_sha256':manifest_sha,'audio_policy':audio_policy,'source_projects':sources,'motion_time_origin':'entity_local_seconds_preserved','source_times_preserved':True,'global_audio_master_is_new_candidate':True,'historical_annotations_rebased':False,'historical_annotations_have_explicit_original_bases':True,'render_executed':False,'nle_export_validated':False,'joiner_sha256':digest(Path(__file__)), 'renderer_sha256':digest(Path(__file__).with_name('render.py')),'reference_identity_method':'hash_bound_explicit_evidence_pointer','actual_reference_media_rehashed':False}
    if sample_underflow_policy is not None:
        joined['join_provenance']['sample_underflow_policy_selection']={'selected':sample_underflow_policy,'explicit':True,'applies_to_entire_joined_output':True,'input_modes':[source['audio_master_original'].get('sample_underflow_policy',STRICT) if source['audio_master_original'] else STRICT for source in sources],'prior_piece_pcm_preserved':False}
    validate_audio_policy(joined)
    validate(joined,output.parent,True)
    # A final source digest prevents exporting a plan from a concurrently edited input.
    for pp in seen_paths:
        if digest(pp)!=cache[pp]:raise ValueError('Input project changed during join')
    for rp,sig in signatures.items():
        if signature(rp)!=sig:raise ValueError('Bound input/resource changed during join: '+str(rp))
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.join-',suffix='.json',dir=output.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8')as f:json.dump(joined,f,ensure_ascii=False,indent=2);f.write('\n')
        if digest(manifest)!=manifest_sha:raise ValueError('Input manifest changed during join')
        os.link(tmp,output)
    finally:Path(tmp).unlink(missing_ok=True)
    return {'output':str(output),'sha256':digest(output),'parts':len(parts),'native_frames':last-base,'range_frames':[base,last],'audio_policy':audio_policy,'release_ready':False}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('manifest');ap.add_argument('--output',required=True);ap.add_argument('--audio-policy',required=True,choices=[POLICY]);ap.add_argument('--sample-peak-ceiling-dbfs',required=True,type=float);ap.add_argument('--sample-underflow-policy',choices=POLICIES,help='Explicit new global policy; required if any input uses bounded reconstruction. Omitted keeps legacy strict behavior for strict inputs.');a=ap.parse_args()
    try:print(json.dumps(join(a.manifest,a.output,audio_policy=a.audio_policy,sample_peak_ceiling_dbfs=a.sample_peak_ceiling_dbfs,sample_underflow_policy=a.sample_underflow_policy),ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,TypeError)as e:ap.exit(1,str(e)+'\n')
if __name__=='__main__':main()
