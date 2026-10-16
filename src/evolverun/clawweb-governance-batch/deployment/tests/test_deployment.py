import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT.parent)]
import readiness
import openclaw_analysis as oa
spec=importlib.util.spec_from_file_location('daily_run',ROOT/'run.py')
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
from clawweb_batch.config import Config


class ReadinessTests(unittest.TestCase):
    def test_only_valid_business_dates(self):
        for value in ('GC','20260230','2026102','2026-10-02',None):
            self.assertFalse(readiness.valid_day(value))
        self.assertTrue(readiness.valid_day('20261001'))

    def test_requires_all_three_tables_and_excludes_today(self):
        data=readiness.ReadyData(None,'example','20261001')
        base=[{'dt':'20260930','is_cron':0},{'dt':'20261001','is_cron':1},{'dt':'GC','is_cron':0}]
        with patch.object(readiness.PyODPSData,'daily_counts',return_value=base) as read_base:
            data._read=Mock(side_effect=[
                [{'dt':'20260930','row_count':3},{'dt':'20261001','row_count':4}],
                [{'dt':'20260930','row_count':2},{'dt':'20261001','row_count':0}]
            ])
            self.assertEqual(data.daily_counts('20260920','20261002'),[base[0]])
            read_base.assert_called_once_with('20260920','20261001')
            self.assertEqual(data.readiness['category_dates_missing_other_inputs'],['20261001'])
            data.daily_counts('20260920','20261002')
            self.assertEqual(data._read.call_count,2)

    def test_table_identifiers_are_fixed(self):
        with self.assertRaises(ValueError):
            readiness.ReadyData(None,'x;DELETE table','20261001')


class JournalTests(unittest.TestCase):
    def test_confirmed_writes_are_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            center=Mock();center.write.return_value={'improvementId':9}
            journal=readiness.JournalCenter(center,tmp)
            first=journal.write('/fixed',{'a':1},'key-1')
            self.assertEqual(first,journal.write('/fixed',{'a':1},'key-1'))
            center.write.assert_called_once()
            with self.assertRaises(ValueError):journal.write('/fixed',{'a':2},'key-1')

    def test_ambiguous_write_is_blocked_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            center=Mock();center.write.side_effect=TimeoutError()
            journal=readiness.JournalCenter(center,tmp)
            with self.assertRaises(TimeoutError):journal.write('/fixed',{'a':1},'key-1')
            with self.assertRaisesRegex(ValueError,'uncertain'):journal.write('/fixed',{'a':1},'key-1')
            center.write.assert_called_once()

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                readiness.JournalCenter(Mock(),tmp).write('/x',{},'../../escape')


class OpenClawTests(unittest.TestCase):
    def test_model_config_contains_no_secret_and_no_tools(self):
        c=oa.agent_config(Path('/work'),Path('/state'),{'model':'test','base_url':'https://example.test','api_key':'unit-secret'})
        self.assertNotIn('unit-secret',json.dumps(c))
        self.assertEqual(c['tools'],{'deny':['*']})
        self.assertEqual(c['models']['providers']['ais']['apiKey']['source'],'env')

    def test_only_final_complete_envelope_is_accepted(self):
        result={'meta':{'finalAssistantVisibleText':'{"ok":true}'},'payloads':[{'text':'interim'}]}
        self.assertEqual(oa.json_answer('banner\n'+json.dumps(result)),{'ok':True})
        for meta in ({'aborted':True},{'stopReason':'length'},{'error':'fail'}):
            with self.assertRaises(ValueError):oa.final_text(json.dumps({'meta':meta,'payloads':[{'text':'{}'}]}))

    def test_verification_cannot_change_program_result(self):
        a=oa.OpenClawAnalysis({},ROOT.parents[1]/'clawweb-skills/clawinsight-skills',Path('/tmp/not-used'),'/not-used','/not-used',time.monotonic()+60,{})
        data={'plan':{'payload':{'improvementId':43,'outcome':'INSUFFICIENT_DATA'}}}
        good={'improvementId':43,'outcome':'INSUFFICIENT_DATA','reason':'缺数据','gaps':['无相关流量']}
        self.assertEqual(a._validate('clawweb-verification',good,data),good)
        with self.assertRaises(ValueError):a._validate('clawweb-verification',dict(good,outcome='DISAPPEARED'),data)
        with self.assertRaises(ValueError):a._validate('clawweb-verification',dict(good,improvementId=44),data)
        with self.assertRaises(ValueError):a._validate('clawweb-verification',dict(good,extra=True),data)

    def test_actual_skill_text_is_loaded(self):
        a=oa.OpenClawAnalysis({},ROOT.parents[1]/'clawweb-skills/clawinsight-skills',Path('/tmp/not-used'),'/not-used','/not-used',time.monotonic()+60,{})
        self.assertIn('analysis-contract.md',a._skill_text('clawweb-governance'))
        self.assertIn('verification-contract.md',a._skill_text('clawweb-verification'))

    def test_context_limit_fails_before_process_launch(self):
        a=oa.OpenClawAnalysis({},ROOT.parents[1]/'clawweb-skills/clawinsight-skills',Path('/tmp/not-used'),'/not-used','/not-used',time.monotonic()+60,{})
        with patch.object(oa.subprocess,'Popen') as process:
            with self.assertRaises(ValueError):a.analyze('clawweb-governance',{'data':'x'*150001},'schema')
            process.assert_not_called()


