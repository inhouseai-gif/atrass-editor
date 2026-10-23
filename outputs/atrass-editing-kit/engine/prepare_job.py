#!/usr/bin/env python3
"""Prepare a new raw-footage job for the Atrass editing agent.

Probes/hashes the raw file, imports or locally transcribes speech, and preserves
editorial candidates for an explicit edit plan. It never silently approves ASR,
retake deletion, graphic assets, or a completed export.
"""
from pathlib import Path
import argparse,difflib,hashlib,json,re,shutil,sys
import av

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def verify_asr_source(asr,raw,raw_sha256):
    """A stored hash permits relocation; an absent hash requires a legacy path match."""
    expected=asr.get('source_sha256');original=asr.get('input')
    if expected is not None:
        if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',expected):
            raise ValueError('ASR source_sha256 must be a 64-character SHA-256 digest')
        if expected.lower()!=raw_sha256.lower():raise ValueError('ASR source_sha256 differs from requested raw footage')
        method='sha256_match'
    else:
        if not isinstance(original,str) or not original.strip():
            raise ValueError('ASR needs source_sha256 or an input path matching the requested raw footage')
        if Path(original).resolve()!=raw.resolve():
            raise ValueError('ASR input path differs from requested raw footage; a verified source_sha256 is required to relocate it')
        method='legacy_same_path_no_historical_hash'
    return {'method':method,'original_input':original,'requested_raw':str(raw),
            'requested_raw_sha256':raw_sha256,'transcript_text_verified':False}
