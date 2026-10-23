#!/usr/bin/env python3
"""Create a conservative, source-bound dialogue draft without an episode recipe.

All ASR text is retained. Only short internal measured silence may be shortened.
This is mechanical draft automation, not semantic retake/style/audio approval.
"""
from pathlib import Path
from fractions import Fraction
import argparse
import json
import math
import re
import unicodedata

import av
from fontTools.ttLib import TTFont
from run_longform import binding, read, save, sha, verify_bindings
from review_movie import require
from prepare_job import candidates
from suggest_quiet_cuts import contiguous_audio_clock, measure, merge, suggest
from render import Composer

KIT = Path(__file__).resolve().parents[1]
POLICY = {'id': 'atrass-conservative-dialogue-draft-v1', 'max_caption_characters': 28,
          'max_caption_seconds': 4.0, 'caption_split_gap_seconds': .35,
          'max_caption_ink_width': 1640, 'speech_guard_seconds': .15,
          'retained_quiet_per_edge_seconds': .20, 'minimum_quiet_seconds': .80,
          'minimum_cut_seconds': .30, 'maximum_internal_quiet_seconds': 3.0,
          'maximum_removed_seconds_per_gap': 2.0,
          'rms_threshold_dbfs': -42., 'peak_threshold_dbfs': -30.}


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def text_line(text):
    require(isinstance(text, str) and text.strip(), 'Missing ASR text; do not silently discard cues')
    value = unicodedata.normalize('NFC', ' '.join(text.split()))
    require(not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value), 'Unsupported transcript control characters')
    return value


def letters(text):
    return re.sub(r'\s', '', unicodedata.normalize('NFC', text))


def speech_units(transcript, duration):
    segments = transcript.get('segments')
    require(isinstance(segments, list) and segments, 'Nonempty ASR segments required')
    units, fallback, previous = [], [], -1
    for i, segment in enumerate(segments):
        a, b = segment.get('start'), segment.get('end')
        require(all(number(x) for x in (a,b)) and 0 <= a < b <= duration,
                f'Invalid cue timing at {i}; timing repair required')
        require(a >= previous, 'ASR cues are out of order; explicit repair required')
        previous = a
        text = text_line(segment.get('text'))
        words = segment.get('words')
        valid = isinstance(words, list) and bool(words)
        end = a
        if valid:
            for word in words:
                x, y = word.get('start'), word.get('end')
                if not (all(number(v) for v in (x,y)) and a <= x < y <= b
                        and x >= end and isinstance(word.get('word'), str) and word['word'].strip()):
                    valid = False
                    break
                end = y
            if valid and letters(''.join(w['word'] for w in words)) != letters(text):
                valid = False
        if valid:
            for j, word in enumerate(words):
                units.append({'start': word['start'], 'end': word['end'],
                              'text': text_line(word['word']), 'refs': [[i,j]], 'cue_indices': [i]})
        else:
            # No invented interpolation or automatic ASR repair. A bad word
            # alignment falls back to the complete cue and its complete text.
            units.append({'start': a, 'end': b, 'text': text, 'refs': [[i,None]], 'cue_indices': [i]})
            fallback.append(i)
    # Conflicting ASR ownership windows become one display window. Text is
    # retained verbatim (apart from whitespace), including duplicate chunk text.
    # Never infer that overlapping text or negation is a dispensable retake.
    units.sort(key=lambda x: (x['start'], x['refs'][0][0], -1 if x['refs'][0][1] is None else x['refs'][0][1]))
    merged = []
    overlaps = []
    for unit in units:
        if merged and unit['start'] < merged[-1]['end'] - 1e-9:
            p = merged[-1]
            p['end'] = max(p['end'], unit['end'])
            p['text'] += ' ' + unit['text']
            p['refs'] += unit['refs']
            p['cue_indices'] = sorted(set(p['cue_indices']+unit['cue_indices']))
            overlaps.append(unit['refs'])
        else:
            merged.append(dict(unit))
    return merged, {'fallback_whole_cues': fallback, 'overlap_groups_merged_without_deduplication': overlaps,
                    'input_cue_count': len(segments), 'input_text_characters_without_spaces': sum(len(letters(s['text'])) for s in segments)}


