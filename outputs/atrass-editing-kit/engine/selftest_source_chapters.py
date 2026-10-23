"""Behavioral source chapter tests: preserve speech, fail closed and retain editable parts."""
from pathlib import Path
from fractions import Fraction
import argparse,copy,json,tempfile,unittest
from unittest.mock import patch
from PIL import Image
import apply_source_chapters as m
from render import Composer

RAW=None
class SourceChapterTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='atrass-source-chapter-test-');self.root=Path(self.temp.name).resolve();self.p=self.root/'base.json';self.e=self.root/'events.json';self.o=self.root/'new.json'
  half=float(Fraction(30*1001,60000));self.half=half
  self.base={'schema':'atrass-render-v1','variant':'A','width':1920,'height':1080,'fps':'60000/1001','duration':half*2,'release_ready':False,'media':{'raw':{'path':str(RAW),'role':'source_footage','sha256':m.shared.sha(RAW)}},'fonts':{},'clips':[{'id':'a','in':0.,'out':half,'source':'raw','source_in':0.,'rate':1},{'id':'b','in':half,'out':2*half,'source':'raw','source_in':1.,'rate':1}],'audio':[{'id':'av','in':0.,'out':half,'source':'raw','source_in':0.,'rate':1},{'id':'bv','in':half,'out':2*half,'source':'raw','source_in':1.,'rate':1}],'audio_master':{'sample_peak_ceiling_dbfs':-1.5},'layers':[]}
  self.p.write_text(json.dumps(self.base));self.spec={'schema':m.SCHEMA,'project_sha256':m.shared.sha(self.p),'source_sha256':self.base['media']['raw']['sha256'],'events':[{'id':'new-heading','title':'마음이 닿는 순간','number':3,'source_range':[.2,1.3],'take_ids':['a','b']}]};self.e.write_text(json.dumps(self.spec))
 def tearDown(self):self.temp.cleanup()
 def save(self):self.e.write_text(json.dumps(self.spec))
 def build(self):return m.build(self.p,self.e,self.o)
 def test_independent_parts_and_source_frame_mapping(self):
  p=self.build();self.assertEqual(len(p['layers']),10);self.assertEqual([e['kind']for e in p['layers'][:5]],['image','image','rect','text','text']);self.assertEqual(p['source_chapter_application']['events'][0]['fragments'][0]['output_frames'],[12,30]);self.assertEqual(p['source_chapter_application']['events'][0]['fragments'][1]['output_frames'],[30,48])
  for k in ('clips','audio','audio_master','duration'):self.assertEqual(p[k],self.base[k])
  for e in p['layers']:self.assertEqual(e['source_chapter_binding']['events_sha256'],m.shared.sha(self.e))
 def test_title_glyphs_and_template_geometry(self):
  p=self.build();comp=Composer(p,self.root)
  for e in p['layers']:
   im,xy=comp.image(e,e['in']);self.assertTrue(im.getbbox());self.assertGreaterEqual(xy[0],0);self.assertGreaterEqual(xy[1],0);self.assertLessEqual(xy[0]+im.width,1920);self.assertLessEqual(xy[1]+im.height,1080)
  title=[e for e in p['layers']if e.get('text')=='마음이 닿는 순간'][0];self.assertNotIn('glyph_width',title);self.assertEqual(title['glyph_height'],76)
 def test_unknown_or_mutating_fields_rejected(self):
  for field in ('audio','silence','duration','fade_out','unknown'):
   self.spec['events'][0][field]=1;self.save()
   with self.assertRaises(ValueError):self.build()
   del self.spec['events'][0][field]
 def test_bad_sha_or_variant_rejected(self):
  self.spec['project_sha256']='0'*64;self.save()
  with self.assertRaises(ValueError):self.build()
  self.spec['project_sha256']=m.shared.sha(self.p);self.spec['source_sha256']='0'*64;self.save()
  with self.assertRaises(ValueError):self.build()
 def test_duplicate_and_nonintersecting_takes_rejected(self):
  for ids in (['a','a'],['unknown'],[]):
   self.spec['events'][0]['take_ids']=ids;self.save()
   with self.assertRaises(ValueError):self.build()
  self.spec['events'][0]['take_ids']=['b'];self.spec['events'][0]['source_range']=[.1,.4];self.save()
  with self.assertRaises(ValueError):self.build()
 def test_overlap_chapters_rejected(self):
  e=copy.deepcopy(self.spec['events'][0]);e['id']='second';self.spec['events'].append(e);self.save()
  with self.assertRaises(ValueError):self.build()
 def test_existing_graphic_overlap_rejected(self):
  self.base['layers']=[{'id':'existing','kind':'rect','in':0.,'out':self.half,'z':5,'position':[0,0],'size':[100,100],'fill':'#FFFFFF'}];self.p.write_text(json.dumps(self.base));self.spec['project_sha256']=m.shared.sha(self.p);self.save()
  with self.assertRaises(ValueError):self.build()
 def test_long_new_title_wrap_not_forced(self):
  self.spec['events'][0]['title']='말보다 행동으로 전해지는 마음을 천천히 이해하는 방법';self.save();p=self.build();title=[e for e in p['layers']if e.get('text')==self.spec['events'][0]['title']][0];self.assertLess(title['glyph_height'],76);self.assertGreaterEqual(title['glyph_height'],48);self.assertNotIn('glyph_width',title)
 def test_create_only_and_input_unchanged(self):
  before=m.shared.sha(self.p);m.apply_chapters(self.p,self.e,self.o);actual=m.shared.sha(self.o)
  with self.assertRaises(ValueError):m.apply_chapters(self.p,self.e,self.o)
  self.assertEqual(m.shared.sha(self.p),before);self.assertEqual(m.shared.sha(self.o),actual)
 def test_dangling_symlink_and_other_folder_rejected(self):
  self.o.symlink_to(self.root/'missing')
  with self.assertRaises(ValueError):self.build()
  with self.assertRaises(ValueError):m.build(self.p,self.e,self.root/'child'/'new.json')
 def test_racing_writer_preserved(self):
  real=m.os.link
  def race(src,dst):Path(dst).write_text('other writer');return real(src,dst)
  with patch.object(m.os,'link',side_effect=race),self.assertRaises(FileExistsError):m.apply_chapters(self.p,self.e,self.o)
  self.assertEqual(self.o.read_text(),'other writer');self.assertFalse(list(self.root.glob('.source-chapters-*')))
 def test_incremental_nonoverlapping_chapter_keeps_history(self):
  self.spec['events'][0]['source_range']=[.1,.3];self.spec['events'][0]['take_ids']=['a'];self.save();m.apply_chapters(self.p,self.e,self.o)
  first=json.loads(self.o.read_text());self.spec['project_sha256']=m.shared.sha(self.o);self.spec['events'][0].update({'id':'later','source_range':[1.1,1.3],'take_ids':['b']});self.save()
  later=self.root/'later.json';m.apply_chapters(self.o,self.e,later);p=json.loads(later.read_text())
  self.assertEqual(p['layers'][:5],first['layers']);self.assertEqual(len(p['layers']),10);self.assertEqual(p['source_chapter_application_history'],[first['source_chapter_application']])
  for key in ('clips','audio','duration'):self.assertEqual(p[key],first[key])
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--synthetic-source',required=True);args,rest=ap.parse_known_args();RAW=Path(args.synthetic_source).resolve();unittest.main(argv=[__file__,*rest])
