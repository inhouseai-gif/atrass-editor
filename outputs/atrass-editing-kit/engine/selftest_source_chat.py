"""Behavior tests for source caption identity, cut-spanning accumulation and bounds."""
from pathlib import Path
import copy, json, tempfile, unittest
import apply_source_chat as chat
from apply_source_assets import sha


class SourceChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        raw=self.root/'raw.bin';raw.write_bytes(b'synthetic identity fixture; no decoded user media')
        self.p={'variant':'A','width':1920,'height':1080,'fps':'60000/1001','duration':2.002,'release_ready':False,
                'media':{'raw':{'path':str(raw),'role':'source_footage','sha256':sha(raw)}},
                'fonts':{'body':{'path':str(Path(chat.__file__).resolve().parents[1]/'assets/fonts/SUIT-Variable.ttf')}},
                'clips':[],'audio':[],'layers':[]}
        for i,start in enumerate([10,13]):
            a=i*1.001;b=(i+1)*1.001
            self.p['clips'].append({'id':f'take{i}','source':'raw','source_in':start,'in':a,'out':b,'rate':1})
            self.p['audio'].append({'id':f'voice{i}','source':'raw','source_in':start,'in':a,'out':b,'rate':1})
            self.p['layers'].append({'id':f'cap{i}','kind':'caption','in':a+.1001,'out':a+.5005,'text':['새 문장도 원본에 연결됩니다','먼저 나온 말풍선을 남겨 주세요'][i],'font':'body','font_size':40,'position':[100,950],'z':10,
                                     'source_dialogue_binding':{'take_id':f'take{i}','source_range':[start+.1001,start+.5005]}})
        self.project=self.root/'base.json';self.events=self.root/'events.json';self.output=self.root/'chat.json'
        self.spec={'schema':chat.SCHEMA,'source_sha256':sha(raw),'project_sha256':'','panels':[{'id':'q','sender':'남자친구','source_range':[10.1001,13.5005],'take_ids':['take0','take1'],'messages':[{'id':'one','side':'right','caption_ids':['cap0']},{'id':'two','side':'left','caption_ids':['cap1']}]}]}
        self.save()
    def tearDown(self):self.temp.cleanup()
    def save(self):
        self.project.write_text(json.dumps(self.p,ensure_ascii=False));self.spec['project_sha256']=sha(self.project);self.events.write_text(json.dumps(self.spec,ensure_ascii=False))
    def build(self):return chat.build(self.project,self.events,self.output)
    def test_source_cut_spanning_accumulation(self):
        p=self.build();states=p['source_chat_application']['panels'][0]['states']
        self.assertEqual([s['output_frames']for s in states],[[6,60],[60,66],[66,90]])
        self.assertEqual([s['visible_message_ids']for s in states],[['one'],['one'],['one','two']])
        for key in ('clips','audio','duration','audio_master'):
            self.assertEqual(p.get(key),self.p.get(key))
        self.assertEqual(p['layers'][:2],self.p['layers'])
        self.assertTrue(all(e['kind']!='chat'for e in p['layers']))
        self.assertEqual({e['fill']for e in p['layers']if e['kind']=='rect'},{'#B9CDE0','#FFFFFF','#FDE400'})
    def test_new_long_unspaced_text_wraps_without_loss(self):
        self.p['layers'][0]['text']='사과나무아래새로운문장을길게넣어도글자가사라지지않는지확인합니다'*2;self.save()
        m=self.build()['source_chat_application']['panels'][0]['messages'][0]
        self.assertIn('\n',m['display_text']);self.assertEqual(m['display_text'].replace('\n',''),m['text']);self.assertLessEqual(m['text_size'][0],440)
    def test_missing_caption_rejected(self):
        self.spec['panels'][0]['messages'][0]['caption_ids']=['missing'];self.save()
        with self.assertRaises(ValueError):self.build()
    def test_reused_caption_rejected(self):
        self.spec['panels'][0]['messages'][1]['caption_ids']=['cap0'];self.save()
        with self.assertRaises(ValueError):self.build()
    def test_wrong_source_identity_rejected(self):
        self.spec['source_sha256']='0'*64;self.save()
        with self.assertRaises(ValueError):self.build()
    def test_unknown_audio_command_rejected(self):
        self.spec['panels'][0]['gain_db']=-10;self.save()
        with self.assertRaises(ValueError):self.build()
    def test_overlapping_graphic_rejected(self):
        self.p['layers'].append({'id':'blocking','kind':'rect','in':.1,'out':.9,'position':[0,0],'size':[100,100],'fill':'#FFFFFF','z':1});self.save()
        with self.assertRaises(ValueError):self.build()
    def test_backwards_message_onsets_rejected(self):
        self.spec['panels'][0]['messages'].reverse();self.save()
        with self.assertRaises(ValueError):self.build()
    def test_content_overflow_rejected(self):
        self.p['layers'][0]['text']='가'*600;self.save()
        with self.assertRaises(ValueError):self.build()
    def test_create_only_publication(self):
        chat.apply_chat(self.project,self.events,self.output);before=self.output.read_bytes()
        with self.assertRaises(ValueError):chat.apply_chat(self.project,self.events,self.output)
        self.assertEqual(self.output.read_bytes(),before)
    def test_cannot_publish_elsewhere(self):
        folder=self.root/'other';folder.mkdir()
        with self.assertRaises(ValueError):chat.apply_chat(self.project,self.events,folder/'new.json')


if __name__=='__main__':unittest.main()