def caption_groups(units, fps, fits):
    groups = []
    for unit in units:
        require(fits(unit['text']), 'An indivisible ASR window is too wide; correct word timings before splitting its text: '+str(unit['refs']))
        join = False
        if groups:
            p = groups[-1]
            proposed = p['text'] + ' ' + unit['text']
            collapses = math.ceil(p['start']*float(fps)-1e-8) >= math.ceil(p['end']*float(fps)-1e-8)
            join = collapses or (len(proposed) <= POLICY['max_caption_characters']
                and unit['end']-p['start'] <= POLICY['max_caption_seconds']
                and unit['start']-p['end'] <= POLICY['caption_split_gap_seconds']
                and not re.search(r'[.!?。！？]$', p['text']))
            join = join and fits(proposed)
        if join:
            groups[-1].update(end=unit['end'], text=proposed,
                refs=groups[-1]['refs']+unit['refs'],
                cue_indices=sorted(set(groups[-1]['cue_indices']+unit['cue_indices'])))
        else:
            groups.append(dict(unit))
    for group in groups:
        require(math.ceil(group['end']*float(fps)-1e-8) > math.ceil(group['start']*float(fps)-1e-8),
                'ASR display duration collapses at output frame rate; timing repair required')
    return groups


def choose_cuts(quiet, groups, frames, fps):
    """Apply only draft-policy short internal gaps, keeping caption lifetimes."""
    r = float(fps)
    accepted, ledger = [], []
    for i, q in enumerate(quiet):
        a,b = q['remove_source_range']; qa,qb = q['quiet_range']
        reason = None
        if qa < groups[0]['start'] or qb > groups[-1]['end']:
            reason = 'preserve_head_and_tail_without_semantic_decisions'
        elif qb-qa > POLICY['maximum_internal_quiet_seconds']:
            reason = 'long_silent_action_or_demonstration_requires_visual_decision'
        # Reduce at most2seconds from the middle, preserving both speech edges.
        if b-a > POLICY['maximum_removed_seconds_per_gap']:
            center=(a+b)/2; a=center-POLICY['maximum_removed_seconds_per_gap']/2; b=center+POLICY['maximum_removed_seconds_per_gap']/2
        lo,hi = math.ceil(a*r-1e-8), math.floor(b*r+1e-8)
        if not reason and (not 0 < lo < hi < frames or (hi-lo)/r < POLICY['minimum_cut_seconds']):
            reason = 'too_short_after_inward_frame_rounding'
        if not reason and any(lo/r < g['end']+POLICY['speech_guard_seconds'] and hi/r > g['start']-POLICY['speech_guard_seconds'] for g in groups):
            reason = 'caption_or_speech_guard_conflict'
        row={'candidate_index':i,'source_range_requested':[a,b],'source_frames':[lo,hi],
             'action':'preserve' if reason else 'shorten_in_draft','reason':reason or 'short_internal_low_energy_gap_outside_all_ASR_caption_guards'}
        ledger.append(row)
        if not reason: accepted.append([lo,hi])
    accepted.sort()
    require(all(a[1]<=b[0] for a,b in zip(accepted,accepted[1:])), 'Quiet cut candidates overlap')
    return accepted, ledger


