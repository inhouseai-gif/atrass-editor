#!/usr/bin/env python3
"""Panel-shadow behavioral checks, including containment and input preservation."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
import create_panel_shadow as shadow
from render import Composer

class PanelTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  Image.new('RGB',(1280,720),'#D4D0C4').save(self.root/'base.png')
  self.parent={'id':'panel','kind':'rect','in':.4,'out':2.6,'z':10,'position':[140,180],'size':[500,300],'radius':26,'fill':'#B9CDE0'}
  self.p={'schema':'atrass-render-v1','width':1280,'height':720,'fps':30,'duration':3,'release_ready':False,'media':{'base':{'path':'base.png','type':'image','role':'procedurally_created_graphic_asset'}},'fonts':{},'clips':[{'id':'base','source':'base','in':0,'out':3,'source_in':0}], 'layers':[self.parent],'audio':[]}
  self.path=self.root/'in.json';self.output=self.root/'out.json';self.assets=self.root/'shadows';self.save()
 def tearDown(self):self.tmp.cleanup()
 def save(self):self.path.write_text(json.dumps(self.p))
 def build(self,**kw):return shadow.build(self.path,['panel'],self.output,self.assets,**kw)
 def create(self,**kw):return shadow.create(self.path,['panel'],self.output,self.assets,**kw)
 def test_parent_pixels_and_other_content_unchanged(self):
  original=self.path.read_bytes();self.create();p=json.loads(self.output.read_text());self.assertEqual(p['layers'][1],self.parent);self.assertEqual(self.path.read_bytes(),original)
  base=np.full((720,1280,3),210,np.uint8);old=Composer(self.p,self.root).compose(base,self.p['layers'],1);new=Composer(p,self.root).compose(base,p['layers'],1)
  self.assertTrue(np.array_equal(old[220:440,180:600],new[220:440,180:600]));self.assertTrue(np.array_equal(old[0:100],new[0:100]));self.assertTrue(np.any(new[475:500,240:500]<old[475:500,240:500]))
 def test_padding_is_not_clipped(self):
  self.create();a=np.array(Image.open(self.assets/'panel-shadow-001.png'))[:,:,3];self.assertFalse(a[0].any()or a[-1].any()or a[:,0].any()or a[:,-1].any());self.assertTrue(json.loads((self.assets/'provenance.json').read_text())['layer_records'][0]['expanded_padding_identical'])
 def test_new_dimensions_regenerate_a_different_shape(self):
  _,a,_=self.build();self.parent.update(size=[690,420],radius=42);self.save();p,b,_=self.build();self.assertNotEqual(a[0][1],b[0][1]);self.assertEqual(p['layers'][1]['size'],[690,420])
 def test_temporal_properties_preserved(self):
  self.parent.update(opacity=.8,opacity_keys=[[0,0],[.4,1],[2.0,.3]],fade_in=.2,fade_out=.2);self.save();p,_,_=self.build()
  for key in ('in','out','opacity','opacity_keys','fade_in','fade_out'):self.assertEqual(p['layers'][0][key],self.parent[key])
 def test_no_shadow_before_or_after_parent(self):
  self.create();p=json.loads(self.output.read_text());c=Composer(p,self.root);base=np.full((720,1280,3),210,np.uint8)
  for t in (0,.39,2.6,2.9):self.assertTrue(np.array_equal(c.compose(base,[e for e in p['layers']if e['in']<=t<e['out']],t),base))
 def test_invalid_geometry_and_transparency_rejected(self):
  for change in ({'rotation':2},{'scale_keys':[[0,1]]},{'anchor':'center'},{'size':[1.5,100]},{'radius':300},{'stroke':'red'},{'fill':'#B9CDE080'},{'box':[0,0,1,1]},{'kind':'text'}):
   with self.subTest(change=change):
    self.p['layers']=[{**self.parent,**change}];self.save()
    with self.assertRaises((ValueError,KeyError)):self.build()
    self.assertFalse(self.output.exists());self.assertFalse(self.assets.exists())
 def test_invalid_opacity_and_motion_rejected(self):
  for change in ({'opacity':float('nan')},{'opacity':1.1},{'fade_in':-1},{'opacity_keys':[[0,1],[0,.5]]},{'opacity_keys':[[0,1],[1,.5,'unknown']]}):
   with self.subTest(change=change):
    self.p['layers']=[{**self.parent,**change}];self.save()
    with self.assertRaises(ValueError):self.build()
 def test_visible_crop_rejected(self):
  self.parent['position']=[0,0];self.save()
  with self.assertRaises(ValueError):self.build()
 def test_existing_paths_and_id_collisions_preserved(self):
  self.output.write_text('unchanged')
  with self.assertRaises(FileExistsError):self.create()
  self.assertEqual(self.output.read_text(),'unchanged');self.output.unlink()
  self.p['media']['panel-shadow-001']=copy.deepcopy(self.p['media']['base']);self.save()
  with self.assertRaises(ValueError):self.build()
 def test_publication_rechecks_source(self):
  original=shadow.build
  def mutate(*args,**kwargs):
   result=original(*args,**kwargs);self.path.write_text(self.path.read_text()+' ');return result
  with patch.object(shadow,'build',side_effect=mutate):
   with self.assertRaises(ValueError):self.create()
  self.assertFalse(self.output.exists());self.assertFalse(self.assets.exists())
 def test_parameter_bounds_and_duplicate_selection(self):
  for kw in ({'sigma':0},{'sigma':float('inf')},{'opacity':0},{'dx':.5},{'dy':300}):
   with self.subTest(kw=kw):
    with self.assertRaises(ValueError):self.build(**kw)
  with self.assertRaises(ValueError):shadow.build(self.path,['panel','panel'],self.output,self.assets)
 def test_source_clip_audio_media_preserved(self):
  p,_,_=self.build()
  for group in ('clips','audio','fonts'):self.assertEqual(p[group],self.p[group])
  self.assertEqual(p['media']['base'],self.p['media']['base'])

if __name__=='__main__':unittest.main()
