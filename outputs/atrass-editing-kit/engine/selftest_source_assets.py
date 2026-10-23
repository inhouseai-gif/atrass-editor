#!/usr/bin/env python3
"""Focused source-asset timing/identity/transaction tests; synthetic fixtures only."""
from pathlib import Path
import argparse, copy, json, tempfile
import numpy as np
from PIL import Image
import apply_source_assets as mod


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--result',required=True);args=ap.parse_args()
    target=Path(args.result).resolve()
    if target.exists():raise ValueError('Preserve existing test result')
    rows=[]
    def check(label,value):
        rows.append({'name':label,'pass':bool(value)})
        if not value:raise AssertionError(label)
    with tempfile.TemporaryDirectory(prefix='source-assets-test-') as td:
        root=Path(td).resolve();raw=root/'synthetic-not-video.bin';raw.write_bytes(b'fixture-source-identity')
        asset=root/'asset.png';provenance=root/'provenance.json'
        rgba=np.zeros((40,80,4),np.uint8);rgba[:,:,:3]=[120,30,220];rgba[5:35,5:75,3]=128
        Image.fromarray(rgba).save(asset);provenance.write_text('{"synthetic_fixture":true}\n')
        p={'width':320,'height':180,'fps':'10','duration':1.5,'release_ready':False,
           'media':{'raw':{'path':'synthetic-not-video.bin','role':'source_footage','sha256':mod.sha(raw)}},'fonts':{},
           'clips':[],'audio':[],'layers':[{'id':'old-caption','kind':'caption','in':0.,'out':1.5,'z':10,'text':'unchanged'}],
           'audio_master':{'sample_peak_ceiling_dbfs':-1.5}}
        for ident,lo,source in [('a',0.,1.),('b',.5,1.7),('c',1.,.2)]:
            p['clips'].append({'id':ident,'in':lo,'out':lo+.5,'source':'raw','source_in':source,'rate':1,'geometry':{}})
            p['audio'].append({'id':ident+'-voice','in':lo,'out':lo+.5,'source':'raw','source_in':source,'rate':1,'gain_db':0})
        P=root/'input.json';P.write_text(json.dumps(p));inputbytes=P.read_bytes()
        asset_spec={'path':'asset.png','sha256':mod.sha(asset),'provenance':{'path':'provenance.json','sha256':mod.sha(provenance)}}
        event={'id':'test-art','source_range':[1.2,2.0],'take_ids':['b','a'],'asset':asset_spec,
               'layout':{'fit':'cover','position':[160,90],'size':[64,36],'z':5},
               'motion':{'scale_start':1.,'scale_end':1.2,'xy_start':[0.,0.],'xy_end':[20.,-10.]}}
        spec={'schema':mod.SCHEMA,'project_sha256':mod.sha(P),'source_sha256':mod.sha(raw),'events':[event]}
        E=root/'events.json';E.write_text(json.dumps(spec));eventbytes=E.read_bytes()
        output_dir=root/'another-location';output_dir.mkdir();O=output_dir/'new.json'
        r=mod.apply_assets(P,E,O);new=json.loads(O.read_text());added=new['layers'][1:]
        check('quiet-split-two-fragments',r['new_image_layers']==2)
        check('source-to-output-CFRceil',[e['source_asset_binding']['output_frames'] for e in added]==[[2,5],[5,8]])
        check('output-order-not-take-list-order',[e['source_asset_binding']['take_id'] for e in added]==['a','b'])
        check('old-clips-literal',new['clips']==p['clips'])
        check('old-audio-literal',new['audio']==p['audio'] and new['audio_master']==p['audio_master'])
        check('old-layers-literal',new['layers'][:1]==p['layers'])
        check('input-files-unmodified',P.read_bytes()==inputbytes and E.read_bytes()==eventbytes)
        check('runtime-media-rebased',(O.parent/new['media']['raw']['path']).resolve()==raw)
        check('motion-no-restart',abs(added[0]['scale_keys'][-1][1]-1.08)<1e-12 and abs(added[1]['scale_keys'][0][1]-1.12)<1e-12)
        check('motion-final-endpoint',added[1]['scale_keys'][-1][1]==1.2 and added[1]['x_keys'][-1][1]==180)
        check('motion-entity-local-time',added[1]['scale_keys'][-1][0]==.2)
        check('asset-below-dialogue',all(e['z']==5 for e in added))
        from render import Composer
        comp=Composer(new,O.parent)
        for fragment in added:
            binding=fragment['source_asset_binding']
            for j in range(binding['output_frames'][1]-binding['output_frames'][0]):
                im,xy=comp.image(fragment,fragment['in']+j/10)
                progress=(binding['event_selected_frame_offset']+j)/5
                expected=(round(160+20*progress-im.width/2),round(90-10*progress-im.height/2))
                if xy!=expected:raise AssertionError({'position':xy,'expected':expected,'size':im.size,'layer':fragment})
                check('actual-renderer-pan-position-'+str(binding['event_selected_frame_offset']+j),xy==expected)
        media=new['media']['source-asset-test-art'];derived=(O.parent/media['path']).resolve()
        check('derived-image-SHA',mod.sha(derived)==media['sha256'])
        check('alpha-not-double-applied',int(np.asarray(Image.open(derived))[18,32,3])==128)
        check('new-project-unapproved',new['release_ready'] is False and new['source_asset_application']['render_completed'] is False)
        check('original-asset-unmodified',mod.sha(asset)==asset_spec['sha256'])
        contained=mod.fitted_image(asset,[60,60],'contain');array=np.asarray(contained)
        check('contain-transparent-padding',array[:15,:,3].max()==0 and array[45:,:,3].max()==0)
        check('contain-original-alpha-preserved',int(array[30,30,3])==128)
        before=O.read_bytes()
        try:mod.apply_assets(P,E,O)
        except ValueError:check('output-create-only',O.read_bytes()==before)
        else:check('output-create-only',False)
        def reject(label,change,project_change=None):
            test=copy.deepcopy(spec);change(test)
            projectcopy=root/(label+'-input.json')
            pc=copy.deepcopy(p)
            if project_change:project_change(pc)
            projectcopy.write_text(json.dumps(pc))
            if project_change:test['project_sha256']=mod.sha(projectcopy)
            else:projectcopy=P
            path=root/(label+'.json');path.write_text(json.dumps(test));dest=output_dir/(label+'.json')
            try:mod.apply_assets(projectcopy,path,dest)
            except (ValueError,KeyError,TypeError):
                check(label,not dest.exists() and not dest.with_name(dest.stem+'.source-assets').exists())
            else:check(label,False)
        reject('wrong-project-SHA',lambda s:s.update(project_sha256='0'*64))
        reject('wrong-source-SHA',lambda s:s.update(source_sha256='0'*64))
        reject('wrong-asset-SHA',lambda s:s['events'][0]['asset'].update(sha256='0'*64))
        reject('wrong-provenance-SHA',lambda s:s['events'][0]['asset']['provenance'].update(sha256='0'*64))
        reject('unknown-root-key',lambda s:s.update(unknown=True))
        reject('unknown-event-key',lambda s:s['events'][0].update(hidden_filter='blur'))
        reject('unknown-take',lambda s:s['events'][0].update(take_ids=['missing']))
        reject('duplicate-take-ambiguous',lambda s:s['events'][0].update(take_ids=['a','a']))
        reject('take-without-intersection',lambda s:s['events'][0].update(take_ids=['c']))
        reject('collapsed-CFR-range',lambda s:s['events'][0].update(source_range=[1.201,1.202],take_ids=['a']))
        reject('unknown-fit',lambda s:s['events'][0]['layout'].update(fit='stretch'))
        reject('over-caption-z',lambda s:s['events'][0]['layout'].update(z=10))
        reject('zero-scale',lambda s:s['events'][0]['motion'].update(scale_end=0))
        reject('negative-scale',lambda s:s['events'][0]['motion'].update(scale_start=-1))
        reject('nonfinite-xy',lambda s:s['events'][0]['motion'].update(xy_end=[float('nan'),0]))
        reject('unknown-motion-key',lambda s:s['events'][0]['motion'].update(rotation=4))
        reject('audio-source-clock-mismatch',lambda s:None,lambda pc:pc['audio'][0].update(source_in=1.01))
        reject('video-rate-change',lambda s:None,lambda pc:pc['clips'][0].update(rate=2))
        reject('native-frame-map',lambda s:None,lambda pc:pc['clips'][0].update(source_frame_times=[1.]*5))
        reject('ambiguous-two-voice-pieces',lambda s:None,lambda pc:pc['audio'].append(copy.deepcopy(pc['audio'][0])))
        # Reorder source occurrence is explicit and maps to its selected output take.
        reverse=copy.deepcopy(spec);reverse['events'][0].update(source_range=[.25,.65],take_ids=['c'])
        reverse['events'][0].pop('motion');R=root/'reordered-events.json';R.write_text(json.dumps(reverse));D=output_dir/'reordered.json'
        mod.apply_assets(P,R,D);rd=json.loads(D.read_text())
        check('reordered-source-maps-output',[rd['layers'][-1]['in'],rd['layers'][-1]['out']]==[1.1,1.5])
        check('static-has-no-animation-keys',not any(k.endswith('_keys') for k in rd['layers'][-1]))
        # A photograph added after a distant chapter is muted must not restore
        # that chapter's audio or erase earlier graphic provenance.
        muted=copy.deepcopy(p);muted['audio']=muted['audio'][:2]
        muted['source_asset_application']={'events':[{'id':'earlier-art'}]}
        muted['source_asset_application_history']=[{'events':[{'id':'first-art'}]}]
        MP=root/'muted.json';MP.write_text(json.dumps(muted))
        scoped=copy.deepcopy(spec);scoped.update(project_sha256=mod.sha(MP),audio_clock_scope='event_takes_only')
        scoped['events'][0]['asset']['role']='licensed_source_photo_candidate'
        SE=root/'scoped-events.json';SE.write_text(json.dumps(scoped));SO=output_dir/'scoped.json'
        mod.apply_assets(MP,SE,SO);sp=json.loads(SO.read_text())
        check('later-photo-keeps-chapter-silence',sp['audio']==muted['audio'])
        check('later-photo-keeps-existing-layers',sp['layers'][:1]==muted['layers'])
        check('later-photo-preserves-previous-provenance',sp['source_asset_application_history']==muted['source_asset_application_history']+[muted['source_asset_application']])
        check('licensed-photo-role-not-generated',sp['media']['source-asset-test-art']['role']=='licensed_source_photo_candidate')
        check('scoped-voice-contract-recorded',sp['source_asset_application']['audio_clock_scope']=='event_takes_only' and sp['source_asset_application']['audio_clock_checked_take_ids']==['a','b'])
        reject('default-rejects-unrelated-silence',lambda s:None,lambda pc:pc.update(audio=pc['audio'][:2]))
        reject('scoped-target-silence-rejected',lambda s:s.update(audio_clock_scope='event_takes_only'),lambda pc:pc.update(audio=pc['audio'][1:]))
        reject('scoped-target-clock-mismatch',lambda s:s.update(audio_clock_scope='event_takes_only'),lambda pc:pc['audio'][0].update(source_in=1.01))
        reject('scoped-unknown-take',lambda s:(s.update(audio_clock_scope='event_takes_only'),s['events'][0].update(take_ids=['unknown'])))
        reject('scoped-video-gap-still-rejected',lambda s:s.update(audio_clock_scope='event_takes_only'),lambda pc:pc['clips'][2].update(**{'in':1.1}))
        reject('unknown-clock-scope',lambda s:s.update(audio_clock_scope='none'))
        reject('unknown-photo-role',lambda s:s['events'][0]['asset'].update(role='verified_original'))
        # Inject a write-time failure only into this fixture; cleanup owns new paths.
        saved=mod.fitted_image
        def fail(*a,**k):raise ValueError('injected save failure')
        mod.fitted_image=fail
        dest=output_dir/'write-failure.json'
        try:
            try:mod.apply_assets(P,E,dest)
            except ValueError:check('failed-write-cleans-own-derived-directory',not dest.exists() and not dest.with_name(dest.stem+'.source-assets').exists())
            else:check('failed-write-cleans-own-derived-directory',False)
        finally:mod.fitted_image=saved
        check('failure-keeps-existing-output',O.read_bytes()==before and derived.is_file())
        # Mutation after verification must fail before JSON publication.
        def mutate(*a,**k):
            E.write_bytes(eventbytes+b' ')
            return saved(*a,**k)
        mod.fitted_image=mutate;dest=output_dir/'changed-input.json'
        try:
            try:mod.apply_assets(P,E,dest)
            except ValueError:check('mid-application-input-mutation-rejected',not dest.exists() and not dest.with_name(dest.stem+'.source-assets').exists())
            else:check('mid-application-input-mutation-rejected',False)
        finally:mod.fitted_image=saved;E.write_bytes(eventbytes)
    result={'pass':all(x['pass'] for x in rows),'checks':len(rows),'rows':rows,'implementation_sha256':mod.sha(Path(mod.__file__)),
            'selftest_sha256':mod.sha(Path(__file__)),'synthetic_fixtures_only':True,'source_media_decoded':False,'rendered_frames':0,
            'direct_listening_seconds':0,'release_ready':False}
    with target.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'pass':result['pass'],'checks':result['checks']}))


if __name__=='__main__':main()
