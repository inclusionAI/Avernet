import json,tempfile,hashlib,unittest
from pathlib import Path
from agent_evaluation.synthesis.baseline_similarity import search,SimilarityError
class SimilarityTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)/'x';self.docs=[{'id':'1','question':'容器临时目录会丢失数据吗','tags':['甲']},{'id':'2','question':'容器临时目录会丢失数据吗','tags':['乙']},{'id':'3','question':'用户修改昵称','tags':['甲']}];self.write()
 def tearDown(self):self.tmp.cleanup()
 def write(self):self.p.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in self.docs));self.h=hashlib.sha256(self.p.read_bytes()).hexdigest()
 def test_identical_cross_category(self):
  r=search(self.p,self.h,'容器临时目录会丢失数据吗');self.assertEqual([x['id'] for x in r['results'][:2]],['1','2']);self.assertEqual(r['records_scanned'],3)
 def test_bad_hash(self):
  with self.assertRaisesRegex(SimilarityError,'BASELINE_HASH_MISMATCH'):search(self.p,'0'*64,'题目')
 def test_duplicate_id(self):
  self.docs[1]['id']='1';self.write()
  with self.assertRaisesRegex(SimilarityError,'DUPLICATE_ID'):search(self.p,self.h,'题目')
 def test_empty_query(self):
  with self.assertRaisesRegex(SimilarityError,'INVALID_QUERY'):search(self.p,self.h,'  ')
 def test_limit(self):self.assertEqual(len(search(self.p,self.h,'容器临时目录',1)['results']),1)
if __name__=='__main__':unittest.main()
