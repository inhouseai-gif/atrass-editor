#!/usr/bin/env python3
"""Exercise concurrency, crash/resume and source/sidecar tamper boundaries.
Use a new scratch directory; preserve every test file for inspection.
"""
from pathlib import Path
import argparse
import copy
import json
import subprocess
import sys

from run_longform import binding, completed_outputs, job_lock, project_bindings, stage
from review_movie import parse_loudness, save, sha, verify_sidecar


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', required=True)
    a = ap.parse_args()
    root = Path(a.output_dir).resolve(); root.mkdir(parents=True)
    checks = []

    def denied(name, fn):
        try:
            fn()
        except (ValueError, OSError):
            checks.append(name)
        else:
            raise AssertionError('Expected rejection: '+name)

    raw = root/'raw.mov'; raw.write_bytes(b'private original test bytes')
    frozen = [binding(raw)]
    calls = []

    def action(attempt):
        calls.append(attempt)
        out = attempt/'result.txt'; out.write_text('preserved output', encoding='utf-8')
        return {'state': 'done', 'release_ready': False}, [out]

    first = stage(root, 'once', frozen, action)
    again = stage(root, 'once', frozen, lambda _: (_ for _ in ()).throw(AssertionError('Must not rerun')))
    assert first == again and len(calls) == 1; checks.append('completed_stage_never_rerendered')
    inventory = completed_outputs(root, 'once')
    cache = calls[0]/'__pycache__'; cache.mkdir()
    (cache/'module.pyc').write_bytes(b'cache created during rendering')
    assert completed_outputs(root, 'once') == inventory
    checks.append('runtime_cache_does_not_change_closed_stage_inventory')
    raw.write_bytes(b'PRIVATE original test bytes')
    denied('source_change_blocks_resume', lambda: stage(root, 'once', frozen, action))
    raw.write_bytes(b'private original test bytes')
    output = calls[0]/'result.txt'; output.write_text('changed output!', encoding='utf-8')
    denied('completed_output_change_blocks_resume', lambda: stage(root, 'once', frozen, action))

    def interrupted(attempt):
        (attempt/'partial.mp4').write_bytes(b'partial output retained')
        raise ValueError('simulated interruption')

    denied('partial_stage_records_failure', lambda: stage(root, 'retry', frozen, interrupted))
    result = stage(root, 'retry', frozen, action)
    assert result == first and (root/'retry/attempt-001/partial.mp4').read_bytes() == b'partial output retained'
    assert (root/'retry/attempt-001/failed.json').exists() and (root/'retry/attempt-002/complete.json').exists()
    checks.append('retry_uses_new_attempt_preserves_partial_output')

    def drift(attempt):
        raw.write_bytes(b'changed while stage executes')
        return {}, []

    denied('mid_stage_input_change_rejects_completion', lambda: stage(root, 'drift', frozen, drift))
    assert not (root/'drift/attempt-001/complete.json').exists()
    raw.write_bytes(b'private original test bytes')
    with job_lock(root):
        script = 'import sys;sys.path.insert(0,sys.argv[1]);from run_longform import job_lock\nwith job_lock(sys.argv[2]): print("incorrect concurrent acquisition")'
        child = subprocess.run([sys.executable, '-c', script, str(Path(__file__).parent), str(root)], capture_output=True, text=True)
        assert child.returncode != 0 and 'already running this job' in child.stderr
        checks.append('concurrent_second_writer_rejected')
    with job_lock(root):
        checks.append('OS_lock_released_without_lock_file_deletion')
    assert (root/'.execution.lock').exists()

    project = root/'project.json'
    p = {'schema':'atrass-render-v1','variant':'A','width':1920,'height':1080,'fps':'30','duration':1.0,
         'media':{'raw':{'path':str(raw),'role':'source_footage','sha256':sha(raw)}},'fonts':{},
         'clips':[{'id':'c','in':0,'out':1,'source':'raw'}],'audio':[],'layers':[]}
    save(project,p)
    assert project_bindings(project,sha(raw)); checks.append('raw_identity_verified')
    denied('other_raw_identity_rejected',lambda:project_bindings(project,'0'*64))
    for name, mutate in [
        ('reference_media_rejected',lambda q:q['media']['raw'].update(role='reference')),
        ('unused_raw_declaration_rejected',lambda q:q.update(clips=[])),
        ('false_media_hash_rejected',lambda q:q['media']['raw'].update(sha256='f'*64)),
    ]:
        q=copy.deepcopy(p);mutate(q);project.write_text(json.dumps(q),encoding='utf-8')
        denied(name,lambda:project_bindings(project,sha(raw)))
    project.write_text(json.dumps(p),encoding='utf-8')
    movie=root/'closed.mp4';movie.write_bytes(b'closed bytes for contract test, not a real video')
    sidecar=root/'closed.render.json'
    receipt={'project_sha256':sha(project),'output_sha256':sha(movie),'range':[0.,1.], 'frame_count':30,'fps':'30','size':[1920,1080],'audio':{'present':False}}
    save(sidecar,receipt)
    for name, change in [
        ('foreign_project_sidecar_rejected',{'project_sha256':'0'*64}),
        ('partial_range_rejected',{'range':[0.,0.5]}),
        ('wrong_frame_count_rejected',{'frame_count':29}),
        ('wrong_dimensions_rejected',{'size':[1280,720]}),
        ('missing_audio_contract_rejected',{'audio':{'present':True}}),
    ]:
        sidecar.write_text(json.dumps({**receipt,**change}),encoding='utf-8')
        denied(name,lambda:verify_sidecar(project,movie,sidecar))
    sidecar.write_text(json.dumps(receipt),encoding='utf-8')
    movie.write_bytes(b'changed closed movie')
    denied('movie_tamper_rejected',lambda:verify_sidecar(project,movie,sidecar))
    loudness = {'input_i':'-14.78','input_tp':'-0.99','input_lra':'6.00','input_thresh':'-26.15'}
    assert parse_loudness('FFmpeg header\n'+json.dumps(loudness)+'\nFFmpeg muxer footer') == loudness
    checks.append('loudness_parser_accepts_real_ffmpeg_trailing_summary')
    for name, log in [('missing_loudness_rejected', 'no JSON'), ('incomplete_loudness_rejected', '{}'),
                      ('malformed_loudness_rejected', '{broken')]:
        denied(name,lambda:parse_loudness(log))
    result={'pass':True,'checks':checks,'count':len(checks), 'scope':'State/concurrency/identity failure contracts only; actual rendering is separately exercised', 'release_ready':False}
    save(root/'validation.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
