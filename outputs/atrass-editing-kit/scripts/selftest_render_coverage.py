#!/usr/bin/env python3
"""Regression checks for evidence accounting; never edit media or source records."""
from copy import deepcopy
import json
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import audit_reconstruction_coverage as coverage


def main():
    kit=Path(__file__).resolve().parents[1]
    baseline=coverage.audit(kit)
    assert baseline['input_integrity']['snapshot_stable']
    scopes=baseline['render_execution']['execution_by_base_media']
    # Further real renders may extend coverage. The frozen known ranges must
    # still be present and separately classified; don't freeze the total forever.
    known_raw=[[1822,4238],[31081,31538],[32253,32977],[39317,39960]]
    def contains(ranges, expected):
        return all(any(a<=lo and hi<=b for a,b in ranges) for lo,hi in expected)
    assert contains(scopes['source_footage']['current_project_and_output_hash_bound_ranges'],known_raw)
    assert contains(scopes['procedural_graphics']['current_project_and_output_hash_bound_ranges'],[[5131,5339]])
    original_read=coverage.Inputs.read

    def wrong_fps(reader,name,required=True):
        value=original_read(reader,name,required)
        if str(name).endswith('A_챕터재현_시안.render.json'):
            value=deepcopy(value)
            value.update(fps=30,range=[.5,1.0],frame_count=15)
        return value

    with patch.object(coverage.Inputs,'read',wrong_fps):
        changed=coverage.audit(kit)
    records=changed['render_execution']['historical_full_range_execution_records']
    rejected=[r for r in records if str(r['source']).endswith('A_챕터재현_시안.render.json')]
    assert len(rejected)==1
    assert not rejected[0]['historical_render_execution_record']
    assert rejected[0]['base_media_classification']=='unclassified'
    assert 'output_fps_differs_from_project_or_is_invalid_native_mapping_required' in rejected[0]['record_errors']
    assert rejected[0]['source'] not in changed['render_execution']['execution_by_base_media']['procedural_graphics']['sources']

    def wrong_legacy_count(reader,name,required=True):
        value=original_read(reader,name,required)
        if name=='references/reconstruction-40s-validation.json':
            value=deepcopy(value)
            value['native_frame_count']+=1
        return value

    with patch.object(coverage.Inputs,'read',wrong_legacy_count):
        changed=coverage.audit(kit)
    records=changed['render_execution']['historical_full_range_execution_records']
    rejected=[r for r in records if r['source']=='references/reconstruction-40s-validation.json']
    assert len(rejected)==1 and rejected[0]['count_matches_range'] is False
    assert rejected[0]['historical_render_execution_record'] is False
    assert rejected[0]['source'] not in changed['render_execution']['execution_by_base_media']['source_footage']['sources']
    sidecar=(kit.parent/'reconstruction/A_챕터재현_시안.render.json').resolve()
    sidecar_data=json.loads(sidecar.read_text());project=Path(sidecar_data['project']).resolve()
    selected=coverage.audit(kit,snapshot_paths=[sidecar,project])
    selected_scopes=selected['render_execution']['execution_by_base_media']
    assert selected_scopes['procedural_graphics']['current_project_and_output_hash_bound_native_frame_count']==208
    assert selected_scopes['source_footage']['current_project_and_output_hash_bound_native_frame_count']==0
    assert selected['render_execution']['discovery_scope']['mode']=='explicit_snapshot_paths'
    assert len(selected['render_execution']['planned_projects'])==1
    assert not any(x['path'].startswith('projects/') and (kit/x['path']).resolve()!=project for x in selected['input_integrity']['files'])
    empty=coverage.audit(kit,snapshot_paths=[])
    assert empty['render_execution']['planned_projects']==[]
    assert empty['render_execution']['current_project_and_output_hash_bound_native_frame_count']==0
    for supplied,paths in [([sidecar],[]),([],[sidecar])]:
        try:coverage.audit(kit,render_records=supplied,snapshot_paths=paths)
        except ValueError:pass
        else:raise AssertionError('A sidecar without its selected project must be rejected')
    relative=coverage.audit(kit,snapshot_paths=[os.path.relpath(p,kit) for p in [sidecar,project]])
    assert relative['render_execution']['execution_by_base_media']==selected_scopes
    assert relative['render_execution']['discovery_scope']==selected['render_execution']['discovery_scope']
    with tempfile.TemporaryDirectory(prefix='atrass-coverage-selection-') as temporary:
        selection=Path(temporary)/'selection.json'
        original=(json.dumps([str(sidecar),str(project)])+'\n').encode()
        selection.write_bytes(original)
        argv=['audit','--kit-dir',str(kit),'--snapshot-file-list',str(selection),'--output',str(selection)]
        with patch.object(sys,'argv',argv),patch.object(coverage,'audit',return_value=deepcopy(selected)),contextlib.redirect_stderr(io.StringIO()):
            try:coverage.main()
            except SystemExit as error:assert error.code==2
            else:raise AssertionError('Selection input must not be overwritten by report output')
        assert selection.read_bytes()==original
    print(json.dumps({'status':'passed','regression_scenarios':9,'scope':'3 prior full-audit regressions plus selected snapshot isolation, empty snapshot, unselected explicit record rejection, missing selected project rejection, kit-relative API equivalence and CLI selection-file preservation. Source evidence was not mutated; one disposable temporary selection file was used.'}))


if __name__=='__main__':main()
