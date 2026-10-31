"""Unit tests for the owned-local custom-collaboration parent lifecycle."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bcs_e2e.inventory import KNOWN_UNMAPPED_LEGACY_IDS
from bcs_e2e.local_custom_collaboration_case import (CASE_ID, DENSITY_REQUIREMENTS,
                                                         DENSITY_SPECS, REQUIREMENTS)
from bcs_e2e.local_custom_collaboration_reporting import (
    build_custom_collaboration_report,
)
from bcs_e2e.local_custom_collaboration_run import (
    DEFINITION_PATH,
    DEFINITION_YAML,
    SUCCESS_STATUS,
    local_custom_collaboration_run,
)
from bcs_e2e.run_state import RunStateError, write


class FakeOwnedBcsProcess:
    def __init__(self, *, binary, binary_sha256, root, run_id):
        self.binary = binary
        self.binary_sha256 = binary_sha256
        self.root = root
        self.run_id = run_id
        self.inputs = self
        self.human_id = 'e2ehuman123'
        self.origin = 'http://127.0.0.1:12345'
        self.port = 12345
        self.exited = False

    def __enter__(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        write(self.root / 'bcs-config.json', {'schema_version': 1,
                                              'bind': '127.0.0.1',
                                              'store_messages': False})
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.exited = True
        write(self.root / 'owned-bcs.json', self._record())
        return False

    def _record(self):
        return {'schema_version': 1, 'status': 'STOPPED', 'returncode': 0,
                'forced_stop': False, 'run_id': self.run_id, 'origin': self.origin,
                'binary_sha256': self.binary_sha256}


class FakeFixtures:
    def __init__(self, runtime):
        self.runtime = runtime
        self.fixture_set = 'pilot'
        self.roles = ('driver', 'member')
        self.receipt = None

    def _state(self):
        return {'schema_version': 1, 'status': 'VERIFIED', 'run_id': self.runtime.run_id,
                'runtime_origin': self.runtime.origin, 'human': {'user_id': self.runtime.human_id},
                'roles': {
                    'driver': {'bot_id': 'driver-bot', 'owner_id': self.runtime.human_id,
                               'dedicated': True, 'name': 'driver', 'origin': 'created'},
                    'member': {'bot_id': 'member-bot', 'owner_id': self.runtime.human_id,
                               'dedicated': True, 'name': 'member', 'origin': 'created'},
                }}

    def apply(self, *, allow_write, driver_token_sink):
        assert allow_write is True
        driver_token_sink('driver-bot', 'owned-driver-token')
        self.receipt = self._state()
        write(self.runtime.root / 'local-fixtures.json', self.receipt)

    def check(self):
        return self._state()


class CustomCollaborationLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state_dir = self.root / 'state'
        self.run_id = 'custom-collab-lifecycle'
        self.binary = self.root / 'bcs'
        self.cli = self.root / 'bcs-cli'
        self.binary.write_bytes(b'bcs')
        self.cli.write_bytes(b'bcs-cli')
        self.binary_sha = 'a' * 64
        self.cli_sha = 'b' * 64
        reviewed_legacy = [
            {'id': 'story_cli_operator_creates_custom_collaboration'}
        ] + [{'id': f'other-{index}'} for index in range(28)]
        # The frozen binding keeps the raw 41-row manifest; only the release
        # denominator guards read the reviewed reviewed-release-row inventory.
        raw_legacy = list(reviewed_legacy) + [
            {'id': legacy_id} for legacy_id in KNOWN_UNMAPPED_LEGACY_IDS]
        self.raw_legacy = raw_legacy
        self.inventory_patcher = patch(
            'bcs_e2e.local_custom_collaboration_run.reviewed_release_inventory',
            return_value=reviewed_legacy)
        self.run_manifest_patcher = patch(
            'bcs_e2e.local_custom_collaboration_run.current_manifest',
            return_value={'legacy_inventory': raw_legacy})
        # 5aae6c12d unified frozen-binding guards on the reviewed release
        # denominator: the reporting module no longer reads the raw manifest
        # (current_manifest import removed), so the fixture world is patched at
        # the inventory namespace, where the guard helper resolves it. The run
        # module keeps its own directly-imported names patched above.
        self.inventory_manifest_patcher = patch(
            'bcs_e2e.inventory.current_manifest',
            return_value={'legacy_inventory': raw_legacy})
        self.inventory_reviewed_patcher = patch(
            'bcs_e2e.inventory.reviewed_release_inventory',
            return_value=reviewed_legacy)
        self.digest_patcher = patch(
            'bcs_e2e.local_custom_collaboration_run.executor_digest',
            return_value='e' * 64)
        self.reporting_digest_patcher = patch(
            'bcs_e2e.local_custom_collaboration_reporting.executor_digest',
            return_value='e' * 64)
        for patcher in (self.inventory_patcher, self.run_manifest_patcher,
                        self.inventory_manifest_patcher, self.inventory_reviewed_patcher,
                        self.digest_patcher, self.reporting_digest_patcher):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_success_case_file(self, root, yaml_path, status=SUCCESS_STATUS):
        result = {
            'schema_version': 1,
            'status': status,
            'case_id': CASE_ID,
            'legacy_id': 'story_cli_operator_creates_custom_collaboration',
            'assertions': [
                {'id': 'CUSTOM-COLLABORATION.validate-yaml', 'status': 'PASS'},
                {'id': 'CUSTOM-COLLABORATION.create-persistent-group', 'status': 'PASS'},
                {'id': 'CUSTOM-COLLABORATION.trial-one-shot-lifecycle', 'status': 'PASS'},
            ] + [{'id': name, 'status': 'PASS'} for name in DENSITY_REQUIREMENTS],
            'density_observations': {f"CUSTOM-COLLABORATION.{name}": True
                                     for name, _method, _first, _last in DENSITY_SPECS},
            'selected_requirement_ids': list(REQUIREMENTS),
            'parent_requirement_count': 37,
            'full_parent_coverage': 'LOCAL_PARENT_REQUIREMENTS_ONLY',
            'operations': {
                'validate': {'status': 'LOCAL_CUSTOM_COLLABORATION_VALIDATE_PASS',
                             'remote_validation': 'NOT_RUN', 'grants_release_credit': False},
                'trial': {'status': 'LOCAL_CUSTOM_COLLABORATION_TRIAL_PASS',
                          'remote_validation': 'NOT_RUN', 'grants_release_credit': False},
                'create-persistent-group': {
                    'status': 'LOCAL_CUSTOM_COLLABORATION_CREATE_GROUP_PASS',
                    'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
                    'group_id': 'created-group', 'group_cleanup': 'DELETED'},
            },
            'persistent_group_cleanup': 'DELETED',
            'binary_sha256': self.cli_sha,
            'driver_bot': 'driver-bot',
            'human_participant': 'human_e2ehuman123',
            'yaml_path': str(yaml_path),
            'remote_validation': 'NOT_RUN',
            'grants_release_credit': False,
            'legacy_counts': {
                'case_id': CASE_ID,
                'legacy_id': 'story_cli_operator_creates_custom_collaboration',
                'requirement_ids': list(REQUIREMENTS),
                'parent_requirement_count': 37,
            },
        }
        write(root / 'local-custom-collaboration.json', result)
        return result

    def call_run(self, case_status=SUCCESS_STATUS):
        observed = {}
        admitted = []
        websocket = []

        def fake_runtime(*, binary, binary_sha256, root, run_id):
            return FakeOwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                       root=root, run_id=run_id)

        def fake_fixtures(runtime):
            return FakeFixtures(runtime)

        def fake_admission(runtime, *, driver_id, member_id):
            admitted.append((driver_id, member_id))

        def fake_websocket(port, driver_id, driver_token):
            websocket.append((port, driver_id, driver_token))

            class FakeOwnedBotWebSocket:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_value, traceback):
                    return False

            return FakeOwnedBotWebSocket()

        def fake_case(runtime, *, state_dir, binary, binary_sha256, yaml_path,
                      driver_id, reviewer_id, human_id, driver_token):
            observed.update({
                'state_dir': state_dir, 'binary': binary,
                'binary_sha256': binary_sha256, 'yaml_path': yaml_path,
                'driver_id': driver_id, 'reviewer_id': reviewer_id,
                'human_id': human_id, 'driver_token': driver_token,
            })
            return self.assert_success_case_file(runtime.root, yaml_path,
                                                 status=case_status)

        with patch('bcs_e2e.local_custom_collaboration_run.OwnedBcsProcess',
                   side_effect=fake_runtime), \
             patch('bcs_e2e.local_custom_collaboration_run.OwnedLocalFixtures',
                   side_effect=fake_fixtures), \
             patch('bcs_e2e.local_custom_collaboration_run.'
                   '_admit_owned_friend_pair',
                   side_effect=fake_admission), \
             patch('bcs_e2e.local_custom_collaboration_run.OwnedLocalBotWebSocket',
                   side_effect=fake_websocket), \
             patch('bcs_e2e.local_custom_collaboration_run.run_custom_collaboration_case',
                   side_effect=fake_case):
            result = local_custom_collaboration_run(
                binary=self.binary, binary_sha256=self.binary_sha,
                cli_binary=self.cli, cli_binary_sha256=self.cli_sha,
                state_dir=self.state_dir, run_id=self.run_id,
                confirm_run_id=self.run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
        return result, observed, admitted, websocket

    def test_authoring_yaml_matches_current_state_machine_contract(self):
        self.assertNotIn('outcomes:', DEFINITION_YAML)
        self.assertIn('instruction: Produce a concise release plan.', DEFINITION_YAML)
        self.assertIn('transitions:\n          complete:\n            targets: [review]', DEFINITION_YAML)
        self.assertIn('instruction: Review the plan and produce the final recommendation.',
                      DEFINITION_YAML)
        self.assertIn('final_output: true', DEFINITION_YAML)

    def test_success_provisions_exact_roles_publishes_and_clears_credential(self):
        result, observed, admitted, websocket = self.call_run()
        self.assertEqual(result['status'], SUCCESS_STATUS)
        self.assertEqual(observed['state_dir'], self.state_dir)
        self.assertEqual(observed['driver_id'], 'driver-bot')
        self.assertEqual(observed['reviewer_id'], 'member-bot')
        self.assertEqual(observed['human_id'], 'human_e2ehuman123')
        self.assertEqual(admitted, [('driver-bot', 'member-bot')])
        self.assertEqual(websocket, [(12345, 'driver-bot', 'owned-driver-token')])
        self.assertEqual(observed['driver_token'], 'owned-driver-token')
        self.assertEqual(observed['binary'], self.cli)
        self.assertEqual(observed['binary_sha256'], self.cli_sha)
        yaml_path = self.state_dir / DEFINITION_PATH
        self.assertEqual(observed['yaml_path'], yaml_path)
        self.assertTrue(yaml_path.is_file())
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        self.assertEqual(result['credentials']['state'], 'CLEARED')
        self.assertEqual(result['credentials']['bot_id'], 'driver-bot')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['shutdown']['returncode'], 0)
        self.assertTrue((self.state_dir / 'reports/summary.json').is_file())
        self.assertTrue((self.state_dir / 'reports/junit.xml').is_file())
        self.assertTrue((self.state_dir / 'reports/summary.md').is_file())
        for path in [self.state_dir / 'custom-collaboration-lifecycle-binding.json',
                     self.state_dir / 'custom-collaboration-lifecycle-flow-report.json',
                     self.state_dir / 'local-custom-collaboration.json',
                     self.state_dir / 'reports/summary.json']:
            self.assertNotIn(b'owned-driver-token', path.read_bytes())
        binding = json.loads(
            (self.state_dir / 'custom-collaboration-lifecycle-binding.json').read_text())
        self.assertEqual(binding['legacy_inventory'], self.raw_legacy)
        self.assertEqual(len(binding['legacy_inventory']), len(self.raw_legacy))
        self.assertTrue(
            KNOWN_UNMAPPED_LEGACY_IDS
            <= {row['id'] for row in binding['legacy_inventory']})
        canonical = build_custom_collaboration_report(self.state_dir)
        self.assertEqual(canonical['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(canonical['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(canonical['scope']['full_story_status'], 'LOCAL_INTEGRATED')
        self.assertEqual(len(canonical['requirements']), 37)

    def test_release_parent_guard_reads_reviewed_denominator_not_the_raw_freeze(self):
        without_parent = [{'id': f'other-{index}'} for index in range(31)]
        with patch('bcs_e2e.local_custom_collaboration_run.reviewed_release_inventory',
                   return_value=without_parent):
            with self.assertRaisesRegex(RunStateError, 'parent is missing'):
                self.call_run()

    def test_non_pass_case_is_blocked_and_never_gets_release_credit(self):
        result, observed, _, websocket = self.call_run(case_status='NOT_PASS')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['case']['status'], 'NOT_PASS')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['shutdown']['returncode'], 0)
        canonical = build_custom_collaboration_report(self.state_dir)
        self.assertEqual(canonical['status'], 'BLOCKED')
        self.assertEqual(canonical['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(canonical['scope']['full_story_status'], 'NOT_QUALIFIED')
        self.assertEqual(canonical['grants_release_credit'], False)
        self.assertNotIn(b'owned-driver-token',
                         (self.state_dir / 'custom-collaboration-lifecycle-flow-report.json').read_bytes())

    def test_offline_report_rejects_changed_evidence(self):
        self.call_run()
        fixtures = self.state_dir / 'local-fixtures.json'
        fixtures.write_bytes(fixtures.read_bytes() + b'\n')
        canonical = build_custom_collaboration_report(self.state_dir)
        self.assertEqual(canonical['status'], 'BLOCKED')
        self.assertEqual(canonical['integrity']['status'], 'BLOCKED')
        self.assertTrue(canonical['integrity']['issues'])
        # The original completion certificate remains available for diagnosis.
        completion = json.loads((self.state_dir / 'custom-collaboration-completion.json').read_text())
        self.assertEqual(set(completion['sources']), {
            'custom-collaboration-lifecycle-binding.json',
            'custom-collaboration-lifecycle-flow-report.json',
            'bcs-config.json', 'owned-bcs.json', 'local-fixtures.json',
            'run.json', 'custom-collaboration-definition.yaml',
            'local-custom-collaboration.json',
        })


if __name__ == '__main__':
    unittest.main()
