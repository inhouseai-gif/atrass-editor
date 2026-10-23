#!/usr/bin/env python3
"""Small semantic checks for source-map integrity and visual matching. No media needed."""
import contextlib,copy,importlib.util,io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('video_map',Path(__file__).with_name('video_map.py'));vm=importlib.util.module_from_spec(spec);spec.loader.exec_module(vm)
np=vm.np
class SourceMapTests(unittest.TestCase):
 def fixture(self):
  return {'clips':[{'id':'c1','output_in_seconds':0,'output_out_seconds':1/30,'source_in_seconds':1/60,'source_out_seconds':.05,'output_native_frame_indices':[0,1],'output_frame_pts':[0,17],'source_frame_indices':[1,2],'source_frame_pts':[320,640],'frame_match_rmse_8bit':[1.,1.],'frame_match_roi':['both','both'],'frame_match_ambiguity_seconds':[[1/60,1/60],[2/60,2/60]],'status':'render_candidate_visual_fit'}],'unmapped_intervals':[],'coverage':{'output_ranges':[[0,1/30]]},'frame_mapping_coverage':{'indexed_native_output_frames':2},'source_time_base':{'num':1,'den':19200},'reference_time_base':{'num':1,'den':1000},'source_last_pts':640,'source_native_frame_count':3}
 def check_map(self,d,passed):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'map.json';p.write_text(json.dumps(d));a=SimpleNamespace(map=str(p),output=str(Path(td)/'audit.json'),anchors=None,raw_cache=None,final_cache=None)
   with contextlib.redirect_stdout(io.StringIO()):
    if passed:vm.audit(a)
    else:
     with self.assertRaises(SystemExit):vm.audit(a)
   report=json.loads(Path(a.output).read_text());self.assertEqual(not report['errors'],passed);self.assertFalse(report['release_certified'])
 def test_valid_candidate_not_release(self):self.check_map(self.fixture(),True)
 def test_missing_array(self):
  d=self.fixture();del d['clips'][0]['source_frame_pts'];self.check_map(d,False)
 def test_source_bounds(self):
  d=self.fixture();d['clips'][0]['source_frame_indices'][1]=3;self.check_map(d,False)
 def test_pts_outside_interval(self):
  d=self.fixture();d['clips'][0]['output_frame_pts'][1]=40;self.check_map(d,False)
 def test_duplicate_output(self):
  d=self.fixture();d['clips'][0]['output_native_frame_indices'][1]=0;self.check_map(d,False)
 def test_coverage_endpoint(self):
  d=self.fixture();d['coverage']['output_ranges']=[[0,.1]];self.check_map(d,False)
 def test_count_inconsistent(self):
  d=self.fixture();d['frame_mapping_coverage']['indexed_native_output_frames']=3;self.check_map(d,False)
 def test_high_visual_error(self):
  d=self.fixture();d['clips'][0]['frame_match_rmse_8bit'][1]=20;self.check_map(d,False)
 def test_monotonic_render_violation(self):
  d=self.fixture();d['clips'][0]['render_source_frame_indices']=[2,1];self.check_map(d,False)
 def test_exact_visual_candidate(self):
  feat=np.stack([np.full(vm.DIM,k*20,np.uint8) for k in range(8)]);raw={'features':feat,'times':np.arange(8)/60,'pts':np.arange(8)*320};r=vm.match_query(raw,feat[4],np.arange(8));self.assertEqual(r['raw_cache_index'],4);self.assertEqual(r['rmse'],0);self.assertEqual(r['matched_roi'],'both')
 def test_occluded_hand_preserves_face_match(self):
  feat=np.stack([np.full(vm.DIM,k*20,np.uint8) for k in range(8)]);raw={'features':feat,'times':np.arange(8)/60,'pts':np.arange(8)*320};target=feat[4].copy();target[vm.SPLIT:]=255;r=vm.match_query(raw,target,np.arange(8));self.assertEqual(r['raw_cache_index'],4);self.assertEqual(r['matched_roi'],'face_only');self.assertGreater(r['hand_rmse_at_selected'],100)
if __name__=='__main__':unittest.main()
