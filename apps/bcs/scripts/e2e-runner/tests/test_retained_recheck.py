"""Read-only recheck uses new authority without replaying a quarantined Story."""
import copy
import json
import os
from pathlib import Path
import signal
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.profile import fingerprint, ConfigError, load
from bcs_e2e.pool import bound_pool_path, PoolLease
from bcs_e2e.recheck_review import recheck_plan, RetainedRecheckReview
from bcs_e2e.retained_remote import CONTRACT, execution_generation
from bcs_e2e.run_state import read, write
import test_delivery_observation as fixtures
import test_remote_observer_cli
from test_remote_pilot_all_cases import RemoteShapedAdapter
from test_retained_remote import dual_header_profile


class RetainedRecheckTests(unittest.TestCase):
    command = fixtures.RetainedDeliveryCompositionTests.command
    boundaries = fixtures.RetainedDeliveryCompositionTests.boundaries
    process = fixtures.RetainedDeliveryCompositionTests.process

    def setUp(self):
        fixtures.RetainedDeliveryCompositionTests.setUp(self)
        fixtures.RetainedDeliveryCompositionTests.enable(self)
        with self.boundaries():
            rc,result=self.command(fixtures.RetainedDeliveryCompositionTests.args(self))
        self.assertEqual(result['functional'],'PASS')
        self.original = {p.name:p.read_bytes() for p in self.state.iterdir() if p.is_file()}
        self.lock=bound_pool_path(read(self.state/'run.json'))
        self.lock_bytes=self.lock.read_bytes()
        self.recheck_path=self.root/'fresh-recheck.json'
        self.fresh_binding=self.root/'fresh-binding.json'
        write(self.fresh_binding,self.receipt)
        template=recheck_plan(self.profile,self.state,self.run_id,'RETAIN-GROUP-01')['review_template']
        now=int(time.time())
        template.update(reviewed_at=now-1,expires_at=now+600,observation_id='observe-1',
                        observer_binding_digest=fingerprint(self.receipt))
        template['execution_observation_budget']={'timeout_seconds':30,'max_rounds':2,'poll_interval_seconds':1}
        self.recheck=template
        write(self.recheck_path,template)
        self.calls.clear();self.http.clear();self.delivery_queries.clear()

    def args(self):
        return ['retained-recheck','--profile',str(self.profile_path),'--state-dir',str(self.state),
                '--run-id',self.run_id,'--case','RETAIN-GROUP-01','--review',str(self.recheck_path),
                '--observer-binding',str(self.fresh_binding),'--mcporter-binary',str(self.executable),
                '--allow-observer-read','--confirm-run-id',self.run_id]

    def execute(self):
        with self.boundaries(),patch('bcs_e2e.delivery_observation.time.sleep'):
            return self.command(self.args())

    def assert_original_unchanged(self):
        for name,raw in self.original.items():self.assertEqual((self.state/name).read_bytes(),raw,name)
        self.assertEqual(self.lock.read_bytes(),self.lock_bytes)
        self.assertEqual(self.http,[])
        self.assertFalse(self.adapter.deleted)

    def test_readonly_recheck_saves_separate_evidence_never_green_or_release(self):
        rc,report=self.execute()
        self.assertEqual(rc,2,report)
        self.assertEqual(report['persistence'],'SAVED',report)
        self.assertEqual(report['execution_observation']['status'],'OBSERVED',report)
        self.assertEqual(report['execution_status'],'UNCONFIRMED')
        self.assertEqual(report['business_writes'],0)
        self.assertFalse(report['pool_release_authorized'])
        self.assertTrue((self.state/'observations/observe-1/report.json').exists())
        self.assert_original_unchanged()

    def test_historical_run_without_quota_can_be_rechecked_without_initialization(self):
        # Build a synthetic pre-quota historical fixture, never migrate real evidence.
        old = read(self.state/'retained-review.json')
        old.pop('retention_quota')
        old['expires_at'] = old['reviewed_at'] + 1
        write(self.state/'retained-review.json', old)
        for name in ('run.json', 'report-RETAIN-GROUP-01.json', 'retention.json'):
            value = read(self.state/name)
            value.pop('retention_quota', None)
            if 'retained_review' in value:
                value['retained_review']['receipt_digest'] = fingerprint(old)
                value['retained_review']['expires_at'] = old['expires_at']
            write(self.state/name, value)
        quota = self.lock.with_suffix('.retention-quota.json')
        quota.unlink()  # Synthetic fixture only: old runs had no such file.
        self.recheck['source'] = recheck_plan(
            self.profile, self.state, self.run_id, 'RETAIN-GROUP-01')['review_template']['source']
        write(self.recheck_path, self.recheck)
        self.original = {p.name:p.read_bytes() for p in self.state.iterdir() if p.is_file()}
        with patch('bcs_e2e.retention_quota.reserve', side_effect=AssertionError('no quota mutation')):
            rc, report = self.execute()
        self.assertEqual(rc, 2, report)
        self.assertEqual(report['execution_observation']['status'], 'OBSERVED', report)
        self.assertFalse(quota.exists())
        self.assert_original_unchanged()

    def test_dry_run_is_offline_and_has_no_effective_authorization(self):
        args=self.args()+['--dry-run']
        with self.boundaries():rc,report=self.command(args)
        self.assertEqual(rc,0)
        self.assertEqual(report['review_template']['expires_at'],0)
        self.assertEqual(self.calls,[])
        self.assertFalse((self.state/'observations').exists())
        self.assert_original_unchanged()

    def test_same_observation_cannot_be_replayed(self):
        rc,first=self.execute();self.assertEqual(first['persistence'],'SAVED')
        saved=(self.state/'observations/observe-1/report.json').read_bytes()
        self.calls.clear()
        rc,second=self.execute()
        self.assertEqual(rc,2);self.assertEqual(self.calls,[])
        self.assertEqual((self.state/'observations/observe-1/report.json').read_bytes(),saved)
        self.assert_original_unchanged()

    def test_missing_guard_is_not_recreated(self):
        self.lock.with_suffix('.guard').unlink()
        rc,report=self.execute()
        self.assertEqual(rc,2);self.assertEqual(self.calls,[])
        self.assertFalse(self.lock.with_suffix('.guard').exists())
        self.assert_original_unchanged()

    def test_busy_guard_blocks_all_queries(self):
        with PoolLease.from_file(self.lock,self.run_id,fingerprint(self.profile)):
            rc,report=self.execute()
        self.assertEqual(rc,2);self.assertEqual(self.calls,[])
        self.assert_original_unchanged()

    def test_changed_source_or_binding_refused_before_queries(self):
        value=read(self.fresh_binding);value['receipt_id']='another-valid-receipt';write(self.fresh_binding,value)
        rc,report=self.execute()
        self.assertEqual(rc,2);self.assertEqual(self.calls,[])
        write(self.fresh_binding,self.receipt)
        state=read(self.state/'run.json');state['note']='changed';write(self.state/'run.json',state)
        rc,report=self.execute()
        self.assertEqual(rc,2);self.assertEqual(self.calls,[])

    def test_expired_new_receipt_is_not_renewed(self):
        self.recheck['expires_at']=self.recheck['reviewed_at'];write(self.recheck_path,self.recheck)
        rc,report=self.execute()
        self.assertEqual(rc,2);self.assertEqual(self.calls,[])
        self.assert_original_unchanged()

    def test_unsafe_observation_names_and_symlink_output_are_rejected(self):
        for name in ('../outside','..','a/b','/tmp/out','a.b'):
            value=copy.deepcopy(self.recheck);value['observation_id']=name;write(self.recheck_path,value)
            rc,report=self.execute();self.assertEqual(rc,2);self.assertEqual(self.calls,[])
        write(self.recheck_path,self.recheck)
        (self.state/'observations').symlink_to(self.root,target_is_directory=True)
        rc,report=self.execute();self.assertEqual(rc,2);self.assertEqual(self.calls,[])
        self.assert_original_unchanged()

    def test_metadata_query_lost_lease_blocks_next_call(self):
        original=self.process
        def lose(command,**kwargs):
            result=original(command,**kwargs)
            self.lock.unlink()
            return result
        with patch.object(self,'process',side_effect=lose):rc,report=self.execute()
        self.assertEqual(rc,2)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(report['execution_observation']['status'],'NOT_RUN')
        self.assertEqual(report['persistence'],'SAVED')
        self.assertFalse(self.lock.exists())

    def test_cancel_during_query_persists_cancel_without_target_actions(self):
        self.delivery_response='cancel'
        rc,report=self.execute()
        self.assertEqual(rc,130,report)
        self.assertEqual(report['execution_observation']['status'],'CANCELLED')
        self.assertEqual(report['persistence'],'SAVED')
        self.assert_original_unchanged()

    def test_sigterm_during_final_publication_is_recorded(self):
        from bcs_e2e import retained_recheck as module
        real=module.write;fired=False
        def interrupt(path,value):
            nonlocal fired
            if path.name=='report.json' and value.get('persistence')=='SAVED' and not fired:
                fired=True;os.kill(os.getpid(),signal.SIGTERM)
            return real(path,value)
        with patch.object(module,'write',side_effect=interrupt):rc,report=self.execute()
        self.assertTrue(fired);self.assertEqual(rc,130,report)
        self.assertEqual(read(self.state/'observations/observe-1/report.json')['status'],'CANCELLED')
        self.assert_original_unchanged()

    def test_publication_failure_is_not_reported_saved(self):
        from bcs_e2e import retained_recheck as module
        real=module.write
        def fail(path,value):
            if path.name=='report.json' and value.get('persistence')=='SAVED':raise OSError('private-disk-error')
            return real(path,value)
        with patch.object(module,'write',side_effect=fail):rc,report=self.execute()
        self.assertEqual(rc,2)
        self.assertEqual(report['persistence'],'FAILED')
        self.assertNotIn('private-disk-error',json.dumps(report))
        self.assert_original_unchanged()

    def test_same_content_replaced_source_inode_invalidates_review(self):
        review=RetainedRecheckReview(self.profile,self.state,self.run_id,'RETAIN-GROUP-01',self.recheck_path)
        value=read(self.state/'retention.json');write(self.state/'retention.json',value)
        with self.assertRaises(ConfigError):review.validate()

    def test_expired_historical_receipts_do_not_block_new_explicit_read_authority(self):
        # Move the clock beyond both original receipts, then explicitly construct
        # new grants. Historical files and their original digests remain untouched.
        future=int(time.time())+7200
        grant=copy.deepcopy(self.recheck);grant.update(reviewed_at=future-1,expires_at=future+600)
        binding=copy.deepcopy(self.receipt);binding.update(reviewed_at=future-1,expires_at=future+600)
        grant['observer_binding_digest']=fingerprint(binding)
        write(self.recheck_path,grant);write(self.fresh_binding,binding)
        with patch('time.time',return_value=future):rc,report=self.execute()
        self.assertEqual(rc,2,report)
        self.assertEqual(report['execution_observation']['status'],'OBSERVED',report)
        self.assert_original_unchanged()

    def test_changed_ledger_resource_never_adopts_the_replacement(self):
        from bcs_e2e.ledger import Ledger
        with Ledger(self.state/'run.sqlite',self.run_id,fingerprint(self.profile)) as ledger:
            ledger.db.execute("UPDATE resource SET origin='borrowed' WHERE kind='session'")
            ledger.db.commit()
        rc,report=self.execute()
        self.assertEqual(rc,2);self.assertEqual(self.calls,[])
        self.assertFalse((self.state/'observations').exists())

    def test_ordinary_retained_review_cannot_query_a_quarantined_run(self):
        from bcs_e2e.delivery_observation import DeliveryReadScope,DeliveryReadPlan
        from bcs_e2e.ledger import Ledger
        from bcs_e2e.retained_remote import RetainedReview
        from bcs_e2e.evidence import EvidenceError
        review=RetainedReview(self.profile,self.run_id,'RETAIN-GROUP-01',self.state/'retained-review.json')
        review.state_dir=self.state
        resources=read(self.state/'retention.json')['resources']
        group=next(r['resource_id'] for r in resources if r['kind']=='group')
        session=next(r['resource_id'] for r in resources if r['kind']=='session')
        with PoolLease.from_file(self.lock,self.run_id,fingerprint(self.profile)) as lease, \
             Ledger(self.state/'run.sqlite',self.run_id,fingerprint(self.profile),read_only=True) as ledger:
            scope=DeliveryReadScope(ledger,lease,'RETAIN-GROUP-01')
            plan=DeliveryReadPlan('testdb','pre',group,session,('test-driver','test-member'))
            with self.assertRaises(EvidenceError):scope.validate(plan,review)
        self.assert_original_unchanged()

    def test_cancel_plus_final_disk_failure_keeps_cancelled_exit(self):
        from bcs_e2e import retained_recheck as module
        real=module.write
        def interrupt_and_fail(path,value):
            if path.name=='report.json' and value.get('persistence')=='SAVED':
                os.kill(os.getpid(),signal.SIGTERM)
                raise OSError('private-publication-error')
            return real(path,value)
        with patch.object(module,'write',side_effect=interrupt_and_fail):rc,report=self.execute()
        self.assertEqual(rc,130,report)
        self.assertEqual(report['status'],'CANCELLED')
        self.assertEqual(report['persistence'],'FAILED')
        self.assertEqual(report['errors'][-1]['error_type'],'OSError')
        self.assert_original_unchanged()

    def test_query_timeout_keeps_unconfirmed_child_shutdown_evidence(self):
        from bcs_e2e.bounded_process import ProcessCaptureTimeout
        real=self.process
        def timeout(command,**kwargs):
            if command[2].endswith('.db_query'):
                error=ProcessCaptureTimeout('private-timeout')
                error.child_shutdown_status='UNCONFIRMED'
                raise error
            return real(command,**kwargs)
        with patch.object(self,'process',side_effect=timeout):rc,report=self.execute()
        self.assertEqual(rc,2,report)
        self.assertEqual(report['execution_observation']['status'],'TIMED_OUT')
        self.assertEqual(report['child_shutdown_status'],'UNCONFIRMED')
        self.assertEqual(read(self.state/'observations/observe-1/report.json')['child_shutdown_status'],'UNCONFIRMED')
        self.assertNotIn('private-timeout',json.dumps(report))
        self.assert_original_unchanged()

    def test_guard_removed_between_precheck_and_open_is_never_recreated(self):
        original=PoolLease._guard
        def remove(path,**kwargs):
            self.assertTrue(kwargs['existing_only'])
            path.with_suffix('.guard').unlink()
            return original(path,**kwargs)
        with patch.object(PoolLease,'_guard',side_effect=remove):rc,report=self.execute()
        self.assertEqual(rc,2)
        self.assertEqual(self.calls,[])
        self.assertFalse(self.lock.with_suffix('.guard').exists())
        self.assert_original_unchanged()


