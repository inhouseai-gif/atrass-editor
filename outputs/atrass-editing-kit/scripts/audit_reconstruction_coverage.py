#!/usr/bin/env python3
"""Read-only evidence audit, not an overall completion score or release approval.

python scripts/audit_reconstruction_coverage.py --kit-dir . --output references/reconstruction-coverage.json
Add --render-record PATH for each new execution record with reference_range_frames,
rendered_frame_count, project_path and project_sha256. A matching project hash is
reported separately from successful historical execution and style approval.
The sibling outputs/reconstruction/*.render.json engine sidecars are also checked.
python scripts/audit_reconstruction_coverage.py --self-test
Only the requested output file is written. No font, media, project or input is edited.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import sys


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def merge_ranges(ranges, lo=None, hi=None):
    cleaned=[]
    for a,b in ranges:
        if lo is not None:a=max(a,lo)
        if hi is not None:b=min(b,hi)
        if b>a:cleaned.append((a,b))
    out=[]
    for a,b in sorted(cleaned):
        if out and a<=out[-1][1]+1e-9:out[-1][1]=max(out[-1][1],b)
        else:out.append([a,b])
    return out


def complement(ranges,lo,hi):
    out=[];cursor=lo
    for a,b in merge_ranges(ranges,lo,hi):
        if a>cursor:out.append([cursor,a])
        cursor=max(cursor,b)
    if cursor<hi:out.append([cursor,hi])
    return out


def span(ranges):
    return sum(b-a for a,b in merge_ranges(ranges))


def index_runs(indices):
    return merge_ranges((i,i+1) for i in sorted(set(indices)))


def populated(value):
    return value is not None and value not in ('','unknown','unconfirmed','not_identified')


def text_reviewed(event):
    return event.get('review_required') is False and event.get('text_verification','').startswith('visually_verified')


def timing_reviewed(event):
    status=event.get('timing_status','')
    if status=='native_visual_opacity_envelope_measured':return event.get('review_required') is False
    return event.get('review_required') is False and 'native' in status and ('verified' in status or 'directly_viewed' in status) and 'unverified' not in status


def exact_font(event):
    h=event.get('render_hints',{})
    return any(populated(v) for v in [h.get('exact_font'),event.get('font_family_exact'),event.get('source_font')])


def field_state(value):
    if isinstance(value,dict):
        if value.get('observed') is False:return 'observed_absent'
        if value.get('observed') is True:return 'observed_present_parameters_unmeasured'
    return 'unmeasured_or_not_explicitly_assessed'


def render_base_spans(project, bounds):
    """Partition one verified execution by its declared base clips.

    This is not source-content authentication or a visual/audio approval. Missing
    coverage, unsupported roles or overlapping clips leave the whole run unknown.
    A mixed run may contain known raw and artwork spans, without counting artwork
    as raw footage or dropping its intervening raw frames from the evidence.
    """
    unknown=[{'kind':'unclassified','reference_range_frames':bounds}]
    try:
        origin=project['reference_range_frames'][0]
        lo,hi=bounds[0]-origin,bounds[1]-origin
        fps=float(Fraction(str(project['fps'])))
        if lo<0 or hi<=lo or bounds[1]>project['reference_range_frames'][1] or not math.isfinite(fps) or fps<=0:return unknown
        intervals=[];rows=[]
        for clip in project.get('clips',[]):
            start,end=float(clip['in']),float(clip['out'])
            if not all(math.isfinite(v) for v in (start,end)) or end<=start:return unknown
            a=max(lo,math.ceil(start*fps-1e-9));b=min(hi,math.ceil(end*fps-1e-9))
            if b<=a:continue
            media=project.get('media',{}).get(clip.get('source'),{})
            if media.get('role')=='source_footage' and media.get('type','video')=='video':kind='source_footage'
            elif media.get('role')=='archival_source_footage' and media.get('type','video')=='video':kind='archival_source_footage'
            elif media.get('role')=='procedurally_created_graphic_asset' and media.get('type')=='image':kind='procedural_graphics'
            elif media.get('role') in ('generated_replacement_illustration','generated_replacement_artwork') and media.get('type')=='image':kind='generated_replacement_artwork'
            else:kind='unclassified'
            intervals.append([a,b]);rows.append({'kind':kind,'reference_range_frames':[a+origin,b+origin]})
        if span(intervals)!=hi-lo or sum(b-a for a,b in intervals)!=hi-lo:return unknown
        if any(r['kind']=='unclassified' for r in rows):return unknown
        combined=[]
        for row in sorted(rows,key=lambda r:r['reference_range_frames']):
            if combined and combined[-1]['kind']==row['kind'] and combined[-1]['reference_range_frames'][1]==row['reference_range_frames'][0]:
                combined[-1]['reference_range_frames'][1]=row['reference_range_frames'][1]
            else:combined.append(row)
        return combined
    except (KeyError,IndexError,TypeError,ValueError,ZeroDivisionError,OverflowError):
        return unknown


def render_base_class(project,bounds):
    classes={row['kind'] for row in render_base_spans(project,bounds)}
    return next(iter(classes)) if len(classes)==1 else 'mixed_base_media'


def render_fps_matches_project(record, project):
    """Different output rates need an explicit native-frame mapping, not a count."""
    try:
        output_fps=float(Fraction(str(record['fps'])))
        project_fps=float(Fraction(str(project['fps'])))
        return output_fps>0 and project_fps>0 and math.isclose(output_fps,project_fps,rel_tol=1e-12,abs_tol=1e-10)
    except (KeyError,TypeError,ValueError,ZeroDivisionError,OverflowError):
        return False


class Inputs:
    def __init__(self,root):self.root=root;self.records={};self.errors=[]
    def read(self,name,required=True):
        p=self.root/name
        if not p.is_file():
            if required:self.errors.append({'type':'missing_input','path':name})
            return {}
        data=p.read_bytes();digest=hashlib.sha256(data).hexdigest()
        self.records[name]={'path':name,'sha256':digest,'bytes':len(data)}
        try:return json.loads(data)
        except (UnicodeDecodeError,json.JSONDecodeError) as exc:
            self.errors.append({'type':'invalid_json','path':name,'detail':str(exc)});return {}
    def stable(self):
        return [name for name,row in self.records.items() if not (self.root/name).is_file() or sha(self.root/name)!=row['sha256']]


# Explicit per-template color checklist; a missing numeric observation stays missing.
# A template-family sample does not imply every event/pixel has been measured.
COLOR_ROLES={
 'dialogue':['basic-bar','basic-caption-cream'],
 'bold':['bold-conclusion-white'],
 'question':['handwritten-white'],
 'heading':['broll-bold-heading-cream'],
 'two-line':['upper-line-white','lower-line-cream'],
 'definition':['definition-title-white','definition-serif-cream','definition-photo-pixels'],
 'chapter':['chapter-title-white-box','chapter-title-black','chapter-upper-beige','chapter-rose-band','chapter-middle-blue','chapter-lower-teal','chapter-small-heading-white','chapter-grain-color'],
 'situation':['situation-label-white','situation-label-black'],
 'thought':['thought-white','thought-black'],
 'chat':['chat-panel-bluegray','chat-bubble-yellow','chat-incoming-white','chat-black-text','chat-avatar-circle','chat-avatar-silhouette'],
 'formula':['formula-text-white'],
 'number':['number-left-white','number-right-cream','number-warm-panel','number-cool-panel'],
 'carrot':['carrot-orange','carrot-green','carrot-shade-orange','carrot-outline-white'],
 'whip':['whip-light-brown','whip-dark-brown','whip-gold','whip-outline-white'],
 'cross':['red-cross'],
 'quotes':['distributed-quote-white'],
 'photo-pip':['photo-pip-pixels'],
 'dual-pip':['chat-panel-bluegray','chat-bubble-yellow','chat-incoming-white','chat-black-text','product-photo-pixels'],
 'emoji':['emoji-yellow','emoji-dark-features','emoji-keyword-cream','emoji-small-text-white'],
 'split':['split-left-picture-pixels','split-right-picture-pixels'],
 'quote-photo':['quote-photo-pixels','quote-darkening-black','quote-serif-cream','quote-credit-white'],
 'outtake':[]}
TEXT_TEMPLATES={'dialogue','bold','question','heading','two-line','definition','chapter','situation','thought','chat','formula','number','quotes','dual-pip','emoji','quote-photo'}


def template_audit(analysis,palette,chats,kit):
    measurements={m.get('role'):m for m in palette.get('measurements',[])}
    if chats:
        avatar=chats[0].get('avatar',{}).get('layout_states',[{}])[0]
        for role,key in [('chat-avatar-circle','circle_fill_observed_decoded_rgb'),('chat-avatar-silhouette','silhouette_fill_observed_decoded_rgb')]:
            if avatar.get(key):measurements[role]={'rgb_mode':avatar[key],'method':'native decoded chat component measurement','source':'references/A-layer-events.json#/chat_composites/0/avatar/layout_states/0/'+key}
    result=[]
    for t in analysis.get('templates',[]):
        ident=t['id'];required=COLOR_ROLES.get(ident,['template-color-contract-not-defined']);roles=[]
        for role in required:
            m=measurements.get(role)
            roles.append({'role':role,'state':'representative_decoded_rgb_measured' if m else 'numeric_measurement_not_linked','rgb':m.get('rgb_mode') if m else None,'authored_rgba':None,'source':m.get('source','evidence/A-palette-measurements.json#'+role) if m else None,'note':'Representative role only; does not certify all per-event colors or authored RGBA.' if m else 'Appearance may be described in A-analysis; a numeric role-specific measurement was not found in this audit input.'})
        stroke={'state':'unmeasured_or_not_explicitly_assessed','width_px':None,'color':None}
        shadow={'state':'unmeasured_or_not_explicitly_assessed','blur_px':None,'opacity':None,'offset':None}
        alpha={'state':'authored_alpha_unmeasured','value':None}
        if ident=='chat' and chats:
            stroke={'state':'observed_absent_on_measured_chat_panels','evidence':'references/A-layer-events.json#/chat_composites/*/panel/stroke','bubble_and_text_stroke':'not_explicitly_assessed'}
            shadow={'state':'panel_edge_shadow_observed_parameters_unmeasured','blur_px':None,'opacity':None,'offset':None,'evidence':'references/A-layer-events.json#/chat_composites/*/panel/shadow'}
        if ident in ('carrot','whip'):stroke.update(state='white_outline_observed_width_alpha_unmeasured',color_role='outline-white')
        if ident=='cross':stroke.update(state='two_red_strokes_observed_exact_width_curve_unmeasured',color_role='red-cross')
        if ident=='question' and (kit/'assets/recovered/A-question-observed-phrase.provenance.json').is_file():alpha={'state':'one_observed_phrase_estimated_alpha_available_not_original_rgba','evidence':'assets/recovered/A-question-observed-phrase.provenance.json','supports_unseen_text':False}
        if ident=='outtake':stroke=shadow=alpha={'state':'not_applicable_no_graphic_layer_in_this_template'}
        result.append({'id':ident,'name':t.get('name'),'evidence':t.get('evidence'),'required_color_roles':roles,'numeric_color_roles_found':sum(r['rgb'] is not None for r in roles),'required_color_role_count':len(roles),'font_identity':{'state':'unconfirmed' if ident in TEXT_TEMPLATES else 'not_applicable_asset_or_live_picture','exact_font':t.get('source_font')},'alpha':alpha,'stroke':stroke,'shadow':shadow,'full_event_parameter_coverage':False if ident!='outtake' else None})
    return result


def audit(kit,render_records=(),snapshot_paths=None):
    kit=kit.resolve();inp=Inputs(kit)
    captions=inp.read('references/A-caption-timeline.json');layers=inp.read('references/A-layer-events.json');mapping=inp.read('references/video-source-map-render.json');assets=inp.read('references/assets-needed.json');audio=inp.read('references/audio-deep-analysis.json');amap=inp.read('references/audio-source-map.json');ae=inp.read('references/audio-event-candidates.json');profile=inp.read('references/audio-processing-profile.json');analysis=inp.read('references/A-analysis.json');palette=inp.read('evidence/A-palette-measurements.json')
    issues=[];events=captions.get('events',[]);all_graphics=layers.get('events',[]);graphics=[e for e in all_graphics if e.get('render_enabled') is True];chats=layers.get('chat_composites',[])
    n=mapping.get('reference_native_frame_count',mapping.get('frame_mapping_coverage',{}).get('output_native_frames_total',0));ranges=mapping.get('coverage',{}).get('output_ranges',[]);duration=max((b for a,b in ranges),default=0);clips=mapping.get('clips',[])
    indices=[];invalid=[];array_mismatches=[]
    for c in clips:
        out=c.get('output_native_frame_indices',[]);src=c.get('render_source_frame_indices',c.get('source_frame_indices',[]));indices+=out
        if len(src)!=len(out):array_mismatches.append(c['id'])
        invalid.extend({'clip':c['id'],'frame':i} for i in out if not isinstance(i,int) or not 0<=i<n)
    valid=[i for i in indices if isinstance(i,int) and 0<=i<n];unique=set(valid);missing_frames=complement(index_runs(unique),0,n);seconds_ranges=merge_ranges((c['output_in_seconds'],c['output_out_seconds']) for c in clips);gaps=complement(seconds_ranges,0,duration)
    duplicates=[i for i,c in Counter(valid).items() if c>1];declared=mapping.get('unmapped_intervals',[]);declared_ranges=merge_ranges((g['output_in_seconds'],g['output_out_seconds']) for g in declared)
    if array_mismatches or invalid or duplicates:issues.append({'gate':'video_map_integrity','clip_array_mismatches':array_mismatches,'out_of_bounds':invalid,'duplicate_output_frames':duplicates})
    gap_diff=abs(span(gaps)-span(declared_ranges))
    if gap_diff>1e-5:issues.append({'gate':'video_gap_consistency','difference_seconds':gap_diff})
    video={'total_native_output_frames':n,'mapped_native_candidate_frames':len(unique),'unmapped_native_frames':n-len(unique),'unmapped_frame_ranges_half_open':missing_frames,'clip_candidates':len(clips),'clip_status_counts':dict(Counter(c.get('status','missing') for c in clips)),'timeline_seconds':duration,'mapped_candidate_seconds':span(seconds_ranges),'unmapped_seconds':span(gaps),'unmapped_seconds_ranges':gaps,'declared_gap_count':len(declared),'computed_gap_count':len(gaps),'declared_gap_duration_difference_seconds':gap_diff,'mapping_is_render_execution':False,'original_project_certified':False,'human_full_native_viewing':mapping.get('frame_mapping_coverage',{}).get('human_full_viewing',False),'integrity':{'duplicate_frame_count':len(duplicates),'out_of_bounds_count':len(invalid),'clip_array_length_mismatches':array_mismatches}}
    def text_gate(rows):
        return {'total':len(rows),'text_visually_reviewed':sum(text_reviewed(e) for e in rows),'native_timing_reviewed':sum(timing_reviewed(e) for e in rows),'native_hard_boundary_reviewed':sum(timing_reviewed(e) and e.get('timing_status')!='native_visual_opacity_envelope_measured' for e in rows),'native_opacity_envelope_measured':sum(e.get('timing_status')=='native_visual_opacity_envelope_measured' for e in rows),'timing_status_counts':dict(Counter(e.get('timing_status','missing') for e in rows)),'render_enabled':sum(e.get('render_enabled') is True for e in rows),'font_identity_confirmed':sum(exact_font(e) for e in rows),'text_unreviewed_ids':[e['id'] for e in rows if not text_reviewed(e)],'timing_unreviewed_ids':[e['id'] for e in rows if not timing_reviewed(e)],'font_identity_unconfirmed_ids':[e['id'] for e in rows if not exact_font(e)]}
    messages=[m for c in chats for m in c.get('messages',[])];senders=[c['sender'] for c in chats if c.get('sender')]
    text={'dialogue':text_gate(events),'independent_graphic_text':text_gate(graphics),'archived_or_superseded_ocr_rows':{'count':len(all_graphics)-len(graphics),'ids':[e['id'] for e in all_graphics if e.get('render_enabled') is not True],'note':'These are not additional live graphic text layers; superseded/rejected statuses must be retained.'},'chat':{'composites':len(chats),'render_ready_structure':sum(c.get('render_ready_structure') is True for c in chats),'render_likeness_verified':sum(c.get('render_likeness_verified') is True for c in chats),'messages':len(messages),'message_text_verified':sum(m.get('text_verified') is True for m in messages),'message_timing_verified':sum(m.get('timing_verified') is True for m in messages),'message_exact_font_confirmed':sum(populated(m.get('text_style',{}).get('font_family_exact')) for m in messages),'sender_labels':len(senders),'sender_exact_font_confirmed':sum(populated(s.get('font_family_exact')) for s in senders),'font_identity_does_not_follow_from_reviewed_text':True}}
    inventory=assets.get('assets',[]);asset_rows=[]
    for a in inventory:
        row={k:a.get(k) for k in ['id','category','source_asset_status','clean_asset_available','exact_source_identity_verified','gap_ids','recommended_action']};row['scope']='listed_visual_gap_assets_only';asset_rows.append(row)
    derived=[]
    for c in chats:
        a=c.get('avatar',{});path=a.get('asset');prov=a.get('asset_provenance',{})
        if path:derived.append({'usage':a.get('id'),'path':path,'exists':(kit/path).is_file(),'original_asset_available':prov.get('original_asset_available',False),'original_alpha_available':prov.get('alpha_is_original',False),'status':'reference_derived_raster'})
    recovered=inp.read('assets/recovered/A-question-observed-phrase.provenance.json',required=False)
    if recovered:derived.append({'usage':'A-handwritten-0001 observed phrase only','path':'assets/recovered/A-question-observed-phrase.png','exists':(kit/'assets/recovered/A-question-observed-phrase.png').is_file(),'original_asset_available':False,'original_alpha_available':False,'status':'reference_derived_estimated_alpha','supports_new_text':False})
    asset_gate={'inventory_scope':assets.get('scope'),'inventory_is_complete_all_overlays':False,'listed_assets':len(inventory),'clean_originals_available':sum(a.get('clean_asset_available') is True for a in inventory),'exact_source_identities_verified':sum(a.get('exact_source_identity_verified') is True for a in inventory),'unavailable_asset_ids':[a['id'] for a in inventory if a.get('clean_asset_available') is not True],'assets':asset_rows,'supplemental_overlay_needs':assets.get('additional_overlay_assets_needed_in_these_gaps',[]),'reference_derived_assets':derived,'derived_assets_are_not_clean_originals':True}
    asset_gap_ranges=merge_ranges((g['in_seconds'],g['out_seconds']) for g in assets.get('gaps',[]))
    asset_gate['inventory_gap_ranges_match_current_map']=(len(asset_gap_ranges)==len(gaps) and all(abs(a-c)<1e-5 and abs(b-d)<1e-5 for (a,b),(c,d) in zip(asset_gap_ranges,gaps)))
    if not asset_gate['inventory_gap_ranges_match_current_map']:issues.append({'gate':'asset_inventory_gap_consistency','next_action':'Refresh asset inventory against the current map gaps; the old source-map hash may refer to a research version.'})
    # Planned projects and rendered comparison evidence are different gates.
    projects=[]
    # API and CLI both interpret relative selection paths from the kit root.
    selected=None if snapshot_paths is None else {(kit/Path(p)).resolve() for p in snapshot_paths}
    for path in sorted((kit/'projects').glob('*.json')):
        if selected is not None and path.resolve() not in selected:continue
        rel=str(path.relative_to(kit));p=inp.read(rel,required=False)
        if p.get('variant')=='A' and p.get('schema')=='atrass-render-v1':projects.append({'path':rel,'sha256':inp.records[rel]['sha256'],'reference_range_frames':p.get('reference_range_frames'),'duration':p.get('duration'),'layer_ids':[e.get('id') for e in p.get('layers',[])],'release_ready':p.get('release_ready',False),'execution_inferred':False})
    comparisons=[];compared_frames=set()
    paths=[p for p in (kit/'evidence').rglob('comparison.json') if str(p.relative_to(kit)).startswith(('evidence/reconstruction/question-','evidence/reconstruction-40s/','evidence/reconstruction-chat'))];opening=kit/'evidence/reconstruction/opening-comparison.json'
    if opening.is_file():paths.append(opening)
    for path in sorted(set(paths)):
        rel=str(path.relative_to(kit));c=inp.read(rel,required=False)
        rows=c.get('frames',[]);sample_frames=[r['reference_frame'] for r in rows if isinstance(r.get('reference_frame'),int)];missing=[]
        for r in rows:
            if r.get('evidence') and not (path.parent/r['evidence']).is_file():missing.append(r['evidence'])
        if path==opening:sample_frames=[r['frame'] for r in c.get('metrics',[]) if isinstance(r.get('frame'),int)]
        compared_frames.update(sample_frames);comparisons.append({'path':rel,'status':c.get('status'),'reported_sample_count':len(sample_frames),'sample_reference_frames':sample_frames,'missing_sample_files':missing,'is_whole_range_execution_proof':False,'source_reference_passthrough':c.get('source_reference_passthrough')})
    records=[];full_ranges=[]
    val=inp.read('references/reconstruction-40s-validation.json',required=False);review=inp.read('references/A-pilot-extension-review.json',required=False)
    if val.get('native_frame_count') and review.get('render_range_frames'):
        a,b=review['render_range_frames'];consistent=b-a==val['native_frame_count']
        if consistent:full_ranges.append([a,b])
        records.append({'source':'references/reconstruction-40s-validation.json','reference_range_source':'references/A-pilot-extension-review.json','reference_range_frames':[a,b],'reported_output_frames':val['native_frame_count'],'count_matches_range':consistent,'historical_render_execution_record':consistent,'base_media_classification':'source_footage','bound_to_current_project_sha256':False,'style_approved':val.get('release_ready',False),'note':'A historical completed-run count is distinct from a current project hash match or full visual approval.'})
    compiler=inp.read('references/plan-compiler-validation.json',required=False);pilot=compiler.get('actual_provided_raw_compile_and_render',{})
    if pilot.get('project')=='projects/A-compiled-opening.json' and isinstance(pilot.get('frames'),int):
        full_ranges.append([0,pilot['frames']]);records.append({'source':'references/plan-compiler-validation.json','reference_range_frames':[0,pilot['frames']],'reported_output_frames':pilot['frames'],'historical_render_execution_record':True,'base_media_classification':'source_footage','bound_to_current_project_sha256':False,'style_approved':False})
    supplied_paths={Path(p).resolve() for p in render_records}
    discovered_paths=set((kit.parent/'reconstruction').glob('*.render.json'))
    if selected is not None:
        if not supplied_paths.issubset(selected):raise ValueError('Explicit render records must belong to the selected snapshot')
        discovered_paths={p for p in discovered_paths if p.resolve() in selected}
    for path in sorted(supplied_paths|discovered_paths):
        name=str(path.relative_to(kit)) if path.is_relative_to(kit) else str(path);r=inp.read(name);bounds=r.get('reference_range_frames');count=r.get('rendered_frame_count',r.get('frame_count'));errors=[]
        project_name=r.get('project_path',r.get('project'));project_path=Path(project_name) if project_name else None
        if project_path and not project_path.is_absolute():project_path=kit/project_path
        if selected is not None and project_path and project_path.resolve() not in selected:raise ValueError('Selected render record refers to a project outside the snapshot: '+str(project_path))
        binding=bool(project_path and project_path.is_file() and r.get('project_sha256') and sha(project_path)==r['project_sha256'])
        project={}
        if project_path and project_path.is_file():
            project_rel=str(project_path.relative_to(kit)) if project_path.is_relative_to(kit) else str(project_path);project=inp.read(project_rel,required=False)
        if path in discovered_paths and project.get('variant')!='A':continue
        engine_sidecar='output_sha256' in r and 'frame_count' in r
        if engine_sidecar and binding and not render_fps_matches_project(r,project):errors.append('output_fps_differs_from_project_or_is_invalid_native_mapping_required')
        if not errors and bounds is None and binding and project.get('reference_range_frames') and isinstance(count,int):
            fps=float(Fraction(str(r.get('fps',project.get('fps','60000/1001')))));start=project['reference_range_frames'][0]+round(r.get('range',[0,0])[0]*fps);bounds=[start,start+count]
            if bounds[1]>project['reference_range_frames'][1]:errors.append('render_range_exceeds_project_reference_range')
        if not isinstance(bounds,list) or len(bounds)!=2 or not all(isinstance(x,int) for x in bounds):errors.append('missing_or_invalid_reference_range_frames')
        elif not (0<=bounds[0]<bounds[1]<=n):errors.append('range_outside_reference')
        elif count!=bounds[1]-bounds[0]:errors.append('rendered_count_mismatch')
        if not engine_sidecar and r.get('status') not in ('completed','passed','rendered','draft_render_and_sample_compare_pass'):errors.append('execution_not_reported_successful')
        output_path=Path(r['output']) if r.get('output') else None
        if output_path and not output_path.is_absolute():output_path=path.parent/output_path
        output_exists=bool(output_path and output_path.is_file());output_match=bool(output_exists and r.get('output_sha256') and sha(output_path)==r['output_sha256'])
        if engine_sidecar and not output_match:errors.append('output_file_or_sha256_not_verified')
        if not errors:full_ranges.append(bounds)
        records.append({'source':name,'reference_range_frames':bounds,'reported_output_frames':count,'historical_render_execution_record':not errors,'base_media_classification':render_base_class(project,bounds) if binding and not errors else 'unclassified','record_errors':errors,'bound_to_current_project_sha256':binding,'output_exists':output_exists,'output_sha256_matches':output_match,'project_sha256':r.get('project_sha256'),'output_sha256':r.get('output_sha256'),'style_approved':r.get('style_approved') is True,'note':'Engine sidecar or explicit supplied run record. Current project and output hashes checked independently; no output video decode is performed in this audit.'})
        if binding and not errors:records[-1]['base_media_spans']=render_base_spans(project,bounds)
        if errors:issues.append({'gate':'supplied_render_record','source':name,'errors':errors})
    union=merge_ranges(full_ranges,0,n)
    def covers(e):return any(a<=e.get('in_frame',-1) and b>=e.get('out_frame',n+1) for a,b in union)
    def sampled(e):return any(e.get('in_frame',n+1)<=f<e.get('out_frame',-1) for f in compared_frames)
    execution={'planned_projects':projects,'planned_projects_do_not_prove_execution':True,'historical_full_range_execution_records':records,'documented_full_range_union_frames':union,'documented_full_range_unique_native_frame_count':span(union),'comparison_records':comparisons,'unique_reference_frames_with_numeric_render_comparison':len(compared_frames),'comparison_reference_frames':sorted(compared_frames),'dialogue_events_fully_inside_documented_execution_ranges':sum(covers(e) for e in events),'graphic_events_fully_inside_documented_execution_ranges':sum(covers(e) for e in graphics),'dialogue_events_with_at_least_one_numeric_comparison_frame':sum(sampled(e) for e in events),'graphic_events_with_at_least_one_numeric_comparison_frame':sum(sampled(e) for e in graphics),'comparison_frame_presence_certifies_font_or_visual_match':False,'all_events_rendered_and_compared':False,'note':'Counts are conservative documented evidence. Completed renders without a linked full-range run record remain unknown here; a project file or semantic test alone is not execution proof. Comparison evidence may be historical and is not automatically bound to the latest project hash.'}
    execution['discovery_scope']={'mode':'all_local_projects_and_sidecars' if selected is None else 'explicit_snapshot_paths','selected_paths':None if selected is None else sorted(os.path.relpath(p,kit) for p in selected),'historical_fixed_validation_inputs_still_included':True,'scope_is_not_style_or_source_identity_approval':True}
    current_ranges=[r['reference_range_frames'] for r in records if r.get('historical_render_execution_record') and r.get('bound_to_current_project_sha256') and r.get('output_sha256_matches')]
    execution['current_project_and_output_hash_bound_ranges']=merge_ranges(current_ranges)
    execution['current_project_and_output_hash_bound_native_frame_count']=span(current_ranges)
    execution['base_media_scope_note']='The total union includes both footage renders and generated graphics. Counts below partition verified mixed runs into their base-clip frame spans; record-level mixed labels are retained. Roles and complete nonoverlapping clip coverage are checked, not original-media authenticity, soundtrack or style. Alternative renders can classify the same reference frame differently, so category counts must not be added as a completion score.'
    execution['execution_by_base_media']={}
    for kind in ('source_footage','archival_source_footage','procedural_graphics','generated_replacement_artwork','mixed_base_media','unclassified'):
        selected=[]
        for record in records:
            if not record.get('historical_render_execution_record'):continue
            partitions=record.get('base_media_spans',[{'kind':record.get('base_media_classification','unclassified'),'reference_range_frames':record['reference_range_frames']}])
            selected.extend((record,row['reference_range_frames']) for row in partitions if row['kind']==kind)
        historical=[bounds for record,bounds in selected]
        bound=[bounds for record,bounds in selected if record.get('bound_to_current_project_sha256') and record.get('output_sha256_matches')]
        execution['execution_by_base_media'][kind]={'documented_reference_ranges':merge_ranges(historical),'documented_unique_native_frame_count':span(historical),'current_project_and_output_hash_bound_ranges':merge_ranges(bound),'current_project_and_output_hash_bound_native_frame_count':span(bound),'sources':sorted({record['source'] for record,bounds in selected})}
    segments=amap.get('segments',[]);audio_ranges=merge_ranges((s['final_in'],s['final_out']) for s in segments);sound_events=ae.get('events',[])
    audio_gate={'alignment_segments':len(segments),'alignment_seconds':span(audio_ranges),'unmapped_seconds_ranges':complement(audio_ranges,0,duration),'alignment_status_counts':dict(Counter(s.get('status','missing') for s in segments)),'median_correlation_below_0_8_ids':[s['id'] for s in segments if s.get('correlation_median',0)<.8],'boundary_approved_segments':sum(s.get('status') in ('native_and_listening_verified','approved') for s in segments),'candidate_effect_events':len(sound_events),'identified_effect_events':sum(populated(e.get('confirmed_sound_identity')) for e in sound_events),'selected_effect_asset_events':sum(e.get('render_asset_selected') is True for e in sound_events),'unresolved_effect_events':sound_events,'direct_listened_seconds':audio.get('manual_listened_seconds',0),'local_asr':audio.get('asr',{}),'asr_is_human_transcript_review':False,'implementation_status':audio.get('implementation_status',{}),'processing_profile_available':bool(profile),'processing_profile':profile,'delivery_preview':audio.get('delivery_preview_validation',{}),'whole_video_soundtrack_reconstruction_certified':False}
    templates=template_audit(analysis,palette,chats,kit)
    for gate,ids,action in [('clean_visual_assets',asset_gate['unavailable_asset_ids'],'Obtain originals or make explicitly approved replacements; validate transforms and lifetimes.'),('dialogue_font_identity',text['dialogue']['font_identity_unconfirmed_ids'],'Font candidates and measured bbox fits do not certify the original font.'),('graphic_font_identity',text['independent_graphic_text']['font_identity_unconfirmed_ids'],'Validate exact face or retain a declared approximation and compare each role.')]:
        if ids:issues.append({'gate':gate,'unresolved_count':len(ids),'ids':ids,'next_action':action})
    if gaps:issues.append({'gate':'unmapped_visual_spans','unresolved_count':len(gaps),'seconds':span(gaps),'next_action':'Link source/recreated B-roll and independent overlays; audio remains separately mapped.'})
    unknown_colors=[{'template':t['id'],'roles':[r['role'] for r in t['required_color_roles'] if r['rgb'] is None]} for t in templates if any(r['rgb'] is None for r in t['required_color_roles'])]
    issues.append({'gate':'template_color_measurement','templates_with_unlinked_numeric_roles':unknown_colors,'next_action':'Measure each listed role at native resolution, retaining distributions and output-vs-authored distinction.'})
    issues.append({'gate':'template_alpha_stroke_shadow','template_states':[{'id':t['id'],'alpha':t['alpha']['state'],'stroke':t['stroke']['state'],'shadow':t['shadow']['state']} for t in templates if t['id']!='outtake'],'next_action':'Record observed absence or measure present effects; missing keys must not silently become opacity1/stroke0/shadow0 as an evidence claim.'})
    issues.append({'gate':'audio_listening_and_assets','unidentified_candidate_count':audio_gate['candidate_effect_events']-audio_gate['identified_effect_events'],'listened_seconds':audio_gate['direct_listened_seconds'],'next_action':'Review sound identity and cut phonemes; obtain licensed stems, then execute and validate the full soundtrack.'})
    issues.append({'gate':'full_render_validation','next_action':'Render all approved source/asset/layer intervals, bind run metadata to exact project/input hashes, compare every transition and selected native frames, and validate audio independently.'})
    identities={captions.get('source_sha256'),layers.get('source_sha256'),mapping.get('reference_identity',{}).get('source_sha256')};identities.discard(None)
    if len(identities)>1:inp.errors.append({'type':'reference_identity_mismatch','values':sorted(identities)})
    changed=inp.stable()
    return {'schema':'atrass-reconstruction-coverage-v1','generated_at':datetime.now(timezone.utc).isoformat(),'video_id':'DvDeXNPS3Wc','status':'separate_evidence_gates_incomplete','overall_completion_percent':None,'why_no_overall_percent':'Text review, original font identity, asset availability, candidate mapping, render execution and visual/audio approval are different obligations. A single percentage hides missing gates.','release_ready':False,'input_integrity':{'files':list(inp.records.values()),'errors':inp.errors,'changed_during_audit':changed,'snapshot_stable':not changed and not inp.errors},'video_mapping':video,'text_and_font_gates':text,'external_visual_assets':asset_gate,'render_execution':execution,'template_parameter_matrix':templates,'audio':audio_gate,'unresolved_gates':issues,'audit_limits':['Read-only mechanical audit of explicit records; no new visual review or listening performed.','Missing fields mean unknown, not confirmed absent or completed.','No entire-video completeness claim from OCR/native machine scans or frame-sample review.','Assets-needed inventories only the mapped gaps, not all overlay assets on talking-head spans.','Decoded output RGB/estimated alpha do not uniquely identify authored RGBA or original software.','Historical render records and comparisons do not certify the latest mutable project configuration.']}


def self_test():
    assert merge_ranges([[3,6],[0,2],[1,4],[6,7]])==[[0,7]]
    assert complement([[2,4],[3,7]],0,10)==[[0,2],[7,10]]
    assert merge_ranges([[-4,1],[9,15]],0,10)==[[0,1],[9,10]]
    assert index_runs([3,0,1,1,4])==[[0,2],[3,5]]
    assert not text_reviewed({'review_required':False,'text_verification':'ocr_only'})
    assert not exact_font({'render_hints':{'exact_font':None}})
    assert not exact_font({'render_hints':{'exact_font':'unknown'}})
    assert field_state({'observed':False})=='observed_absent'
    assert field_state({})=='unmeasured_or_not_explicitly_assessed'
    assert span([[0,4],[2,6]])==6
    assert timing_reviewed({'review_required':False,'timing_status':'native_visual_opacity_envelope_measured'})
    assert not timing_reviewed({'review_required':False,'timing_status':'native_unverified'})
    p={'reference_range_frames':[100,160],'fps':60,'media':{'raw':{'role':'source_footage'},'art':{'type':'image','role':'procedurally_created_graphic_asset'},'ref':{'role':'finished_reference'}},'clips':[{'source':'raw','in':0,'out':.5},{'source':'art','in':.5,'out':1}]}
    assert render_base_class(p,[100,130])=='source_footage'
    assert render_base_class(p,[130,160])=='procedural_graphics'
    assert render_base_class(p,[100,160])=='mixed_base_media'
    assert render_base_class(p,[100,161])=='unclassified'
    assert render_base_class(p,[99,130])=='unclassified'
    assert render_base_class({**p,'clips':[{'source':'ref','in':0,'out':1}]},[100,160])=='unclassified'
    assert render_base_class({**p,'clips':p['clips']+[p['clips'][0]]},[100,160])=='unclassified'
    assert render_base_class({**p,'clips':[{'source':'raw','in':0,'out':.4},{'source':'raw','in':.5,'out':1}]},[100,160])=='unclassified'
    assert render_base_class({},[0,1])=='unclassified'
    generated={**p,'media':{'replacement':{'role':'generated_replacement_illustration','type':'image'}},'clips':[{'source':'replacement','in':0,'out':1}]}
    assert render_base_class(generated,[100,160])=='generated_replacement_artwork'
    assert render_base_class({**generated,'media':{'replacement':{'role':'generated_replacement_illustration','type':'video'}}},[100,160])=='unclassified'
    assert render_fps_matches_project({'fps':30},{'fps':60}) is False
    assert render_fps_matches_project({'fps':59.94005994005994},{'fps':'60000/1001'}) is True
    assert render_fps_matches_project({'fps':'0/1'},{'fps':60}) is False
    assert render_fps_matches_project({'fps':'broken'},{'fps':60}) is False
    assert render_fps_matches_project({}, {'fps':60}) is False
    assert render_base_spans(p,[100,160])==[{'kind':'source_footage','reference_range_frames':[100,130]},{'kind':'procedural_graphics','reference_range_frames':[130,160]}]
    assert render_base_spans(p,[110,150])==[{'kind':'source_footage','reference_range_frames':[110,130]},{'kind':'procedural_graphics','reference_range_frames':[130,150]}]
    assert render_base_spans({**p,'clips':[{'source':'raw','in':0,'out':.5},{'source':'raw','in':.5,'out':1}]},[100,160])==[{'kind':'source_footage','reference_range_frames':[100,160]}]
    assert render_base_spans({**p,'clips':p['clips']+[p['clips'][0]]},[100,160])==[{'kind':'unclassified','reference_range_frames':[100,160]}]
    assert render_base_spans({**p,'clips':[{'source':'raw','in':0,'out':.4},{'source':'raw','in':.5,'out':1}]},[100,160])==[{'kind':'unclassified','reference_range_frames':[100,160]}]
    assert render_base_spans({**p,'clips':[{'source':'raw','in':0,'out':.5},{'source':'ref','in':.5,'out':1}]},[100,160])==[{'kind':'unclassified','reference_range_frames':[100,160]}]
    ntsc={'reference_range_frames':[590,1822],'fps':'60000/1001','media':{'raw':{'role':'source_footage'},'art':{'type':'image','role':'generated_replacement_illustration'}},'clips':[{'source':'art','in':0,'out':392*1001/60000},{'source':'raw','in':392*1001/60000,'out':796*1001/60000},{'source':'art','in':796*1001/60000,'out':1232*1001/60000}]}
    assert render_base_spans(ntsc,[590,1822])==[{'kind':'generated_replacement_artwork','reference_range_frames':[590,982]},{'kind':'source_footage','reference_range_frames':[982,1386]},{'kind':'generated_replacement_artwork','reference_range_frames':[1386,1822]}]
    assert render_base_spans({**p,'reference_range_frames':[100,140]},[100,160])==[{'kind':'unclassified','reference_range_frames':[100,160]}]
    archival={**p,'media':{'archive':{'role':'archival_source_footage','type':'video'}},'clips':[{'source':'archive','in':0,'out':1}]}
    assert render_base_class(archival,[100,160])=='archival_source_footage'
    assert render_base_class({**archival,'media':{'archive':{'role':'archival_source_footage','type':'image'}}},[100,160])=='unclassified'
    assert render_base_class({**archival,'media':{'archive':{'role':'speech_source_footage','type':'video'}}},[100,160])=='unclassified'
    archived_mixed={**p,'media':{**p['media'],'archive':{'role':'archival_source_footage'}},'clips':[{'source':'raw','in':0,'out':.5},{'source':'archive','in':.5,'out':1}]}
    assert render_base_spans(archived_mixed,[100,160])==[{'kind':'source_footage','reference_range_frames':[100,130]},{'kind':'archival_source_footage','reference_range_frames':[130,160]}]
    assert render_base_class(archived_mixed,[100,160])=='mixed_base_media'
    assert render_base_class({**archived_mixed,'clips':archived_mixed['clips']+[archived_mixed['clips'][1]]},[100,160])=='unclassified'
    assert render_base_class({**generated,'media':{'replacement':{'role':'generated_replacement_artwork','type':'image'}}},[100,160])=='generated_replacement_artwork'
    assert render_base_class({**generated,'media':{'replacement':{'role':'generated_replacement_artwork','type':'video'}}},[100,160])=='unclassified'
    print(json.dumps({'status':'passed','checks':44,'scope':'36 existing checks plus 6 archival-role partitions and 2 image-only generated-artwork alias checks; keeps raw footage, explicit archival video and generated artwork distinct; rejects still-image archival role misuse, speech-only base roles and overlaps. Classification does not verify rights or source authenticity.'}))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--kit-dir',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--output',type=Path);parser.add_argument('--render-record',type=Path,action='append',default=[]);parser.add_argument('--snapshot-file-list',type=Path,help='JSON array of project/sidecar paths relative to kit; limits discovery for a frozen checkpoint while other agents continue.');parser.add_argument('--self-test',action='store_true');args=parser.parse_args()
    if args.self_test:self_test();return 0
    selected=None;selection_bytes=None
    if args.snapshot_file_list:
        selection_bytes=args.snapshot_file_list.read_bytes();names=json.loads(selection_bytes)
        if not isinstance(names,list) or not all(isinstance(p,str) and p for p in names):parser.error('Snapshot file list must be a JSON string array')
        selected=[(args.kit_dir/p).resolve() for p in names]
        if len(set(selected))!=len(selected) or any(not p.is_file() for p in selected):parser.error('Snapshot paths must exist and be unique')
    result=audit(args.kit_dir,args.render_record,selected)
    if selection_bytes is not None:result['render_execution']['discovery_scope']['selection_sha256']=hashlib.sha256(selection_bytes).hexdigest()
    target=args.output or args.kit_dir/'references/reconstruction-coverage.json';target=target.resolve()
    inputs={(args.kit_dir/r['path']).resolve() for r in result['input_integrity']['files']}
    if args.snapshot_file_list:inputs.add(args.snapshot_file_list.resolve())
    if target in inputs:parser.error('Output cannot overwrite an audited input.')
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(target),'snapshot_stable':result['input_integrity']['snapshot_stable'],'video_candidate_frames':result['video_mapping']['mapped_native_candidate_frames'],'video_total_frames':result['video_mapping']['total_native_output_frames'],'visual_gap_seconds':result['video_mapping']['unmapped_seconds'],'dialogue_text_reviewed':result['text_and_font_gates']['dialogue']['text_visually_reviewed'],'graphic_text_reviewed':result['text_and_font_gates']['independent_graphic_text']['text_visually_reviewed'],'clean_original_visual_assets':result['external_visual_assets']['clean_originals_available'],'listed_visual_assets':result['external_visual_assets']['listed_assets'],'exact_font_confirmed_dialogue':result['text_and_font_gates']['dialogue']['font_identity_confirmed'],'release_ready':False},ensure_ascii=False))
    return 0 if result['input_integrity']['snapshot_stable'] else 2


if __name__=='__main__':sys.exit(main())
