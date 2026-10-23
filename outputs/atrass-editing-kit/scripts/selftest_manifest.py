#!/usr/bin/env python3
"""Synthetic contract tests only. No Atrass footage or observations are represented."""
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from validate_manifest import LAYERS, validate


def fixture(directory):
    blob = b'SYNTHETIC CONTRACT FIXTURE: NOT VIDEO, NOT ATRASS EVIDENCE\n'
    (directory/'synthetic.txt').write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    def fact(value):
        return {'value':value,'status':'verified','reason':'Synthetic fixture only','evidence_refs':['review']}
    def number(value,unit='px'):
        return {**fact(value),'unit':unit,'uncertainty':0}
    def vector(value): return number(value)
    color_meta={k:fact(v) for k,v in {'range':'limited','primaries':'bt709','transfer':'bt709','matrix':'bt709','pixel_format':'yuv420p'}.items()}
    timeline={'reference_media_id':'ref','width':16,'height':16,'fps':{'num':4,'den':1},'time_base':{'num':1,'den':4},'frame_count':4,'color':color_meta,'metadata_status':'verified','metadata_reason':'Synthetic fixture'}
    video={k:v for k,v in timeline.items() if k not in ('reference_media_id','metadata_status','metadata_reason')}
    media=[{'id':i,'role':role,'origin_uri':None,'path':'synthetic.txt','sha256':sha,'byte_size':len(blob),'status':'verified','reason':'Synthetic fixture, not a decoded media file','video':video if i!='font' else None,'audio':None} for i,role in [('ref','reference'),('raw','raw_source'),('render','render'),('font','asset')]]
    evidence=[{'id':i,'path':'synthetic.txt','sha256':sha,'kind':kind,'media_id':mid,'frame':0,'time_tick':0,'time_seconds':0,'time_uncertainty_seconds':0,'description':'Synthetic fixture only'} for i,kind,mid in [('review','review_log','ref'),('comparison','render_comparison','render'),('frame0','frame','ref')]]
    interval={'start_frame':0,'end_frame':4}
    def event(ident,kind,data):
        return {'id':ident,'kind':kind,'layer':kind,'interval':interval.copy(),'status':'verified','confidence':1,'reason':'Synthetic fixture only','evidence_refs':['review'],'extra_parameters':[],'data':data}
    shot=event('shot1','shot',{'camera_id':fact('camera-A'),'shot_size':fact('medium'),'angle':fact('eye_level'),'camera_movement':fact('static'),'lens_mm':number(50,'mm'),'crop_ltrb_px':vector([0,0,16,16]),'reframe_position_px':vector([8,8]),'reframe_scale_xy':number([1,1],'ratio'),'rotation_deg':number(0,'degrees'),'anchor_px':vector([8,8]),'continuity_notes':'Synthetic fixture'})
    mapping=event('map1','mapping',{'source_media_id':'raw','source_start_tick':0,'source_end_tick':4,'speed':{'num':1,'den':1},'mapping_type':'cut','mapping_status':'verified','reason':'Synthetic fixture only'})
    span={'char_start':0,'char_end':2,'font_candidates':[{'family':'Fixture Sans','postscript_name':None,'weight':'400','confidence':1,'evidence_refs':['review']}],'selected_font':fact('Fixture Sans'),'font_asset_media_id':'font','size_px':number(4),'tracking_px':number(0),'line_height_px':number(5),'position_px':vector([8,12]),'bounds_px':vector([4,10,8,4]),'alignment':fact('center'),'fill_color_ids':['white'],'stroke':{'enabled':False,'width_px':number(0),'color_id':None,'alignment':'outside'},'shadow':{'enabled':False,'color_id':None,'offset_px':vector([0,0]),'blur_px':number(0),'opacity':number(0,'ratio')},'bar':{'enabled':False,'color_id':None,'bounds_px':vector([0,0,0,0]),'radius_px':number(0),'padding_px':vector([0,0,0,0]),'opacity':number(0,'ratio')},'decoration':fact('none')}
    caption=event('cap1','caption',{'text':'가나','spans':[span],'line_breaks':[],'speaker':fact('fixture speaker')})
    events=[shot,mapping,caption]
    ranges=[{'layer':layer,'interval':interval.copy(),'presence':'present' if any(e['layer']==layer for e in events) else 'absent','event_ids':[e['id'] for e in events if e['layer']==layer],'status':'verified','reason':'Synthetic fixture only','evidence_refs':['review']} for layer in LAYERS]
    reviewed=[{'interval':interval.copy(),'reviewer':'SYNTHETIC TEST','reviewed_at':'2026-10-20T00:00:00+09:00','evidence_refs':['review']}]
    return {'schema_version':'1.0','project_id':'SYNTHETIC_NOT_ATRASS','analysis_status':'ready_for_handoff','purpose':'reconstruction','media':media,'timeline':timeline,'evidence':evidence,'colors':[{'id':'white','role':'caption fill','sample_rgba':[255,255,255,255],'recipe_rgba':[255,255,255,255],'color_space':fact('sRGB'),'alpha_basis':'opaque_sample','rgb_uncertainty_8bit':0,'alpha_uncertainty_8bit':0,'measurement_method':'Synthetic constant','roi_px':[0,0,1,1],'status':'verified','reason':'Synthetic fixture only','evidence_refs':['review']}],'events':events,'coverage':{'sampled_frames':[0],'sampling_interval_seconds':None,'visual_review_mode':'frame_by_frame','visual_reviewed_ranges':copy.deepcopy(reviewed),'audio_review_mode':'listened','audio_reviewed_ranges':copy.deepcopy(reviewed),'layer_ranges':ranges,'design_color_inventory_complete':True,'notes':'Synthetic fixture only'},'uncertainties':[],'validation':{'replication_test':'passed','replication_evidence_refs':['comparison'],'comparison_criteria':[{'metric':'synthetic error','operator':'le','threshold':1,'measured':0,'unit':'test','passed':True}],'reviewer':'SYNTHETIC TEST','notes':'Record validator test, no render performed'}}


