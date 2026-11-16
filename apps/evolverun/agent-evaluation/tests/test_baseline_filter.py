import hashlib,json,tempfile,unittest
from pathlib import Path
from agent_evaluation.synthesis.baseline_filter import run,FilterError
class FilterTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'x.jsonl';self.rows=[{'id':'1','question':'容器临时目录无法保存','tags':['业务-存储']},{'id':'2','question':'NAS跨容器状态','tags':['旧-持久']},{'id':'3','question':'别的课题','tags':['其他-标签']}];self.write()
 def tearDown(self):self.tmp.cleanup()
 def write(self):
  self.path.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in self.rows));self.h=hashlib.sha256(self.path.read_bytes()).hexdigest()
 def query(self,**kwargs):return run(self.path,self.h,{'target_tags':['业务-存储'],**kwargs})
 def test_exact(self):self.assertEqual(self.query()['matched_count'],1)
 def test_alias_opt_in(self):self.assertEqual(self.query(tag_aliases={'业务-存储':['旧-持久']})['matched_count'],2)
 def test_exclude_topic(self):self.assertEqual(self.query(exclude_question_terms=['临时目录'])['matched_count'],0)
 def test_exclude_tag(self):self.assertEqual(self.query(exclude_tags=['业务-存储'])['matched_count'],0)
 def test_terms(self):self.assertEqual(self.query(query_terms_any=['NAS'])['matched_count'],0)
 def test_hash_drift(self):
  with self.assertRaisesRegex(FilterError,'BASELINE_HASH_MISMATCH'):run(self.path,'0'*64,{'target_tags':['业务-存储']})
 def test_duplicate_id(self):
  self.rows[1]['id']='1';self.write()
  with self.assertRaisesRegex(FilterError,'DUPLICATE_ID'):self.query()
 def test_truncation(self):
  x=self.query(tag_aliases={'业务-存储':['旧-持久']},limit=1);self.assertEqual(x['matched_count'],2);self.assertTrue(x['truncated'])
 def test_alias_cannot_expand_other_targets(self):
  with self.assertRaisesRegex(FilterError,'INVALID_ALIASES'):self.query(tag_aliases={'其他':['x']})
 def test_unknown_field_fails(self):
  with self.assertRaisesRegex(FilterError,'UNKNOWN_REQUEST_FIELD'):self.query(magic_filter='x')
 def test_empty_exclusion_fails(self):
  with self.assertRaisesRegex(FilterError,'INVALID_EXCLUDE_TERMS'):self.query(exclude_question_terms=[' '])
if __name__=='__main__':unittest.main()
