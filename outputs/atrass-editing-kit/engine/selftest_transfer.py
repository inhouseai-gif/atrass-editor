#!/usr/bin/env python3
"""Small positive/negative source identity and relocation fixtures; no real media edited."""
from pathlib import Path
import argparse, copy, hashlib, json, subprocess, sys, tempfile
from prepare_job import verify_asr_source
from relink_project import relink, digest, pairs


def run():
    results = []

    def check(name, test):
        test()
        results.append({'name': name, 'pass': True})

    def reject(fn):
        try:
            fn()
        except (ValueError, FileNotFoundError, FileExistsError):
            return
        raise AssertionError('Invalid fixture was accepted')

    with tempfile.TemporaryDirectory(prefix='atrass-transfer-') as temp:
        root = Path(temp).resolve()
        old = root / 'source.mov'; old.write_bytes(b'same source bytes')
        moved = root / 'moved.mov'; moved.write_bytes(old.read_bytes())
        wrong = root / 'wrong.mov'; wrong.write_bytes(b'different source bytes')
        h = digest(old)
        font = root / 'font.ttf'; font.write_bytes(b'fixture-font-existence-only')
        asr = {'input': str(old), 'source_sha256': h, 'segments': []}
        check('ASR relocated same SHA accepted', lambda: verify_asr_source(asr, moved, h))
        check('ASR same path no historical SHA accepted as legacy', lambda: verify_asr_source({'input': str(old)}, old, h))
        check('ASR moved without SHA rejected', lambda: reject(lambda: verify_asr_source({'input': str(old)}, moved, h)))
        check('ASR same path wrong SHA rejected', lambda: reject(lambda: verify_asr_source({**asr, 'source_sha256': '0' * 64}, old, h)))
        check('ASR malformed SHA rejected', lambda: reject(lambda: verify_asr_source({**asr, 'source_sha256': 'not-a-hash'}, old, h)))
        check('ASR source identity absent rejected', lambda: reject(lambda: verify_asr_source({}, moved, h)))
        p = {'schema': 'atrass-render-v1', 'release_ready': False,
             'media': {'rawA': {'path': str(old), 'role': 'source_footage'}},
             'fonts': {'body': {'path': 'font.ttf'}},
             'mapping_evidence': '/historical/research/map.json', 'clips': [], 'layers': []}
        project = root / 'project.json'; project.write_text(json.dumps(p), encoding='utf-8')
        target = root / 'relinked.json'

        def positive():
            before = digest(project)
            report = relink(project, target, {'rawA': str(moved)})
            actual = json.loads(target.read_text(encoding='utf-8')); expected = copy.deepcopy(p)
            expected['media']['rawA'].update(path=str(moved), sha256=h)
            assert actual == expected and digest(project) == before
            assert report['historical_evidence_paths_unchanged'] and not actual['release_ready']
        check('relink only media fields and preserves original/evidence/release', positive)
        check('existing output rejected', lambda: reject(lambda: relink(project, target, {'rawA': str(moved)})))
        check('different raw bytes rejected', lambda: reject(lambda: relink(project, root / 'bad-raw.json', {'rawA': str(wrong)})))
        check('conflicting expected hash rejected', lambda: reject(lambda: relink(project, root / 'conflict.json', {'rawA': str(moved)}, {'rawA': '0' * 64})))
        check('missing replacement rejected', lambda: reject(lambda: relink(project, root / 'missing.json', {'rawA': str(root / 'absent.mov')})))
        check('unknown media ID rejected', lambda: reject(lambda: relink(project, root / 'id.json', {'unknown': str(moved)})))
        check('non-sibling output rejected', lambda: reject(lambda: relink(project, root / 'elsewhere/out.json', {'rawA': str(moved)})))
        absent = copy.deepcopy(p); absent['media']['rawA']['path'] = str(root / 'old-machine.mov')
        project2 = root / 'absent-project.json'; project2.write_text(json.dumps(absent), encoding='utf-8')
        check('missing old file without digest rejected', lambda: reject(lambda: relink(project2, root / 'no-trust.json', {'rawA': str(moved)})))
        check('missing old file explicit trusted digest accepted', lambda: relink(project2, root / 'trusted.json', {'rawA': str(moved)}, {'rawA': h}))
        absent['schema'] = 'atrass-edit-plan-v1'; absent['media']['rawA']['sha256'] = h
        project2.write_text(json.dumps(absent), encoding='utf-8')
        check('edit plan stored source digest accepted', lambda: relink(project2, root / 'plan-new.json', {'rawA': str(moved)}))
        check('argument duplicate rejected', lambda: reject(lambda: pairs(['rawA=x', 'rawA=y'])))
        # Actual prepare_job CLI with a short native video and a stale path + valid hash.
        import av
        import numpy as np
        video = root / 'tiny.avi'
        with av.open(str(video), 'w') as container:
            stream = container.add_stream('mpeg4', rate=30); stream.width = 64; stream.height = 64; stream.pix_fmt = 'yuv420p'
            for i in range(3):
                frame = av.VideoFrame.from_ndarray(np.full((64, 64, 3), 50 + i, dtype=np.uint8), format='rgb24')
                for packet in stream.encode(frame): container.mux(packet)
            for packet in stream.encode(): container.mux(packet)
        transcript = {'input': '/another-machine/tiny.avi', 'source_sha256': digest(video),
                      'segments': [{'start': 0, 'end': .05, 'text': '테스트 문장'}]}
        transcript_path = root / 'transcript.json'; transcript_path.write_text(json.dumps(transcript), encoding='utf-8')
        command = [sys.executable, str(Path(__file__).with_name('prepare_job.py')), '--raw', str(video), '--variant', 'A', '--asr-json', str(transcript_path)]

        def prepare_positive():
            jobdir = root / 'job'
            run = subprocess.run(command + ['--output-dir', str(jobdir)], capture_output=True, text=True)
            assert run.returncode == 0, run.stderr
            job = json.loads((jobdir / 'job.json').read_text(encoding='utf-8'))
            assert job['asr_source_validation']['method'] == 'sha256_match'
            assert json.loads((jobdir / 'transcript.json').read_text(encoding='utf-8')) == transcript
            assert job['asr_text_verified'] is False
        check('prepare_job relocated-hash CLI creates unchanged transcript and unreviewed job', prepare_positive)

        def prepare_negative():
            transcript.pop('source_sha256'); transcript_path.write_text(json.dumps(transcript), encoding='utf-8')
            jobdir = root / 'rejected-job'
            run = subprocess.run(command + ['--output-dir', str(jobdir)], capture_output=True, text=True)
            assert run.returncode != 0 and not jobdir.exists()
            assert 'verified source_sha256 is required' in run.stderr
        check('prepare_job mismatched-path CLI rejects without partial output', prepare_negative)
    return {'schema': 'atrass-transfer-fixtures-v1', 'pass': True, 'tests': len(results), 'results': results,
            'scope': 'Tiny synthetic fixtures and CLI execution; no fresh dependency installation or other-platform execution.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path); args = parser.parse_args()
    result = run()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'pass': result['pass'], 'tests': result['tests']}, ensure_ascii=False))