def main():
    schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/edit-event.schema.json').read_text())
    results=[]
    with tempfile.TemporaryDirectory(prefix='atrass-schema-test-') as temp:
        base=Path(temp); good=fixture(base)
        def run(name, mutate, draft=False, expect_error=True, fragment=None):
            item=copy.deepcopy(good); mutate(item)
            errors,warnings=validate(item,schema,base,draft)
            assert bool(errors)==expect_error, (name,errors,warnings)
            if fragment:
                assert any(fragment in e for e in errors+warnings), (name,errors,warnings)
            results.append({'case':name,'passed':True,'errors_detected':len(errors),'warnings':len(warnings)})
        run('synthetic complete record',lambda x:None,expect_error=False)
        run('draft cannot release',lambda x:x.update(analysis_status='draft'),fragment='research draft')
        run('draft null accepted with warning',lambda x:x['colors'][0].update(recipe_rgba=None,reason='Unmeasured'),draft=True,expect_error=False,fragment='incomplete color')
        run('release null color rejected',lambda x:x['colors'][0].update(recipe_rgba=None),fragment='incomplete color')
        run('required style field',lambda x:x['events'][2]['data']['spans'][0].pop('shadow'),draft=True,fragment='missing required')
        run('reversed interval',lambda x:x['events'][0]['interval'].update(start_frame=3,end_frame=1),draft=True,fragment='invalid [start,end)')
        run('visual coverage gap',lambda x:x['coverage']['visual_reviewed_ranges'][0]['interval'].update(end_frame=3),fragment='unreviewed')
        run('samples are not full review',lambda x:x['coverage'].update(visual_review_mode='sampled'),fragment='samples/playback')
        run('missing evidence file',lambda x:x['evidence'][0].update(path='MISSING'),draft=True,fragment='missing file')
        run('wrong source hash',lambda x:x['media'][0].update(sha256='0'*64),draft=True,fragment='sha256 mismatch')
        run('unknown color reference',lambda x:x['events'][2]['data']['spans'][0].update(fill_color_ids=['MISSING']),draft=True,fragment='unknown color')
        run('uncovered caption character',lambda x:x['events'][2]['data']['spans'][0].update(char_end=1),draft=True,fragment='every Unicode character')
        run('source interval mismatch',lambda x:x['events'][1]['data'].update(source_end_tick=1),draft=True,fragment='duration/speed mismatch')
        run('incorrect threshold pass',lambda x:x['validation']['comparison_criteria'][0].update(measured=10),draft=True,fragment='contradicts threshold')
        run('unknown style',lambda x:x['events'][2]['data']['spans'][0]['selected_font'].update(value=None,reason='Not identified'),fragment='unknown value')
        run('sample without frame evidence',lambda x:x['coverage'].update(sampled_frames=[0,2]),draft=True,fragment='lacks frame evidence')
        run('wrong geometry dimensions',lambda x:x['events'][0]['data']['anchor_px'].update(value=[1,2,3]),draft=True,fragment='expected 2 components')
        run('layer gap',lambda x:x['coverage']['layer_ranges'][-1]['interval'].update(end_frame=3),fragment='uncovered timeline')
        run('layer absence conflicts',lambda x:x['coverage']['layer_ranges'][0].update(presence='absent',event_ids=[]),draft=True,fragment='absent claim conflicts')
        def no_metadata(x):
            x['timeline'].update(width=None,height=None,fps=None,time_base=None,frame_count=None,metadata_status='unverified',metadata_reason='Not acquired')
            x['media'][0]['video']=None
            x['media'][0]['audio']=None
        run('unknown timeline can be drafted',no_metadata,draft=True,expect_error=False,fragment='metadata incomplete')
        run('unknown timeline cannot release',no_metadata,fragment='metadata incomplete')
        run('unverified source mapping',lambda x:x['events'][1]['data'].update(mapping_status='unverified'),fragment='mapping is unverified')
        def ratio_equivalent(x):
            x['timeline']['fps']={'num':8,'den':2}
            x['timeline']['time_base']={'num':2,'den':8}
        run('equivalent rational metadata',ratio_equivalent,expect_error=False)
    print(json.dumps({'synthetic_only':True,'passed':len(results),'cases':results},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