def plan_from_evidence(raw, duration, source_sha, transcript, transcript_sha, groups, cuts, fps, font):
    r=float(fps); frames=math.floor(duration*r+1e-8)
    spans=[]; cursor=0
    for lo,hi in cuts:
        if cursor < lo:
            spans.append((cursor,lo))
        cursor=hi
    if cursor < frames:
        spans.append((cursor,frames))
    takes=[]; maps=[]; out=0
    for i,(lo,hi) in enumerate(spans):
        require(lo<hi,'Empty kept interval')
        tid=f'auto-take-{i+1:04d}'
        takes.append({'id':tid,'source':'raw','source_in':lo/r,'source_out':hi/r,'rate':1,
                      'voice_gain_db':0,'status':'conservative_automatic_draft_not_semantic_approval'})
        maps.append({'take_id':tid,'source_frames':[lo,hi],'output_frames':[out,out+hi-lo]});out+=hi-lo
    events=[]; caption_ledger=[]
    for i,group in enumerate(groups):
        a,b=group['start'],group['end']
        matches=[m for m in maps if m['source_frames'][0]/r <= a+1e-8 and b <= m['source_frames'][1]/r+1e-8]
        require(len(matches)==1,'Spoken ASR window would be cut or exceed quantized raw tail')
        m=matches[0]; offset=(m['output_frames'][0]-m['source_frames'][0])/r
        lo=math.ceil((a+offset)*r-1e-8);hi=math.ceil((b+offset)*r-1e-8)
        require(m['output_frames'][0]<=lo<hi<=m['output_frames'][1], 'Caption escapes its take')
        cid=f'auto-caption-{i+1:04d}'
        source_binding={'take_id':m['take_id'],'transcript_sha256':transcript_sha,
                        'source_range':[a,b],'asr_refs':group['refs'],'asr_text_verified':False}
        events.append({'id':cid,'template':'dialogue','in':lo/r,'out':hi/r,'text':group['text'],
                       'tracking':0,'source_dialogue_binding':source_binding})
        caption_ledger.append({'id':cid,'source_range':[a,b],'output_frames':[lo,hi],
                               'text':group['text'],'source_binding':source_binding})
    require(all(a['out']<=b['in']+1e-8 for a,b in zip(events,events[1:])), 'Quantized captions overlap')
    plan={'schema':'atrass-edit-plan-v1','title':'원본 전사 기반 자동 초안 · 의미 편집 검수 전',
          'variant':'A','fps':str(fps),'release_ready':False,
          'media':{'raw':{'path':str(raw),'role':'source_footage','sha256':source_sha}},
          'fonts':{'body':{'path':str(font),'weight':450,'status':'measured_A_candidate_not_original_font_confirmed'}},
          'takes':takes,'audio_follow_takes':True,'audio_master':{'sample_peak_ceiling_dbfs':-1.5},
          'events':events,'automatic_draft':{'policy':POLICY,'semantic_decisions_inferred':False,
          'transcript_sha256':transcript_sha,'release_ready':False,
          'limitations':['All ASR speech retained, including production talk and retakes. ASR errors remain unverified.',
              'Only short measured quiet gaps trimmed; visual intent and direct listening not certified.',
              'Dialogue style only; chapter/art/emphasis/BGM/SFX decisions remain unresolved.']}}
    return plan, {'take_mapping':maps,'captions':caption_ledger,'source_full_frames':frames,'output_frames':out,
                  'removed_frames':frames-out,'tail_CFR_rounding_seconds':duration-frames/r}


