"""Real retained gateway composition; only transport/MCP/WS replies are synthetic."""
import contextlib
import copy
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.adapters import RemotePilotAdapter
from bcs_e2e.identity_binding import HUMAN_VIEW, NATIVE
from bcs_e2e.inventory import current_manifest
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.remote_effects import RemoteBootstrapAdmissionError
from bcs_e2e.retained import retained_run
from bcs_e2e.retained_remote import CONTRACT, RemoteRetainedAdapter, RetainedReview, validate_review
from bcs_e2e.run_state import read, write
from bcs_e2e.transport import Observation, ObservationError
import test_remote_observer_cli as fixtures
from test_remote_pilot_all_cases import RemoteShapedAdapter


class RetainedRemoteTests(unittest.TestCase):
    command=fixtures.RemoteObserverCliTests.command
    process=fixtures.RemoteObserverCliTests.process

    def setUp(self):
        fixtures.RemoteObserverCliTests.setUp(self)
        self.profile['identity_binding']=dict(HUMAN_VIEW)
        write(self.profile_path,self.profile)
        self.receipt['profile_digest']=fingerprint(self.profile)
        write(self.receipt_path,self.receipt)
        self.review_path=self.root/'retained-review.json'
        now=int(time.time())
        self.review={'schema_version':1,'contract':CONTRACT,'profile_digest':fingerprint(self.profile),
            'run_id':self.run_id,'case_id':'RETAIN-GROUP-01','reviewed_at':now-1,'expires_at':now+1800,
            'supervisor':self.profile['human']['user_id'],'effect_review':{
                'reference':'synthetic-effect-review','sha256':'d'*64,'deployment_commit':'a'*40,
                'isolated_test_runtime':True,'no_operational_channels':True,'implicit_bot_execution_allowed':True}}
        self.review['retention_quota']={'contract':'retained-resource-quota-v1','registry_id':'test-inventory',
            'mode':'initialize','expected_digest':'absent','max_groups':10,'max_sessions':20,
            'baseline_groups':0,'baseline_sessions':0,'inventory_reference':'test-inventory-review','inventory_sha256':'e'*64}
        write(self.review_path,self.review)
        self.adapter=RemoteShapedAdapter(self.profile)
        self.http=[];self.visibility='protected'

    def test_review_summary_reports_the_admitted_generation(self):
        from bcs_e2e.retained_remote import review_summary
        self.assertEqual(review_summary(self.review, self.profile)['execution_generation'], 'gateway-human-view')

    def args(self):
        return ['retained-run','--profile',str(self.profile_path),'--state-dir',str(self.state),
                '--run-id',self.run_id,'--case',self.review['case_id'],'--allow-write','--confirm-run-id',self.run_id,
                '--retained-review',str(self.review_path),'--observer-binding',str(self.receipt_path),
                '--mcporter-binary',str(self.executable),'--allow-read','--allow-observer-read']

    @contextlib.contextmanager
    def boundaries(self, lose_create=False, expire_after_create=False):
        from bcs_e2e.alisddbquery import McporterAlisdDbQueryClient
        def metadata_client(**kwargs):return McporterAlisdDbQueryClient(**kwargs,runner=self.process)
        def read_http(client,operation,bot_id=''):
            self.http.append(('read',operation))
            if operation=='environment.read':data={'env':'pre'}
            elif operation=='identity.read':data={'user_id':'test-owner'}
            else:data={'bot_id':bot_id,'kind':'bot','created_by':'test-owner','env':'pre','status':'online',
                       **getattr(self,'visibility_projection',{'visibility':self.visibility})}
            evidence={'operation':operation,'http_status':200}
            client.evidence.append(evidence)
            return Observation(data,evidence)
        def write_http(client,operation,**kwargs):
            self.http.append(('write',operation))
            if operation=='group.create':
                value=self.adapter.group_create(kwargs['body'])
                if lose_create:raise RuntimeError('private-create-error')
                if expire_after_create:
                    target=self.state/'retained-review.json'
                    v=read(target);v['expires_at']=v['reviewed_at'];write(target,v)
                return value
            mapping={'group.read':lambda:self.adapter.group_read(kwargs['group_id']),
                     'group.update':lambda:self.adapter.group_update(kwargs['group_id'],kwargs['body']),
                     'session.create':lambda:self.adapter.session_create(kwargs['group_id'],kwargs['body']),
                     'session.read':lambda:self.adapter.session_read(kwargs['session_id']),
                     'session.update':lambda:self.adapter.session_update(kwargs['session_id'],kwargs['body']),
                     'session.messages':lambda:self.adapter.session_messages(kwargs['session_id']),
                     'session.token': (
                         lambda: self.adapter.session_token(kwargs['session_id']))}
            if operation not in mapping:raise AssertionError('unexpected transport operation '+operation)
            return mapping[operation]()
        def connect(_adapter,group_id,session_id,token):
            self.http.append(('ws','connect'))
            return self.adapter.session_ws_connect(group_id,session_id,token)
        def close(_adapter,session_id):
            self.http.append(('ws','close'))
            return self.adapter.session_ws_close(session_id)
        with patch.dict(os.environ,{'BCS_E2E_HUMAN_COOKIE':'synthetic-cookie'}), \
             patch('bcs_e2e.transport.ReadClient.read',read_http), \
             patch('bcs_e2e.write_transport.WriteClient.call',write_http), \
             patch.object(RemotePilotAdapter,'session_ws_connect',connect), \
             patch.object(RemotePilotAdapter,'session_ws_close',close), \
             patch('bcs_e2e.remote_observer.McporterAlisdDbQueryClient',side_effect=metadata_client):
            yield

    def test_reviewed_gateway_runs_group_slice_with_real_composition_and_no_delete(self):
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,2,result)
        self.assertEqual(result['functional'],'PASS',result)
        self.assertEqual(result['execution_status'],'UNCONFIRMED')
        self.assertEqual(result['cleanup'],'RETAINED')
        self.assertEqual(result['evidence_status'],'PASS')
        self.assertEqual(result['retained_review']['verification_mode'],'OPERATOR_ATTESTED')
        self.assertEqual(result['retained_review']['live_deployment_verification'],'NOT_PERFORMED')
        self.assertTrue(Path(read(self.state/'run.json')['lock_path']).exists())
        self.assertEqual(len(self.adapter.groups),1)
        self.assertEqual(len(self.adapter.sessions),1)
        self.assertFalse(self.adapter.deleted)
        queries=[c for c in self.calls if c[2].endswith('.db_query')]
        self.assertEqual(len(queries),1)
        tables=[json.loads(c[4])['table'] for c in self.calls if c[2].endswith('.table_describe')]
        self.assertNotIn('bcs_session_files',tables)
        self.assertFalse(any('delete' in op for _,op in self.http))
        self.assertEqual(read(self.state/'preflight.json')['observer_preflight']['business_queries'],0)
        self.assertEqual(len(read(self.state/'retention.json')['resources']),2)

    def test_reviewed_gateway_session_slice_keeps_both_sessions_and_closes_ws(self):
        self.review['case_id']='RETAIN-SESSION-01';write(self.review_path,self.review)
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,2,result)
        self.assertEqual(result['functional'],'PASS',result)
        self.assertEqual(result['socket_status'],'CLOSED')
        self.assertEqual(len(self.adapter.sessions),2)
        self.assertFalse(self.adapter.connected_sessions)
        self.assertFalse(self.adapter.deleted_sessions)
        self.assertEqual(len(read(self.state/'retention.json')['resources']),3)
        self.assertEqual(len([c for c in self.calls if c[2].endswith('.db_query')]),2)

    def test_invalid_review_never_reads_http_or_prepares(self):
        for edit in ({'expires_at':1},{'profile_digest':'0'*64},{'case_id':'PRE-01'},
                     {'run_id':'different'},{'supervisor':'different'}):
            with self.subTest(edit=edit):
                value={**self.review,**edit};write(self.review_path,value)
                with self.boundaries():rc,result=self.command(self.args())
                self.assertEqual(rc,2);self.assertFalse(self.state.exists());self.assertEqual(self.http,[])
        write(self.review_path,self.review)

    def test_runtime_review_or_native_identity_cannot_be_bypassed(self):
        for key in ('isolated_test_runtime','no_operational_channels','implicit_bot_execution_allowed'):
            value=copy.deepcopy(self.review);value['effect_review'][key]=False
            with self.assertRaises(ConfigError):validate_review(value,self.profile,self.run_id,self.review['case_id'])
        p=copy.deepcopy(self.profile);p['entrypoint']['kind']='bcs-direct';p['identity_binding']=NATIVE
        value=copy.deepcopy(self.review);value['profile_digest']=fingerprint(p)
        with self.assertRaises(ConfigError):validate_review(value,p,self.run_id,self.review['case_id'])

    def test_public_bot_fails_readonly_before_lease_or_mcp(self):
        self.visibility='public'
        with self.boundaries():rc,result=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertFalse(result['prepared'])
        self.assertEqual(result['functional'],'NOT_RUN')
        self.assertFalse(self.state.exists())
        self.assertEqual(self.calls,[])
        self.assertFalse(any(k=='write' for k,_ in self.http))

    def test_noncontract_or_invalid_visibility_cannot_admit_retained_bootstrap(self):
        projections=({}, {'capabilities':{'visibility':'protected'}},
                     {'visibility':'public','capabilities':{'visibility':'protected'}},
                     {'visibility':None}, {'visibility':True}, {'visibility':'PROTECTED'})
        for projection in projections:
            with self.subTest(projection=projection):
                self.visibility_projection=projection
                with self.boundaries():rc,result=self.command(self.args())
                self.assertEqual(rc,2)
                self.assertFalse(result['prepared'])
                self.assertEqual(result['functional'],'NOT_RUN')
                self.assertFalse(self.state.exists())
                self.assertEqual(self.calls,[])
                self.assertFalse(any(kind=='write' for kind,_ in self.http))

    def test_expired_observer_receipt_prevents_even_http_preflight(self):
        self.receipt['expires_at']=1;write(self.receipt_path,self.receipt)
        with self.boundaries():rc,_=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(self.http,[]);self.assertFalse(self.state.exists())

    def test_lost_create_response_and_review_expiry_do_not_retry_or_delete(self):
        with self.boundaries(lose_create=True):rc,result=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(result['functional'],'FAIL')
        self.assertEqual([x for x in self.http if x[0]=='write'],[('write','group.create')])
        self.assertEqual(len(self.adapter.groups),1)
        self.assertEqual(read(self.state/'retention.json')['inventory_status'],'UNKNOWN_RESOURCES')
        self.assertNotIn('private-create-error',json.dumps(result))
        self.assertFalse(self.adapter.deleted)

    def test_review_expires_after_create_before_any_followup_call(self):
        with self.boundaries(expire_after_create=True):rc,result=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(result['functional'],'FAIL')
        self.assertEqual([x for x in self.http if x[0]=='write'],[('write','group.create')])
        self.assertFalse(self.adapter.deleted)
        self.assertEqual(len(read(self.state/'retention.json')['resources']),2)
        self.assertEqual(read(self.state/'run.json')['status'],'QUARANTINED')

    def test_legacy_group_create_remains_blocked_and_retained_adapter_exposes_no_delete(self):
        with self.boundaries():
            legacy=RemotePilotAdapter(self.profile)
            with self.assertRaises(RemoteBootstrapAdmissionError):legacy.group_create({})
            review=RetainedReview(self.profile,self.run_id,self.review['case_id'],self.review_path)
            adapter=RemoteRetainedAdapter(self.profile,review,observer_client=None,binding=Mock())
            for name in ('group_delete','session_delete','file_prepare','quiesce_execution','session_create'):
                self.assertFalse(hasattr(adapter,name),name)
            with self.assertRaises(ConfigError):adapter.group_create({})
        self.assertFalse(any(k=='write' for k,_ in self.http))

    def test_missing_write_confirmation_never_contacts_either_service(self):
        args=self.args();args.remove('--allow-write')
        with self.boundaries():rc,_=self.command(args)
        self.assertEqual(rc,2);self.assertEqual(self.http,[]);self.assertEqual(self.calls,[])
        self.assertFalse(self.state.exists())


    def test_observer_binding_changes_after_creation_block_next_http_call(self):
        from bcs_e2e.control_steps import _observe_initial_session
        def corrupt(ledger,handle,data):
            _observe_initial_session(ledger,handle,data)
            p=self.state/'observer-binding.json';value=read(p)
            value['expires_at']=value['reviewed_at'];write(p,value)
        with self.boundaries(), patch('bcs_e2e.control_steps._observe_initial_session',side_effect=corrupt):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(result['functional'],'FAIL')
        self.assertEqual([v for v in self.http if v[0]=='write'],[('write','group.create')])
        self.assertFalse(self.adapter.deleted)

    def test_mcp_metadata_failure_is_sanitized_before_prepare(self):
        def fail(*args,**kwargs):raise RuntimeError('private-metadata-failure')
        with self.boundaries(), patch('bcs_e2e.retained_remote.AlisdDbQueryObserver.admit',side_effect=fail):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2);self.assertFalse(result['prepared'])
        self.assertEqual(result['preflight']['observer_preflight']['status'],'BLOCKED')
        self.assertFalse(self.state.exists())
        self.assertNotIn('private-metadata-failure',json.dumps(result))
        self.assertFalse(any(k=='write' for k,_ in self.http))

    def test_expiry_while_ws_owned_does_not_block_exact_socket_close(self):
        from bcs_e2e.correlation import CorrelatedEvidence
        original=CorrelatedEvidence.append
        self.review['case_id']='RETAIN-SESSION-01';write(self.review_path,self.review)
        def expire(evidence,item):
            result=original(evidence,item)
            if item.get('operation')=='session.ws.connect':
                p=self.state/'retained-review.json';value=read(p)
                value['expires_at']=value['reviewed_at'];write(p,value)
            return result
        with self.boundaries(),patch.object(CorrelatedEvidence,'append',expire):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(result['socket_status'],'CLOSED')
        self.assertFalse(self.adapter.connected_sessions)
        self.assertFalse(self.adapter.deleted_sessions)
        self.assertEqual(read(self.state/'run.json')['status'],'QUARANTINED')

    def test_post_preflight_actor_drift_stops_before_group_intent_dispatch(self):
        from bcs_e2e.retained_remote import preflight
        def drift(*args,**kwargs):
            result=preflight(*args,**kwargs);self.visibility='public';return result
        with self.boundaries(),patch('bcs_e2e.retained.remote_preflight',side_effect=drift):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2);self.assertEqual(result['functional'],'FAIL')
        self.assertEqual(read(self.state/'retention.json')['resources'],[])
        self.assertFalse(any(k=='write' for k,_ in self.http))

    def test_group_create_budget_consumed_even_when_response_is_lost(self):
        with self.boundaries(lose_create=True):
            review=RetainedReview(self.profile,self.run_id,self.review['case_id'],self.review_path)
            adapter=RemoteRetainedAdapter(self.profile,review,observer_client=None,binding=Mock())
            self.assertEqual(adapter.preflight()['status'],'DISCOVERY_PASSED')
            body={'group_kind':'normal','name':f'E2E {self.run_id}',
                  'context':'BCS E2E pilot RETAIN-GROUP-01','driver_bot_uuid':'test-driver',
                  'participants':[{'actor_id':'test-driver','role':'driver'},{'actor_id':'test-member','role':'consultant'}],
                  'collaboration':{'strategy':'chat'}}
            with self.assertRaises(RuntimeError):adapter.group_create(body)
            with self.assertRaises(ConfigError):adapter.group_create(body)
        self.assertEqual([x for x in self.http if x[0]=='write'],[('write','group.create')])


    def test_review_freeze_failure_or_cancel_preserves_prepared_failure_receipt(self):
        for error in (RuntimeError('private-freeze'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.state=self.root/('failed-freeze-'+type(error).__name__)
                # Separate synthetic pools so this test does not bypass a retained lock.
                self.profile['policy']['fixture_pool']=self.state.name
                self.review['profile_digest']=fingerprint(self.profile)
                self.receipt['profile_digest']=fingerprint(self.profile)
                write(self.profile_path,self.profile);write(self.review_path,self.review);write(self.receipt_path,self.receipt)
                with self.boundaries(),patch.object(RetainedReview,'freeze',side_effect=error):
                    rc,result=self.command(self.args())
                self.assertEqual(rc,130 if isinstance(error,KeyboardInterrupt) else 2)
                self.assertTrue(result['prepared'])
                self.assertEqual(result['functional'],'NOT_RUN')
                self.assertEqual(result['failure']['phase'],'review-freeze')
                self.assertEqual(result['persistence'],'PERSISTED')
                self.assertEqual(read(self.state/'retained-run.json'),result)
                state=read(self.state/'run.json')
                self.assertEqual(state['status'],'QUARANTINED')
                self.assertTrue(Path(state['lock_path']).exists())
                self.assertFalse(any(k=='write' for k,_ in self.http))
                self.assertNotIn('private-freeze',json.dumps(result))

    def test_preflight_checkpoint_failure_is_saved_without_business_execution(self):
        from bcs_e2e.retained import write as original
        def fail(path,value):
            if path.name=='preflight.json':raise OSError('private-disk')
            return original(path,value)
        with self.boundaries(),patch('bcs_e2e.retained.write',side_effect=fail):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(result['functional'],'NOT_RUN')
        self.assertEqual(result['failure']['phase'],'preflight-checkpoint')
        self.assertEqual(result['persistence'],'PERSISTED')
        self.assertEqual(read(self.state/'retained-run.json'),result)
        self.assertFalse(any(k=='write' for k,_ in self.http))

    def test_prepared_failure_receipt_disk_error_does_not_claim_saved(self):
        from bcs_e2e.retained import write as original
        def fail(path,value):
            if path.name in {'preflight.json','retained-run.json'}:raise OSError('private-disk')
            return original(path,value)
        with self.boundaries(),patch('bcs_e2e.retained.write',side_effect=fail):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(result['persistence'],'FAILED')
        self.assertEqual(result['persistence_errors'],[{'phase':'lifecycle-receipt','error_type':'OSError'}])
        self.assertFalse((self.state/'retained-run.json').exists())
        state=read(self.state/'run.json')
        self.assertEqual(state['status'],'QUARANTINED')
        self.assertTrue(Path(state['lock_path']).exists())


    def test_review_expiry_at_execution_entry_is_still_preexecution_failure(self):
        from bcs_e2e.retained import run_retained_case
        def expire(*args,**kwargs):
            p=self.state/'retained-review.json';value=read(p)
            value['expires_at']=value['reviewed_at'];write(p,value)
            return run_retained_case(*args,**kwargs)
        with self.boundaries(),patch('bcs_e2e.retained.run_retained_case',side_effect=expire):
            rc,result=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(result['functional'],'NOT_RUN')
        self.assertEqual(result['failure']['phase'],'case-execution')
        self.assertEqual(result['persistence'],'PERSISTED')
        self.assertEqual(read(self.state/'retained-run.json'),result)
        self.assertEqual(read(self.state/'run.json')['status'],'QUARANTINED')
        self.assertFalse(any(k=='write' for k,_ in self.http))

    def test_post_handoff_error_is_never_overwritten_with_not_run(self):
        from bcs_e2e.retained import _execute
        def fail_after_effect(*args,**kwargs):
            _execute(*args,**kwargs)
            raise OSError('private-after-effect')
        with self.boundaries(),patch('bcs_e2e.retained._execute',side_effect=fail_after_effect):
            rc,_=self.command(self.args())
        self.assertEqual(rc,2)
        self.assertEqual(len(self.adapter.groups),1)
        self.assertFalse((self.state/'retained-run.json').exists())
        self.assertEqual(read(self.state/'report-RETAIN-GROUP-01.json')['functional'],'PASS')
        self.assertTrue(Path(read(self.state/'run.json')['lock_path']).exists())


    def test_entry_contention_does_not_quarantine_another_command_or_claim_not_run(self):
        from bcs_e2e.pool import PoolLease, bound_pool_path
        from bcs_e2e.retained import run_retained_case
        held=[]
        def contend(*args,**kwargs):
            state=read(self.state/'run.json')
            held.append(PoolLease.from_file(bound_pool_path(state),self.run_id,state['profile_digest']))
            return run_retained_case(*args,**kwargs)
        try:
            with self.boundaries(),patch('bcs_e2e.retained.run_retained_case',side_effect=contend):
                rc,result=self.command(self.args())
            self.assertEqual(rc,2)
            self.assertEqual(result['functional'],'UNKNOWN')
            self.assertEqual(result['persistence'],'PARTIAL')
            self.assertEqual(read(self.state/'run.json')['status'],'PREPARED')
            self.assertTrue(Path(read(self.state/'run.json')['lock_path']).exists())
            self.assertFalse(any(k=='write' for k,_ in self.http))
        finally:
            for guard in held:guard.close()


def dual_header_profile(profile: dict) -> dict:
    """Convert a gateway fixture Profile into the reviewed dual-header generation shape."""
    value = copy.deepcopy(profile)
    value['entrypoint']['kind'] = 'bcs-direct'
    value['entrypoint']['origin'] = 'https://bcs-pre.alipay.com'
    value.pop('identity_binding', None)
    value['auth']['header_env'] = {'Authorization': 'BCS_E2E_DRIVER_TOKEN', 'Cookie': 'BCS_E2E_HUMAN_COOKIE'}
    return value


class RetainedDualHeaderGenerationTests(RetainedRemoteTests):
    """Generation D: bcs-direct without explicit identity_binding; driver token plus Human Cookie."""

    def setUp(self):
        super().setUp()
        self.profile = dual_header_profile(self.profile)
        write(self.profile_path, self.profile)
        self.receipt['profile_digest'] = fingerprint(self.profile)
        self.receipt['target']['api_origin'] = self.profile['entrypoint']['origin']
        write(self.receipt_path, self.receipt)
        self.review['profile_digest'] = fingerprint(self.profile)
        write(self.review_path, self.review)
        self._token_env = patch.dict(os.environ, {'BCS_E2E_DRIVER_TOKEN': 'synthetic-driver-token'})
        self._token_env.start()
        self.addCleanup(self._token_env.stop)

    def test_review_summary_reports_the_admitted_generation(self):
        from bcs_e2e.retained_remote import review_summary
        self.assertEqual(review_summary(self.review, self.profile)['execution_generation'], 'bcs-direct-dual-header')

    def test_dual_header_generation_runs_group_slice_with_real_composition_and_no_delete(self):
        with self.boundaries():
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['functional'], 'PASS', result)
        self.assertEqual(result['execution_status'], 'UNCONFIRMED')
        self.assertEqual(result['cleanup'], 'RETAINED')
        self.assertEqual(result['evidence_status'], 'PASS')
        self.assertEqual(result['retained_review']['execution_generation'], 'bcs-direct-dual-header')
        self.assertTrue(Path(read(self.state / 'run.json')['lock_path']).exists())
        self.assertEqual(len(self.adapter.groups), 1)
        self.assertEqual(len(self.adapter.sessions), 1)
        self.assertFalse(self.adapter.deleted)
        self.assertFalse(any('delete' in op for _, op in self.http))
        self.assertEqual(len(read(self.state / 'retention.json')['resources']), 2)

    def test_dual_header_generation_review_declaration_is_bound_and_reported(self):
        from bcs_e2e import retained_remote
        undeclared = copy.deepcopy(self.review)
        self.assertEqual(retained_remote.execution_generation(self.profile), 'bcs-direct-dual-header')
        retained_remote.validate_review(undeclared, self.profile, self.run_id, self.review['case_id'])
        declared = {**undeclared, 'execution_generation': 'bcs-direct-dual-header'}
        retained_remote.validate_review(declared, self.profile, self.run_id, self.review['case_id'])
        forged = {**undeclared, 'execution_generation': 'gateway-human-view'}
        with self.assertRaises(ConfigError):
            retained_remote.validate_review(forged, self.profile, self.run_id, self.review['case_id'])

    def test_only_the_two_reviewed_generations_are_admitted(self):
        from bcs_e2e import retained_remote
        gateway = dual_header_profile(self.profile)
        gateway['entrypoint']['kind'] = 'gateway'
        gateway['identity_binding'] = dict(HUMAN_VIEW)
        gateway['auth']['header_env'] = {'Cookie': 'BCS_E2E_HUMAN_COOKIE'}
        self.assertEqual(retained_remote.execution_generation(gateway), 'gateway-human-view')
        vetoes = []
        gateway_dual = copy.deepcopy(gateway)
        gateway_dual['auth']['header_env'] = {'Authorization': 'BCS_E2E_DRIVER_TOKEN', 'Cookie': 'BCS_E2E_HUMAN_COOKIE'}
        vetoes.append(('gateway-with-bot-token', gateway_dual))
        gateway_no_binding = copy.deepcopy(gateway)
        gateway_no_binding.pop('identity_binding')
        vetoes.append(('gateway-without-identity-binding', gateway_no_binding))
        native = copy.deepcopy(self.profile)
        native['identity_binding'] = dict(NATIVE)
        vetoes.append(('bcs-direct-explicit-native', native))
        human_view = copy.deepcopy(native)
        human_view['identity_binding'] = dict(HUMAN_VIEW)
        vetoes.append(('bcs-direct-explicit-human-view', human_view))
        cookie_only = copy.deepcopy(self.profile)
        cookie_only['auth']['header_env'] = {'Cookie': 'BCS_E2E_HUMAN_COOKIE'}
        vetoes.append(('bcs-direct-cookie-only', cookie_only))
        for name, profile in vetoes:
            with self.subTest(veto=name):
                with self.assertRaises(ConfigError):
                    retained_remote.execution_generation(profile)
                with self.assertRaises(ConfigError):
                    retained_remote.require_remote_profile(profile)
