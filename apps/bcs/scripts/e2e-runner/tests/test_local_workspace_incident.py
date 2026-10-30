"""Unit guards and opt-in actual-runtime coverage for incident configuration."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.ledger import Ledger
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_workspace_api import OwnedLocalWorkspaceApi
from bcs_e2e.local_workspace_cli import OwnedLocalWorkspaceCli
from bcs_e2e.local_workspace_context import prepare_workspace_context
from bcs_e2e.local_workspace_incident import (
    CASE_ID, OBLIGATIONS, OPERATION_SEQUENCE, OPERATION_EXPECTED_STATUS,
    WORKSPACE, OwnedLocalWorkspaceIncident,
)
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.run_state import RunStateError, read

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_DIGEST = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


class IncidentGuardTests(unittest.TestCase):
    def test_contract_is_exact_first_four_remaining_requirements(self):
        self.assertEqual(CASE_ID, 'LOCAL-WORKSPACE-INCIDENT-01')
        self.assertEqual(OBLIGATIONS, (
            'WORKSPACE.incident-group', 'WORKSPACE.participant-mode',
            'WORKSPACE.workspace', 'WORKSPACE.routing-service'))
        self.assertEqual(OPERATION_SEQUENCE, (
            'incident-create', 'incident-detail', 'participant-mode', 'workspace-put',
            'workspace-get', 'routing-policy-put', 'settings-patch', 'incident-readback'))
        self.assertEqual(WORKSPACE, {
            'decisions': ['ship the hotfix'], 'notes': ['customer impact contained'],
            'tasks': [], 'audit_log': []})
        self.assertEqual(OPERATION_EXPECTED_STATUS,
                         {'workspace-put': 404, 'workspace-get': 404,
                          'settings-patch': (200, 410)})

    def test_settings_face_admits_retired_and_legacy_forms(self):
        self.assertEqual(OwnedLocalWorkspaceIncident._settings_form(
            {'error': 'service_policy_retired',
             'message': 'service_spec settings have been retired'}), 'retired_410')
        self.assertEqual(OwnedLocalWorkspaceIncident._settings_form(
            {'id': 'grp', 'status': 'ok', 'service_spec': {'max_concurrency': 2}}),
            'service_spec_ack')
        self.assertIsNone(OwnedLocalWorkspaceIncident._settings_form(
            {'error': 'unexpected_body'}))
        self.assertIsNone(OwnedLocalWorkspaceIncident._settings_form(
            {'id': 'grp', 'status': 'ok', 'service_spec': {'max_concurrency': 5}}))
        self.assertIsNone(OwnedLocalWorkspaceIncident._settings_form('not-an-object'))
        self.assertIsNone(OwnedLocalWorkspaceIncident._settings_form(None))
        # legacy face keeps its response-id equality gate
        self.assertEqual(OwnedLocalWorkspaceIncident._readback_form(
            {'id': 'grp'}), 'retired_410')

    def test_readback_face_distinguishes_retired_from_legacy_projection(self):
        self.assertEqual(OwnedLocalWorkspaceIncident._readback_form(
            {'id': 'grp', 'service_spec': {'max_concurrency': 2}}), 'service_spec_ack')
        self.assertIsNone(OwnedLocalWorkspaceIncident._readback_form(
            {'id': 'grp', 'service_spec': {'max_concurrency': 9}}))
        self.assertIsNone(OwnedLocalWorkspaceIncident._readback_form('not-an-object'))
        self.assertIsNone(OwnedLocalWorkspaceIncident._readback_form({'service_spec': {}}))

    def test_persisted_service_spec_column_admits_retired_null_and_legacy_dict(self):
        self.assertEqual(OwnedLocalWorkspaceIncident._service_spec_form(None),
                         'retired_null')
        self.assertEqual(OwnedLocalWorkspaceIncident._service_spec_form(
            {'max_concurrency': 2}), 'legacy_ack')
        self.assertIsNone(OwnedLocalWorkspaceIncident._service_spec_form(
            {'max_concurrency': 5}))
        self.assertIsNone(OwnedLocalWorkspaceIncident._service_spec_form({}))
        self.assertIsNone(OwnedLocalWorkspaceIncident._service_spec_form('not-an-object'))

    def test_constructor_rejects_unmanaged_identity(self):
        with self.assertRaisesRegex(RunStateError, 'verified owned'):
            OwnedLocalWorkspaceIncident(object())

    def test_foreign_or_out_of_order_operation_rejected_before_actor_or_ledger(self):
        case = object.__new__(OwnedLocalWorkspaceIncident)
        case._active = Mock()
        case.state = {'operations': [{'operation': 'foreign'}], 'resources': {}}
        case.ledger = Mock()
        case._actor = Mock(side_effect=AssertionError('actor admission must not run'))
        with self.assertRaisesRegex(RunStateError, 'finite ordered'):
            case._operation('incident-create')
        case.ledger.group_creation_intent.assert_not_called()
        case._actor.assert_not_called()

    def test_existing_resource_is_rechecked_before_actor_or_http(self):
        case = object.__new__(OwnedLocalWorkspaceIncident)
        case._active = Mock()
        case._id = lambda role: {'CEO': 'ceo', 'PM': 'pm', 'ENG': 'eng'}[role]
        case.state = {'operations': [{'operation': 'incident-create'}],
                      'resources': {'incident': {'id': 'group-owned', 'handle': 'group-handle',
                          'initial_session': {'id': 'group-owned:session', 'handle': 'session-handle'}}}}
        case.ledger = Mock()
        case._actor = Mock(side_effect=AssertionError('actor admission must not run'))
        case._require_owned_resource = Mock(side_effect=RunStateError('ledger drift'))
        with self.assertRaisesRegex(RunStateError, 'ledger drift'):
            case._operation('incident-detail')
        case._require_owned_resource.assert_called_once_with(case.state['resources']['incident'])
        case._actor.assert_not_called()

    def test_foreign_group_or_session_ledger_binding_is_rejected(self):
        for target, field, value in (
            ('group', 'resource_id', 'foreign-group'),
            ('group', 'origin', 'borrowed'),
            ('group', 'case_id', 'OTHER-CASE'),
            ('session', 'resource_id', 'foreign-session'),
            ('session', 'state', 'unknown'),
            ('session', 'attempt', 2),
            ('session', 'parent', None),
        ):
            with self.subTest(target=target, field=field), tempfile.TemporaryDirectory() as tmp:
                with Ledger(Path(tmp)/'ledger.sqlite', 'incident-guard', 'a'*64, create=True) as ledger:
                    group, session = ledger.group_creation_intent(CASE_ID, 1, 'intent-one')
                    ledger.observed(group, 'owned-group', origin='created')
                    ledger.observed(session, 'owned-group:session', origin='created')
                    resource = {'id': 'owned-group', 'handle': group,
                                'initial_session': {'id': 'owned-group:session', 'handle': session}}
                    case = object.__new__(OwnedLocalWorkspaceIncident)
                    case.ledger = ledger
                    case._require_owned_resource(resource)
                    handle = group if target == 'group' else session
                    # Controlled corruption of the test ledger, never the product DB.
                    with ledger.db:
                        ledger.db.execute(f'UPDATE resource SET {field}=? WHERE handle=?', (value, handle))
                    with self.assertRaisesRegex(RunStateError, 'ledger scope changed'):
                        case._require_owned_resource(resource)

    def test_source_has_no_messages_remote_sql_writes_or_deletion_surface(self):
        source = (ROOT/'bcs_e2e'/'local_workspace_incident.py').read_text()
        for forbidden in ('/messages', '/chat', '/callback', "method='DELETE'", 'method="DELETE"',
                          'INSERT INTO bcs_', 'UPDATE bcs_', 'DELETE FROM bcs_',
                          'subprocess', 'shell=True', '/openapi/', 'Cookie'):
            self.assertNotIn(forbidden, source)
        self.assertIn("'messages': 'NOT_RUN'", source)
        self.assertIn("'incident_delete': 'NOT_RUN'", source)
        self.assertIn("'ready_to_execute': False", source)
        self.assertIn("'workspace_routes': 'DEPRECATED_HTTP_404'", source)


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'approved actual BCS and CLI binaries required')
class IncidentLiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'runtime'

    def execute(self):
        with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                             root=self.root, run_id='workspace-incident-live') as runtime:
            fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles:
                    identities.verify(role)
                self.assertEqual(OwnedLocalWorkspaceApi(identities).run()['status'],
                                 'LOCAL_WORKSPACE_API_PASS')
                prepare_workspace_context(identities)
                self.assertEqual(OwnedLocalWorkspaceCli(
                    identities, Path(CLI), CLI_DIGEST).run()['status'],
                    'LOCAL_WORKSPACE_CLI_PASS')
                result = OwnedLocalWorkspaceIncident(identities).run()
        return result

    def test_actual_incident_configuration_retains_exact_fifth_group(self):
        result = self.execute()
        self.assertEqual(result, read(self.root/'local-workspace-incident.json'))
        self.assertEqual(result['status'], 'LOCAL_WORKSPACE_INCIDENT_CONFIG_PASS')
        self.assertEqual(result['assertions'], [
            {'id': item, 'status': 'PASS'} for item in OBLIGATIONS])
        self.assertEqual([row['operation'] for row in result['operations']],
                         list(OPERATION_SEQUENCE))
        self.assertTrue(all(row['status'] == 'ACKNOWLEDGED' for row in result['operations']))
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(result['messages'], 'NOT_RUN')
        self.assertEqual(result['incident_delete'], 'NOT_RUN')
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_groups WHERE env='local'").fetchone()[0], 5)
        with sqlite3.connect(self.root/'workspace-incident-resources.sqlite') as db:
            rows = db.execute('SELECT kind,origin,state FROM resource ORDER BY rowid').fetchall()
        self.assertEqual(rows, [('group', 'created', 'live'), ('session', 'created', 'live')])
        self.assertEqual(read(self.root/'owned-bcs.json')['status'], 'STOPPED')


    def test_workspace_unavailable_drift_stops_before_routing_and_settings(self):
        original = OwnedLocalWorkspaceIncident._operation
        def wrong_status(case, operation):
            result = original(case, operation)
            if operation == 'workspace-get':
                return {'http_status': 200}
            return result
        with patch.object(OwnedLocalWorkspaceIncident, '_operation', wrong_status):
            with self.assertRaises(RunStateError):
                self.execute()
        journal = read(self.root/'local-workspace-incident.json')
        self.assertEqual(journal['status'], 'BLOCKED')
        self.assertEqual(journal['assertions'][-1], {'id': 'WORKSPACE.workspace', 'status': 'FAIL'})
        self.assertEqual([r['operation'] for r in journal['operations']], list(OPERATION_SEQUENCE[:5]))
        self.assertEqual(read(self.root/'owned-bcs.json')['status'], 'STOPPED')

    def test_create_response_loss_records_unknown_without_replay(self):
        original = OwnedLocalWorkspaceIncident._actor
        def install_loss(case, role):
            actor = original(case, role)
            if role == 'CEO' and not case.state['operations']:
                opener = case.identities.opener
                real_open = opener.open
                def lose_response(request, *args, **kwargs):
                    response = real_open(request, *args, **kwargs)
                    if request.get_method() == 'POST' and request.full_url.endswith('/groups'):
                        response.close()
                        raise OSError('synthetic response loss after actual create')
                    return response
                opener.open = lose_response
            return actor
        with patch.object(OwnedLocalWorkspaceIncident, '_actor', install_loss):
            with self.assertRaises(RunStateError):
                self.execute()
        journal = read(self.root/'local-workspace-incident.json')
        self.assertEqual(journal['status'], 'BLOCKED')
        self.assertEqual(len(journal['operations']), 1)
        self.assertEqual(journal['operations'][0]['status'], 'UNKNOWN')
        self.assertEqual(journal['resources'], {})
        with sqlite3.connect(self.root/'workspace-incident-resources.sqlite') as db:
            self.assertEqual(db.execute('SELECT kind, origin, state FROM resource ORDER BY rowid').fetchall(),
                             [('group', 'unknown', 'unknown'), ('session', 'unknown', 'unknown')])
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bcs_groups').fetchone()[0], 5)
        self.assertEqual(read(self.root/'owned-bcs.json')['status'], 'STOPPED')


if __name__ == '__main__':
    unittest.main()