def candidates(segments):
    norm=lambda s:re.sub(r'[^가-힣a-z0-9]','',s.lower())
    out=[]
    for i,s in enumerate(segments):
        text=s['text'].strip()
        if re.search(r'잠시.{0,8}멈|다시.{0,6}(할게|하겠|갈게|찍)|영상.{0,8}(끊|끊지)|시작할까|촬영.{0,8}(중단|시작|끝)',text):out.append({'kind':'production_discussion_candidate','raw_range':[s['start'],s['end']],'text':text,'decision':None})
        a=norm(text)
        if len(a)<10:continue
        for later in segments[i+1:i+8]:
            if later['start']-s['end']>30:break
            similarity=difflib.SequenceMatcher(None,a,norm(later['text'])).ratio()
            if similarity>=.88:out.append({'kind':'possible_retake_or_intentional_repetition','first':[s['start'],s['end']],'later':[later['start'],later['end']],'text':text,'similarity':similarity,'decision':None})
    return out

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--raw',required=True,type=Path);ap.add_argument('--variant',choices=['A','B'],required=True);ap.add_argument('--output-dir',required=True,type=Path)
    g=ap.add_mutually_exclusive_group(required=True);g.add_argument('--asr-json',type=Path);g.add_argument('--model-dir',type=Path)
    ap.add_argument('--normalize-asr-overlap',action='store_true',help='Derive a transcript with duplicate timed chunk ownership resolved; preserve original and receipt')
    ap.add_argument('--ffmpeg',default='ffmpeg');a=ap.parse_args();raw=a.raw.resolve(strict=True);out=a.output_dir.resolve()
    if out.exists():ap.error('Choose a new job directory; existing work is preserved.')
    with av.open(str(raw)) as c:
        v=c.streams.video[0];meta={'path':str(raw),'bytes':raw.stat().st_size,'sha256':digest(raw),'width':v.width,'height':v.height,'fps':str(v.average_rate),'time_base':str(v.time_base),'duration_seconds':c.duration/av.time_base,'audio_streams':len(c.streams.audio)}
    if a.asr_json:
        asr=json.loads(a.asr_json.read_text(encoding='utf-8'))
        if not isinstance(asr.get('segments'),list):raise ValueError('ASR JSON needs a segments array')
        source_validation=verify_asr_source(asr,raw,meta['sha256'])
        asr_mode='existing_local_transcript_imported'
    else:
        out.mkdir(parents=True)
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from audio_audit import decode,transcribe_local
        x=decode(raw,out/'analysis-audio-16k.wav',a.ffmpeg)
        asr=transcribe_local(x,a.model_dir.resolve(),out,'raw',method='continuous')
        asr['input']=str(raw);asr['source_sha256']=meta['sha256']
        source_validation={'method':'transcribed_from_current_hashed_raw','requested_raw':str(raw),'requested_raw_sha256':meta['sha256'],'transcript_text_verified':False}
        asr_mode='local_mlx_whisper_existing_model'
    for s in asr['segments']:
        if not 0<=s['start']<=s['end']<=meta['duration_seconds']+.5:raise ValueError('ASR segment is outside the raw duration')
    if a.asr_json:out.mkdir(parents=True)
    normalization=None
    # Newly inferred local ASR always records safe empty-metadata cleanup.
    # Existing imported transcripts retain the opt-in derivation contract.
    if a.normalize_asr_overlap or not a.asr_json:
        from normalize_asr_overlap import normalize
        write(out/'transcript-original.json',asr)
        if 'chunk_overlap_normalization' in asr:
            normalization={'status':'already_derived_input_preserved','input_transcript_sha256':digest(out/'transcript-original.json'),
                           'prior_derivation':asr['chunk_overlap_normalization'],'new_word_records_collapsed':0,
                           'transcript_text_verified':False,'direct_listening_seconds':0,'release_ready':False}
        else:
            original_sha=digest(out/'transcript-original.json')
            asr=dict(asr);asr['source_sha256']=meta['sha256']
            asr,normalization=normalize(asr,original_sha)
        write(out/'transcript-normalization.json',normalization)
    write(out/'transcript.json',asr)
    editorial=candidates(asr['segments']);write(out/'editorial-candidates.json',{'status':'unreviewed_candidates_not_auto_delete','candidates':editorial})
    layers=['base_cuts_and_source_mapping','voice_cuts_and_sync','crop_scale_position','body_captions','large_emphasis','handwritten_questions','side_headings_and_labels','chapter_cards','photos_illustrations_pip','chat_bubbles','icons_and_strokes','persistent_logo_variant_B','per_span_fill_stroke_shadow_gradient_opacity','transitions_and_frame_holds','voice_processing','music_and_sfx','output_color_management','reference_comparison_and_audio_review']
    write(out/'coverage-checklist.json',{'release_ready':False,'variant':a.variant,'layers':[{'name':x,'decision':'unresolved','evidence':None,'notes':None} for x in layers],'rule':'Each item needs implemented-and-verified or not-applicable-with-evidence. Missing assets/fonts/timing must stay unresolved.'})
    job={'schema':'atrass-job-v1','status':'input_prepared_edit_plan_pending','variant':a.variant,'raw':meta,'asr_mode':asr_mode,'asr_text_verified':False,'direct_listening_seconds':0,'editorial_candidate_count':len(editorial),'files':{'transcript':'transcript.json','editorial_candidates':'editorial-candidates.json','coverage':'coverage-checklist.json'},'next':'Agent reads full transcript, compares local source evidence, resolves keep/drop and text/graphic events, then builds atrass-render-v1 JSON. Use the renderer and comparison tools. Do not substitute this job record for a rendered completed video.'}
    job['asr_source_validation']=source_validation
    if normalization is not None:
        job['files'].update(original_transcript='transcript-original.json',transcript_normalization='transcript-normalization.json')
        job['asr_normalization']={'status':normalization.get('status','derived_chunk_ownership_only'),
            'duplicate_word_records_collapsed':normalization.get('duplicate_word_records_collapsed',0),
            'empty_metadata_cues_removed':normalization.get('empty_metadata_cues_removed',0),
            'original_transcript_sha256':digest(out/'transcript-original.json'),
            'derived_transcript_sha256':digest(out/'transcript.json'),
            'report_sha256':digest(out/'transcript-normalization.json'),
            'direct_listening_seconds':0,'source_video_or_audio_edited':False,'release_ready':False}
    write(out/'job.json',job);print(json.dumps({'job':str(out/'job.json'),'status':job['status'],'raw_sha256':meta['sha256'],'segments':len(asr['segments']),'editorial_candidates':len(editorial)},ensure_ascii=False))
if __name__=='__main__':main()