class EntryTests(unittest.TestCase):
    def test_pinned_libraries_and_skills_unchanged(self):
        entry.integrity_check()

    def test_historical_apply_rejected(self):
        with self.assertRaises(SystemExit):entry.cli(['--apply','--date','20261001'])

    def test_apply_disabled_before_any_operation(self):
        cfg=Mock(allow_writes=False)
        with patch.object(entry,'load_config',return_value=cfg),patch.object(entry,'prepare_readonly_mounts') as mounts,patch.object(entry,'run') as pipeline:
            self.assertEqual(entry.main(['--apply']),2)
            mounts.assert_not_called();pipeline.assert_not_called()

    def test_check_never_constructs_data_or_starts_model(self):
        cfg=Mock(allow_writes=False)
        with patch.object(entry,'load_config',return_value=cfg),patch.object(entry,'static_check',return_value=({}, {'status':'STATIC_READY'})),patch.object(entry,'ReadyData') as data,patch.object(entry,'OpenClawAnalysis') as model,patch.object(entry,'run') as pipeline,patch.object(entry,'prepare_readonly_mounts') as mounts:
            self.assertEqual(entry.main(['--check']),0)
            data.assert_not_called();model.assert_not_called();pipeline.assert_not_called();mounts.assert_not_called()

    def test_all_files_are_bounded(self):
        for p in ROOT.rglob('*.py'):
            self.assertLessEqual(len(p.read_text().splitlines()),1000,str(p))


if __name__=='__main__':unittest.main()

class ProcessHarnessTests(unittest.TestCase):
    def test_json_failure_retries_fresh_session_and_keeps_secret_out_of_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm={'model':'unit','base_url':'https://example.test','api_key':'test-key'}
            a=oa.OpenClawAnalysis(llm,ROOT.parents[1]/'clawweb-skills/clawinsight-skills',Path(tmp)/'runs','/not-used','/not-used',time.monotonic()+900,{})
            payload={'improvementId':43,'outcome':'STILL_PRESENT','reason':'缺数据','gaps':['无流量']}
            p1=Mock(returncode=0);p1.communicate.return_value=('not-json','test-key')
            p2=Mock(returncode=0);p2.communicate.return_value=(json.dumps({'meta':{'finalAssistantVisibleText':json.dumps(payload)},'payloads':[{'text':'ignore'}]}),'')
            plan={'payload':{'improvementId':43,'outcome':'STILL_PRESENT'}}
            with patch.object(oa.subprocess,'Popen',side_effect=[p1,p2]) as proc:
                self.assertEqual(a.analyze('clawweb-verification',{'evidence':{'tasks':[]},'plan':plan},'test'),payload)
                calls=proc.call_args_list
                ids=[c.args[0][c.args[0].index('--session-id')+1] for c in calls]
                self.assertEqual(len(set(ids)),2)
                self.assertEqual(a.calls['clawweb-verification'],2)
            for p in Path(tmp).rglob('*'):
                if p.is_file():self.assertNotIn(b'test-key',p.read_bytes())

    def test_interrupt_is_not_swallowed_as_business_failure(self):
        self.assertFalse(issubclass(entry.TaskStopped,Exception))
