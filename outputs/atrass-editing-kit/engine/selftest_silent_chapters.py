#!/usr/bin/env python3
import copy,unittest
from silence_chapter_readouts import transform

class SilentChapterTests(unittest.TestCase):
 def setUp(self):
  self.p={'fps':'60','width':1920,'height':1080,'duration':3.,'media':{'raw':{'role':'source_footage','sha256':'a'*64}},'clips':[{'id':'take','in':0.,'out':3.,'source':'raw','source_in':10.}], 'audio':[{'id':'voice','in':0.,'out':3.,'source':'raw','source_in':10.}], 'audio_master':{'sample_peak_ceiling_dbfs':-1.5},'layers':[]}
  for suffix,kind,text in [('gradient','image',None),('chapter-generated-grain','image',None),('chapter-title-box','rect',None),('chapter-heading','text','CHAPTER 5'),('chapter-title','text','애교의 3원칙')]:
   self.p['layers'].append({'id':'c-'+suffix,'kind':kind,'in':.6,'out':1.6,'text':text,'source_chapter_binding':{'event_id':'five','take_id':'take'}})
  self.p['layers'].append({'id':'readout','kind':'caption','in':.8,'out':1.2,'text':'5번 애교의 3원칙.','source_dialogue_binding':{'take_id':'take'}})
  self.p['layers'].append({'id':'body','kind':'caption','in':2.,'out':2.5,'text':'본문','source_dialogue_binding':{'take_id':'take'}})
  self.d=[{'caption_id':'readout','chapter_id':'five','expected_text':'5번 애교의 3원칙.','reason':'Explicit production heading removal for chapter card.','edge_fade_seconds':.02}]
 def run_transform(self):return transform(self.p,self.d,'a'*64)
 def reject(self):
  with self.assertRaises(ValueError):self.run_transform()
 def test_exact_partition_and_provenance(self):
  before=copy.deepcopy(self.p);out,records=self.run_transform();self.assertEqual(before,self.p)
  self.assertEqual([(x['in'],x['out'])for x in out['audio']],[(0.,.6),(1.6,3.)]);self.assertAlmostEqual(out['audio'][1]['source_in'],11.6);self.assertEqual(out['audio'][0]['fade_out'],.02);self.assertEqual(out['audio'][1]['fade_in'],.02)
  self.assertEqual(records[0]['original_audio'],before['audio'][0]);self.assertEqual(records[0]['removed_caption'],before['layers'][-2]);self.assertEqual(records[0]['silence_output_frames'],[36,96]);self.assertEqual(out['layers'][-1],before['layers'][-1])
  for k in ('clips','duration','audio_master','media'):self.assertEqual(before[k],out[k])
 def test_changed_caption(self):self.d[0]['expected_text']='다른 문장';self.reject()
 def test_wrong_number(self):self.p['layers'][3]['text']='CHAPTER 6';self.reject()
 def test_wrong_title(self):self.p['layers'][4]['text']='다른 제목';self.reject()
 def test_uncovered_readout(self):self.p['layers'][-2]['out']=1.7;self.reject()
 def test_protected_body_or_fade_overlap(self):self.p['layers'][-1]['in']=1.61;self.reject()
 def test_source_audio_clock(self):self.p['audio'][0]['source_in']=10.1;self.reject()
 def test_existing_fade(self):self.p['audio'][0]['fade_in']=.1;self.reject()
 def test_large_fade(self):self.d[0]['edge_fade_seconds']=.1;self.reject()
 def test_duplicate(self):self.d.append(copy.deepcopy(self.d[0]));self.reject()
 def test_unknown_field(self):self.d[0]['gain_db']=-120;self.reject()
 def test_prefix_removed_at_take_start(self):
  for e in self.p['layers'][:5]:e['in']=0.
  out,records=self.run_transform();self.assertEqual(len(out['audio']),1);self.assertEqual(out['audio'][0]['in'],1.6);self.assertEqual(records[0]['silence_output_frames'],[0,96])

if __name__=='__main__':unittest.main(verbosity=2)