def build(job_path, output, ffmpeg, preserve_all_gaps=False):
    job_path=Path(job_path).resolve(strict=True);output=Path(output).absolute()
    require(not output.exists() and not output.is_symlink(),'Choose a new draft directory; existing work is preserved')
    job=read(job_path);require(job.get('schema')=='atrass-job-v1','Prepared job required')
    require(job.get('variant')=='A','Automatic dialogue draft currently supports A; B logo/style needs explicit decisions')
    raw=Path(job['raw']['path']);raw=raw.resolve(strict=True) if raw.is_absolute() else (job_path.parent/raw).resolve(strict=True)
    source=binding(raw);require(job['raw']['sha256']==source['sha256'],'Job raw identity differs')
    tp=(job_path.parent/job['files']['transcript']).resolve(strict=True);transcript=read(tp);tb=binding(tp)
    require(transcript.get('source_sha256')==source['sha256'],'Transcript source identity differs')
    font=KIT/'assets/fonts/SUIT-Variable.ttf'
    require(sha(font)=='12e1fea375d27869a686d7432c1e6a38154d394994f5d82ace24f36a7db68333','Measured candidate font bytes differ')
    inputs=[source,binding(job_path),tb,binding(font),binding(ffmpeg)]
    inputs.extend(binding(p) for p in sorted((KIT/'engine').glob('*.py')))
    with av.open(str(raw)) as container:
        require(container.streams.video and container.streams.audio,'Video and original audio required')
        video,audio=container.streams.video[0],container.streams.audio[0]
        require(video.start_time==0 and audio.start_time==0,'Nonzero media time origin unsupported')
        container_duration=container.duration/av.time_base;native=Fraction(video.average_rate)
        require(video.duration is not None and audio.duration is not None,
                'Declared video/audio coverage required for automatic draft; inspect missing track duration first')
        video_end=float(video.duration*video.time_base);audio_end=float(audio.duration*audio.time_base)
        require(video_end>0 and audio_end>0,'Positive video/audio track coverage required')
        duration=min(video_end,audio_end)
        rate,channels=audio.rate,audio.codec_context.channels
        require(abs(container_duration-job['raw']['duration_seconds'])<1e-6,'Job duration differs from actual media')
    coverage={'policy':'common_declared_video_audio_coverage_no_implicit_hold_or_padding',
              'container_seconds':container_duration,'video_seconds':video_end,'audio_seconds':audio_end,
              'usable_common_seconds':duration,'video_tail_outside_common_seconds':max(0,video_end-duration),
              'audio_tail_outside_common_seconds':max(0,audio_end-duration),
              'ASR_outside_common_coverage_policy':'reject_for_explicit_editorial_resolution_not_silent_drop'}
    fps=Fraction(60000,1001)
    units,normalization=speech_units(transcript,duration)
    comp=Composer({'width':1920,'height':1080,'fonts':{'body':{'path':str(font),'weight':450}}},output)
    with TTFont(str(font),lazy=True) as ft:
        cmap=ft.getBestCmap()
    def fits(t):
        require(all(c.isspace() or ord(c) in cmap for c in t),'Transcript contains a glyph missing from the candidate font')
        mask=comp.text_mask(t,{'font':'body','font_size':54,'tracking':0})
        return 0<mask.width<=POLICY['max_caption_ink_width'] and 0<mask.height<=64
    groups=caption_groups(units,fps,fits)
    protected=merge([[max(0,g['start']-POLICY['speech_guard_seconds']),min(duration,g['end']+POLICY['speech_guard_seconds'])] for g in groups])
    quiet=[]; measurements={'performed':False,'policy':'preserve_all_gaps'}
    if not preserve_all_gaps:
        clock=contiguous_audio_clock(raw,rate)
        runs,metrics=measure(raw,ffmpeg,rate,channels,POLICY['rms_threshold_dbfs'],POLICY['peak_threshold_dbfs'])
        require(metrics['decoded_samples_per_channel']==clock['samples_per_channel'],'Audio decode clock count differs')
        require(abs(metrics['decoded_duration']-audio_end)<=1/rate+1e-8,'Decoded audio duration differs from declared audio coverage')
        quiet=suggest(runs,protected,native,POLICY['retained_quiet_per_edge_seconds'],POLICY['minimum_quiet_seconds'],POLICY['minimum_cut_seconds'])
        measurements={'performed':True,'native_clock':clock,'metrics':metrics,'quiet_runs':runs,'candidates':quiet,'protected_caption_union':protected}
    cuts,cut_ledger=choose_cuts(quiet,groups,math.floor(duration*float(fps)+1e-8),fps)
    plan,ledger=plan_from_evidence(raw,duration,source['sha256'],transcript,tb['sha256'],groups,cuts,fps,font)
    # Prove every original cue's text survived. Ordering across overlapping ASR
    # windows can be interleaved, so compare identity-bound unit ownership too.
    original_refs=[tuple(r) for u in units for r in u['refs']]
    final_refs=[tuple(r) for g in groups for r in g['refs']]
    require(original_refs==final_refs,'ASR source ownership changed during grouping')
    require(sum(len(letters(g['text'])) for g in groups)==normalization['input_text_characters_without_spaces'],'ASR characters were lost or duplicated')
    verify_bindings(inputs)
    output.mkdir(parents=True,exist_ok=False)
    save(output/'edit-plan.json',plan)
    report={'schema':'atrass-transcript-draft-report-v1','status':'draft_plan_created_semantic_and_style_review_pending',
            'policy':POLICY,'inputs':inputs,'normalization':normalization,'measurement':measurements,'media_coverage':coverage,
            'cuts':cut_ledger,**ledger,'caption_count':len(groups),'all_ASR_text_retained':True,
            'editorial_review_candidates':candidates(transcript['segments']),
            'new_recording_semantic_decisions_inferred':False,'direct_listening_seconds':0,'release_ready':False}
    save(output/'draft-report.json',report)
    return {'plan':str(output/'edit-plan.json'),'plan_sha256':sha(output/'edit-plan.json'),
            'report':str(output/'draft-report.json'),'captions':len(groups),'takes':len(plan['takes']),
            'removed_frames':ledger['removed_frames'],'output_frames':ledger['output_frames'],
            'new_recording_semantic_decisions_inferred':False,'release_ready':False}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--job',required=True);ap.add_argument('--output-dir',required=True);ap.add_argument('--ffmpeg',required=True)
    ap.add_argument('--preserve-all-gaps',action='store_true')
    a=ap.parse_args()
    print(json.dumps(build(a.job,a.output_dir,a.ffmpeg,a.preserve_all_gaps),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
