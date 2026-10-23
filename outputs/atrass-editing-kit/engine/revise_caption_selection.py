#!/usr/bin/env python3
"""Revise explicit source-word selections before a separately reviewed retake cut.

No repeated-word detector or inferred deletion. Exact old selections and hashes
are required. A bounded ASR timing correction needs its own evidence; it does
not certify a phoneme boundary. Original decisions and transcript are preserved.
"""
from pathlib import Path
import argparse, copy, json, os
import assemble_edit_plan as assembly

SCHEMA = 'atrass-caption-selection-revision-v1'
require = assembly.require


def exact(row, fields, label):
    require(isinstance(row, dict) and set(row) == set(fields), 'Unknown/missing ' + label + ' fields')


def transform(decisions, transcript, spec, decisions_sha, transcript_sha, source_sha, duration):
    exact(spec, ('schema','input_decisions_sha256','transcript_sha256','source_sha256','edits'), 'revision')
    require(spec['schema'] == SCHEMA, 'Revision schema')
    require(spec['input_decisions_sha256'] == decisions_sha and spec['transcript_sha256'] == transcript_sha
            and spec['source_sha256'] == source_sha == transcript['source_sha256'], 'Revision input identity')
    require(decisions['schema'] == 'atrass-source-dialogue-v1' and decisions['transcript_sha256'] == transcript_sha,
            'Dialogue/transcript identity')
    edits = spec['edits']
    require(isinstance(edits, list) and edits, 'Explicit nonempty edits required')
    q = copy.deepcopy(decisions); by = {c['id']: c for c in q['captions']}; seen = set(); records = []
    for edit in edits:
        exact(edit, ('id','expected_text','expected_source_range','word_range','source_range','reason','evidence'), 'caption edit')
        cid = edit['id']; require(cid in by and cid not in seen, 'Unknown/duplicate caption'); seen.add(cid)
        require(assembly.nonempty(edit['reason']) and assembly.nonempty(edit['evidence']), 'Explicit reason/evidence required')
        c = by[cid]; before = copy.deepcopy(c)
        a, z, old, _, _, index, segment = assembly.selected_text(c, transcript['segments'], duration, transcript_sha)
        require(old == edit['expected_text'] and [a, z] == edit['expected_source_range'], 'Old selected text/range differs')
        require('source_range' not in c and 'text' not in c, 'Existing explicit wording/range needs a separate revision')
        words = segment.get('words'); require(isinstance(words, list) and words, 'Word timestamps required')
        left, right = c.get('word_range', [0, len(words)])
        span = edit['word_range']
        require(isinstance(span, list) and len(span) == 2 and all(type(i) is int for i in span)
                and left <= span[0] < span[1] <= right, 'Retained words must be one nonempty subset of old selection')
        chosen = words[span[0]:span[1]]
        text = ''.join(w['word'] for w in chosen).strip()
        wa, wz = chosen[0]['start'], chosen[-1]['end']
        c['word_range'] = span
        c['evidence'] = edit['reason'] + ' ' + edit['evidence']
        if edit['source_range'] is not None:
            times = edit['source_range']; require(isinstance(times, list) and len(times) == 2, 'Corrected range pair')
            lo, hi = times; assembly.interval(lo, hi, duration, 'Corrected range')
            require(a <= lo < hi <= z, 'Corrected time cannot expand old selection')
            require(abs(lo-wa) <= .300000001 and abs(hi-wz) <= .300000001,
                    'ASR boundary adjustment exceeds 300ms; use separate editorial review')
            require(lo < chosen[0]['end'] and hi > chosen[-1]['start'], 'Correction omits a complete retained word')
            c.pop('word_range'); c.update(source_range=times, text=text)
        else:
            lo, hi = wa, wz
        require(c != before and (span != [left, right] or [lo, hi] != [a, z]), 'No actual selection change')
        assembly.selected_text(c, transcript['segments'], duration, transcript_sha)
        records.append({'id': cid, 'before': before, 'after': copy.deepcopy(c), 'old_text': old,
                        'retained_text': text, 'retained_word_range': span, 'retained_asr_range': [wa,wz],
                        'render_source_range': [lo,hi], 'segment_index': index,
                        'removed_word_indices': list(range(left,span[0])) + list(range(span[1],right)),
                        'timing_correction_seconds': [lo-wa,hi-wz]})
    require(q['takes'] == decisions['takes'] and q.get('visual_events') == decisions.get('visual_events'),
            'This stage must not change footage, audio or graphics')
    return q, records


def apply(job_path, decisions_path, selection_path, output_dir):
    job_path, decisions_path, selection_path = [Path(p).resolve() for p in (job_path,decisions_path,selection_path)]
    out = Path(output_dir).absolute(); require(not os.path.lexists(out), 'Choose a new output directory')
    job, jh = assembly.read_bound(job_path); d, dh = assembly.read_bound(decisions_path); s, sh = assembly.read_bound(selection_path)
    tp = (job_path.parent/job['files']['transcript']).resolve(); t, th = assembly.read_bound(tp)
    require(d['job_sha256'] == jh and t['source_sha256'] == job['raw']['sha256'], 'Prepared job identity')
    q, records = transform(d,t,s,dh,th,job['raw']['sha256'],job['raw']['duration_seconds'])
    require(assembly.digest(job_path) == jh and assembly.digest(decisions_path) == dh
            and assembly.digest(selection_path) == sh and assembly.digest(tp) == th, 'Input changed during revision')
    out.mkdir(parents=True, exist_ok=False)
    def write(name, data):
        with (out/name).open('x', encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False); f.write('\n')
    write('decisions.json', q)
    receipt = {'schema': SCHEMA, 'input_decisions_sha256': dh, 'output_decisions_sha256': assembly.digest(out/'decisions.json'),
               'job_sha256': jh, 'transcript_sha256': th, 'source_sha256': job['raw']['sha256'],
               'selection_sha256': sh, 'implementation_sha256': assembly.digest(Path(__file__)), 'edits': records,
               'footage_audio_graphics_unchanged': True, 'automatic_semantic_approval': False,
               'direct_listening_seconds': 0, 'release_ready': False}
    write('selection-revision.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('job','decisions','selection','output-dir'): parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    result = apply(args.job,args.decisions,args.selection,args.output_dir)
    print(json.dumps({'edits': len(result['edits']), 'release_ready': False}))
