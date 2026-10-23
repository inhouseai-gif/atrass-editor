#!/usr/bin/env python3
"""Validate reviewed A text/chat structure. Does not certify rendered likeness."""
from pathlib import Path
import argparse,json,hashlib,math

def validate(package):
 p=Path(package);d=json.loads((p/'references/A-layer-events.json').read_text());a=json.loads((p/'references/A-caption-timeline.json').read_text());errors=[];checks={}
 def ck(name,value):
  checks[name]=bool(value)
  if not value:errors.append(name)
 def interval(x):return isinstance(x.get('in_frame'),int) and isinstance(x.get('out_frame'),int) and 0<=x['in_frame']<x['out_frame']
 def box(b):return isinstance(b,list) and len(b)==4 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in b) and 0<=b[0]<b[2]<=1920 and 0<=b[1]<b[3]<=1080
 events=d['events'];active=[e for e in events if e.get('render_enabled')];chats=d.get('chat_composites',[]);ids=[e['id'] for e in events]+[c['id'] for c in chats]
 ck('shared_source_sha256',d['source_sha256']==a['source_sha256']);ck('unique_event_and_chat_ids',len(ids)==len(set(ids)));ck('active_text_intervals_positive',all(interval(e) for e in active));ck('excluded_text_never_rendered',all(not e.get('render_enabled') for e in events if e.get('exclude_from_render') or e.get('superseded_by')));ck('active_text_has_verified_gate',all(not e.get('review_required') and 'verified' in e.get('text_verification','') for e in active));ck('active_text_bboxes_in_canvas',all(box(e['bbox_1080']) for e in active));ck('active_text_cfr_from_frame',all(abs(e['in_frame']*1001/60000-e['render_in_seconds_cfr_60000_1001'])<1e-9 and abs(e['out_frame']*1001/60000-e['render_out_seconds_cfr_60000_1001'])<1e-9 for e in active))
 for e in active:
  op=e.get('render_hints',{}).get('opacity',{})
  if op.get('kind')=='observed_per_frame_lut':
   ss=op['samples'];ck(e['id']+'_opacity_order_bounds',all(e['in_frame']<=x['frame']<e['out_frame'] and 0<=x['alpha']<=1 for x in ss) and all(x['frame']<y['frame'] for x,y in zip(ss,ss[1:])))
  components=e.get('render_hints',{}).get('recovered_components',[])
  if components:
   ck(e['id']+'_superseded_group_lut_disabled',op.get('render_eligible') is False and op.get('kind')=='superseded_white_glyph_encoded_rgb_ratio')
   ck(e['id']+'_component_roles_order',[(c['role'],c['z_within_event']) for c in components]==[('backing',0),('glyph',1)])
   for c in components:
    ss=c['opacity']['samples'];asset=p/c['asset'];prov=p/c['provenance']
    ck(c['id']+'_interval',c['in_frame']==e['in_frame'] and c['out_frame']==e['out_frame'])
    ck(c['id']+'_geometry',box(c['asset_bbox_1080']) and c['rotation_baked_into_asset'] is True and c['additional_rotation_degrees']==0)
    ck(c['id']+'_asset_hash',asset.is_file() and hashlib.sha256(asset.read_bytes()).hexdigest()==c['sha256'] and prov.is_file())
    ck(c['id']+'_opacity_support',bool(ss) and ss[0]['frame']==e['in_frame'] and ss[-1]['frame']==e['out_frame']-1 and all(e['in_frame']<=x['frame']<e['out_frame'] and 0<=x['alpha']<=1 for x in ss) and all(x['frame']<y['frame'] for x,y in zip(ss,ss[1:])))
    ck(c['id']+'_component_only_opacity',c['opacity']['apply_to']=='this_component_only' and c['contains_background_rgb_region'] is False and c['alpha_is_original'] is False)
 for c in chats:
  states=c['layout_states'];ck(c['id']+'_continuous_layout_support',interval(c) and states[0]['in_frame']==c['in_frame'] and states[-1]['out_frame']==c['out_frame'] and all(interval(s) for s in states) and all(x['out_frame']==y['in_frame'] for x,y in zip(states,states[1:])));ck(c['id']+'_component_common_support',all(c[k]['in_frame']==c['in_frame'] and c[k]['out_frame']==c['out_frame'] for k in ['panel','sender','avatar']))
  mids=[m['id'] for m in c['messages']];ck(c['id']+'_unique_messages',len(mids)==len(set(mids)))
  for m in c['messages']:
   ss=m['layout_states'];ck(m['id']+'_continuous_lifetime',interval(m) and m['out_frame']==c['out_frame'] and ss[0]['in_frame']==m['in_frame'] and ss[-1]['out_frame']==m['out_frame'] and all(interval(s) and box(s['bubble_bbox_1080']) and box(s['text_glyph_bbox_1080']) for s in ss) and all(x['out_frame']==y['in_frame'] for x,y in zip(ss,ss[1:])));ck(m['id']+'_each_live_stage_present',all((m['id'] in [v['message_id'] for v in s['messages']])==(m['in_frame']<=s['in_frame']<m['out_frame']) for s in states))
  asset=p/c['avatar']['asset'];prov=c['avatar']['asset_provenance'];ck(c['id']+'_avatar_asset_hash',asset.is_file() and hashlib.sha256(asset.read_bytes()).hexdigest()==prov['sha256']);ck(c['id']+'_all_state_evidence_exists',all((p/s['source_evidence']).is_file() for s in states))
 replacements={e['id'] for e in events}|{c['id'] for c in chats};ck('all_superseded_component_references_resolve',all(all(x in replacements for x in e.get('replacement_component_ids',[])) for e in events))
 ck('chat04_first_avatar_matches_native_crop',next(c for c in chats if c['id']=='A-chat-04')['avatar']['layout_states'][0]['asset_bbox_1080']==[117,339,174,396]);ck('release_not_falsely_certified',d['render_gate'].get('release_ready') is False)
 return {'passed':not errors,'checks':checks,'errors':errors,'approved_text_events':len(active),'chat_composites':len(chats),'independent_chat_messages':sum(len(c['messages']) for c in chats),'limits':'Data integrity and reviewed support only. Exact fonts, authored RGBA/alpha, remaining non-text assets, audio and full-length rendered likeness are not certified.'}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--package',type=Path,default=Path(__file__).resolve().parent.parent);ap.add_argument('--output',type=Path);args=ap.parse_args();r=validate(args.package)
 if args.output:
  q=args.output.with_suffix(args.output.suffix+'.tmp');q.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');q.replace(args.output)
 print(json.dumps({'passed':r['passed'],'checks':len(r['checks']),'errors':r['errors']}));raise SystemExit(0 if r['passed'] else 1)
if __name__=='__main__':main()
