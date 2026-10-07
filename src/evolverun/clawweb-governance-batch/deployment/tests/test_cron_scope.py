import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT.parent)]
from clawweb_batch.core import rank_counts,select_watermark
from clawweb_batch.adapters.odps import PyODPSData
from clawweb_batch.pipeline import representative_tasks
from openclaw_analysis import OpenClawAnalysis

class CronScopeTests(unittest.TestCase):
    def test_user_bot_merges_categories_and_excludes_noncron(self):
        def r(owner,bot,category,n,lane=1):
            return dict(user_id=owner,bot_id=bot,task_complete_cate=category,weighted_cnt=n,raw_sampled_cnt=n,is_cron=lane)
        rows=[r('u','a','CONFIG_MISSING',60),r('u','a','TOOL_FAILURE',60),r('u','b','NEW_UNRELIABLE_LABEL',100),
              r('v','a','TOOL_FAILURE',10),r('u','c','TOOL_FAILURE',10000,0),r('u','d','COMPLETED',10000),r('u','e','UNKNOWN',10000)]
        ranked=rank_counts(rows,10,cron_only=True)
        self.assertEqual([(x['user_id'],x['bot_id'],x['weighted_cnt']) for x in ranked],[('u','a',120),('u','b',100),('v','a',10)])
    def test_cron_date_does_not_require_noncron(self):
        rows=[dict(dt='20261006',is_cron=1,raw_sampled_cnt=3)]
        self.assertEqual(select_watermark(rows,'20261007',3,cron_only=True)['end_date'],'20261006')
    def test_sql_groups_by_user_bot_not_category(self):
        d=PyODPSData(None,'example',cron_only=True);d._read=Mock(return_value=[])
        d.ranking('20261001','20261006');sql=d._read.call_args.args[0]
        self.assertIn('GROUP BY user_id,bot_id LIMIT',sql)
        self.assertIn('AND is_cron=1',sql)
        self.assertNotIn('CAPABILITY_BOUNDARY',sql)
        d.sessions('u','b','20261001','20261006',10)
        self.assertIn("j.sampling_group_key <> ''",d._read.call_args.args[0])
    def test_task_sampling_is_category_agnostic(self):
        tasks=[dict(id=str(i),is_cron=True,failure_class='MISCLASSIFIED',signature_ids=['s'],end_time='2026-10-06T10:00:00+08:00',is_complete=0) for i in range(3)]
        self.assertEqual(len(representative_tasks(tasks,'ALL_FAILURES',1)),3)

class EvidenceBudgetTests(unittest.TestCase):
    def analyst(self,tmp):
        return OpenClawAnalysis({},ROOT.parents[1]/'clawweb-skills/clawinsight-skills',Path(tmp),'/unused','/unused',time.monotonic()+1000,{})
    def test_large_insufficient_evidence_never_calls_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=self.analyst(tmp);a.analyze=Mock()
            result=a.explain({'tasks':[{'text':'x'*300000}],'warnings':[]},{'payload':{'improvementId':32,'outcome':'INSUFFICIENT_DATA'},'reason':'legacy item has no verified signature'})
            a.analyze.assert_not_called();self.assertEqual(result['outcome'],'INSUFFICIENT_DATA')
    def test_related_tasks_batch_and_unrelated_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=self.analyst(tmp)
            a.analyze=Mock(return_value={'improvementId':1,'outcome':'STILL_PRESENT','reason':'observed','gaps':[]})
            tasks=[{'id':str(i),'evidence':'x'*60000} for i in range(4)]+[{'id':'unrelated','evidence':'x'*300000}]
            result=a.explain({'tasks':tasks},{'payload':{'improvementId':1,'outcome':'STILL_PRESENT'},'reason':'recurrence','checked_task_ids':['0','1','2','3']})
            self.assertEqual(result['batch_count'],4)
            seen=[]
            for c in a.analyze.call_args_list:
                self.assertLess(len(json.dumps(c.args[1],ensure_ascii=False).encode()),150000)
                seen.extend(t['id'] for t in c.args[1]['evidence']['tasks'])
            self.assertEqual(seen,['0','1','2','3'])
            self.assertEqual(len(tasks),5)
    def test_oversize_single_task_discloses_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=self.analyst(tmp);a.analyze=Mock(return_value={'improvementId':1,'outcome':'STILL_PRESENT','reason':'limited','gaps':['raw omitted']})
            a.explain({'tasks':[{'id':'a','session_id':'s','evidence':'x'*300000}]},{'payload':{'improvementId':1,'outcome':'STILL_PRESENT'},'reason':'recurrence','checked_task_ids':['a']})
            t=a.analyze.call_args.args[1]['evidence']['tasks'][0]
            self.assertIn('not reviewed here',t['projection']);self.assertEqual(t['id'],'a')

class GateOwnershipTests(unittest.TestCase):
    def test_governance_adapter_only_validates_structure(self):
        from clawweb_batch.core import validate_analysis
        with tempfile.TemporaryDirectory() as tmp:
            a=OpenClawAnalysis({},ROOT.parents[1]/'clawweb-skills/clawinsight-skills',Path(tmp),'/unused','/unused',time.monotonic()+1000,{})
            proposal=dict(decision='CREATE',reason='investigate',signature_id='not-grounded',evidence_ids=['missing'],
                          title='x',root_cause='x',suggested_action='x',assignment_reason='x',existing_improvement_id=None)
            bundle={'existing_actions':[],'signatures':[],'tasks':[]}
            self.assertEqual(a._validate('clawweb-governance',proposal,bundle),proposal)
            with self.assertRaises(ValueError):validate_analysis(proposal,bundle)
            with self.assertRaises(ValueError):a._validate('clawweb-governance',{'decision':'CREATE'},bundle)
    def test_output_budget_is_forwarded(self):
        from openclaw_analysis import agent_config
        config=agent_config(Path('/work'),Path('/state'),{'model':'unit','base_url':'https://example.test'},max_tokens=12000)
        self.assertEqual(config['models']['providers']['ais']['models'][0]['maxTokens'],12000)