class RetainedRecheckDualHeaderTests(RetainedRecheckTests):
    """The complete recheck battery re-executed under the dual-header generation."""

    def setUp(self):
        self._token_env = patch.dict(os.environ, {'BCS_E2E_DRIVER_TOKEN': 'synthetic-driver-token'})
        self._token_env.start()
        self.addCleanup(self._token_env.stop)

        def dual(path):
            return dual_header_profile(load(path))

        # Build the whole retained Run under the bcs-direct dual-header Profile:
        # the gateway Human-view injection in the remote fixture setUp is skipped.
        with patch('test_remote_observer_cli.load', side_effect=dual):
            test_remote_observer_cli.RemoteObserverCliTests.setUp(self)
        write(self.profile_path, self.profile)
        self.receipt['profile_digest'] = fingerprint(self.profile)
        write(self.receipt_path, self.receipt)
        self.review_path = self.root / 'retained-review.json'
        now = int(time.time())
        self.review = {'schema_version': 1, 'contract': CONTRACT, 'profile_digest': fingerprint(self.profile),
            'run_id': self.run_id, 'case_id': 'RETAIN-GROUP-01', 'reviewed_at': now - 1, 'expires_at': now + 1800,
            'supervisor': self.profile['human']['user_id'], 'effect_review': {
                'reference': 'synthetic-effect-review', 'sha256': 'd' * 64, 'deployment_commit': 'a' * 40,
                'isolated_test_runtime': True, 'no_operational_channels': True, 'implicit_bot_execution_allowed': True}}
        self.review['retention_quota'] = {'contract': 'retained-resource-quota-v1', 'registry_id': 'test-inventory',
            'mode': 'initialize', 'expected_digest': 'absent', 'max_groups': 10, 'max_sessions': 20,
            'baseline_groups': 0, 'baseline_sessions': 0, 'inventory_reference': 'test-inventory-review', 'inventory_sha256': 'e' * 64}
        write(self.review_path, self.review)
        self.adapter = RemoteShapedAdapter(self.profile)
        self.http = []
        self.visibility = 'protected'
        fixtures.RetainedDeliveryCompositionTests.enable(self)
        with self.boundaries():
            rc, result = self.command(fixtures.RetainedDeliveryCompositionTests.args(self))
        self.assertEqual(result['functional'], 'PASS', result)
        self.original = {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()}
        self.lock = bound_pool_path(read(self.state / 'run.json'))
        self.lock_bytes = self.lock.read_bytes()
        self.recheck_path = self.root / 'fresh-recheck.json'
        self.fresh_binding = self.root / 'fresh-binding.json'
        write(self.fresh_binding, self.receipt)
        template = recheck_plan(self.profile, self.state, self.run_id, 'RETAIN-GROUP-01')['review_template']
        now = int(time.time())
        template.update(reviewed_at=now - 1, expires_at=now + 600, observation_id='observe-1',
                        observer_binding_digest=fingerprint(self.receipt))
        template['execution_observation_budget'] = {'timeout_seconds': 30, 'max_rounds': 2, 'poll_interval_seconds': 1}
        self.recheck = template
        write(self.recheck_path, template)
        self.calls.clear()
        self.http.clear()
        self.delivery_queries.clear()

    def test_dual_header_generation_is_admitted_for_recheck(self):
        self.assertEqual(execution_generation(self.profile), 'bcs-direct-dual-header')
