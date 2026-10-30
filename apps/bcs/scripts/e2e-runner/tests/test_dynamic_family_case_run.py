"""Focused offline tests for the four dynamic owned-local dual family runners.

Covers the shared contract surface of every family: strict profile admission,
fresh private state directory, frozen binary digest admission, owned-local
PASS receipts, honest preprod BLOCKED_INVENTORIED gap receipts, and the four
CLI wrapper delegations.  No network, no credentials, no repository writes
outside a per-test temporary directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bcs_e2e.dynamic_family import case_run as dynamic_case_run
from bcs_e2e.dynamic_family.case_run import (
    DUAL_DRAFT_GROUP_COMMAND,
    DUAL_DYNAMIC_FAMILY_CASE_KEYS,
    DUAL_FIXED_LOOP_COMMAND,
    DUAL_NESTED_FIXED_LOOP_COMMAND,
    DUAL_RESULT_QUEUE_COMMAND,
    dual_draft_group_case_run,
    dual_dynamic_family_case_run,
    dual_fixed_loop_case_run,
    dual_nested_fixed_loop_case_run,
    dual_result_queue_case_run,
)
from bcs_e2e.dynamic_family.contract import dynamic_family_contract, dynamic_families
from bcs_e2e.dynamic_family.profile import load_dynamic_family_profile
from bcs_e2e.profile import ConfigError

COMMAND_FAMILY = {
    'fixed_loop': 'fixed-loop',
    'nested_fixed_loop': 'nested-fixed-loop',
    'result_queue': 'result-queue',
    'draft_group': 'draft-group',
}
STATION_ROUTES = {
    'fixed_loop': ['legacy-groups', 'legacy-state-machine-runs', 'legacy-sessions'],
    'nested_fixed_loop': ['legacy-groups', 'legacy-state-machine-runs', 'legacy-graph-block'],
    'result_queue': ['legacy-groups', 'legacy-state-machine-runs', 'unregistered-admin-policy'],
    'draft_group': ['legacy-groups', 'legacy-bots-mine', 'legacy-draft-face'],
}
LOCAL_RUNNER_SEAMS = {
    'fixed_loop': 'local_fixed_loop_run',
    'nested_fixed_loop': 'local_nested_fixed_loop_run',
    'result_queue': 'local_result_queue_run',
    'draft_group': 'local_draft_group_run',
}
BINARY_VALIDATOR_SEAMS = {
    'fixed_loop': 'validate_fixed_loop_binary',
    'nested_fixed_loop': 'validate_nested_fixed_loop_binary',
    'result_queue': 'validate_result_queue_binary',
    'draft_group': 'validate_draft_group_binary',
}


def _prefix(family: str) -> str:
    return 'dynamic-family-' + family + '-'


def _write_json(directory: Path, name: str, value: dict) -> Path:
    path = directory / name
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def _binary_fixture(directory: Path) -> tuple[Path, str]:
    path = directory / 'bcs-binary-fixture'
    path.write_bytes(b'dual dynamic family binary fixture\n')
    path.chmod(0o755)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _cli_fixture(directory: Path) -> Path:
    path = directory / 'bcs-cli-fixture'
    path.write_bytes(b'dual dynamic family cli fixture\n')
    path.chmod(0o755)
    return path


def _profile(family: str, tier: str) -> dict:
    contract = dynamic_family_contract(family)
    command_family = COMMAND_FAMILY[family]
    if tier == 'local':
        environment = {'tier': 'local', 'id': 'singlebox', 'expected_bcs_env': 'local'}
        entrypoint = {'kind': 'owned-local'}
        auth = {'kind': 'owned-local'}
        cli = {'transport': 'owned-local-story', 'command_family': command_family,
               'api_face': 'owned-local'}
    else:
        environment = {'tier': 'preprod', 'id': 'preprod', 'expected_bcs_env': 'pre'}
        entrypoint = {'kind': 'bcs-direct', 'origin': 'https://bcn-pre.teamclaw.example',
                      'api_face': 'legacy', 'manifest_face': 'legacy'}
        auth = {'kind': 'preprod-human-cookie', 'header_env': {
            'Authorization': 'BCS_E2E_DRIVER_TOKEN', 'Cookie': 'BCS_E2E_HUMAN_COOKIE'}}
        cli = {'transport': 'station-gap-receipt', 'command_family': command_family,
               'api_face': 'legacy'}
    return {
        'schema_version': 1,
        'contract': contract.profile_contract,
        'family': family,
        'environment': environment,
        'entrypoint': entrypoint,
        'human': {'user_id': '39464'},
        'auth': auth,
        'cli': cli,
        'policy': {'fixture_pool': 'dual-dynamic-family-test-pool',
                   'exclusive_use_confirmed': True, 'timeout_seconds': 10,
                   'max_response_bytes': 8192, 'failure_retention_hours': 72},
        'station_routes': list(STATION_ROUTES[family]),
    }


def _local_pass_result(family: str) -> dict:
    contract = dynamic_family_contract(family)
    return {
        'status': contract.local_status,
        'canonical_summary': {'status': contract.canonical_status},
        'case': {'status': 'PASS', 'case_id': contract.source_case_id,
                 'required_assertion_count': len(contract.assertion_ids),
                 'assertions': [{'id': site, 'status': 'PASS'}
                                for site in contract.assertion_ids]},
    }


class DynamicFamilyCaseRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._pass_result = None

    def _patch_seams(self, family: str):
        patches = [
            mock.patch.object(dynamic_case_run, 'executor_digest',
                              return_value='test-executor-digest'),
            mock.patch.object(dynamic_case_run, 'validate_nested_fixed_loop_cli_binary',
                              side_effect=lambda value: Path(value)),
        ]
        for seam in LOCAL_RUNNER_SEAMS.values():
            patches.append(mock.patch.object(dynamic_case_run, seam,
                                             return_value=self._pass_result))
        for seam in BINARY_VALIDATOR_SEAMS.values():
            patches.append(mock.patch.object(
                dynamic_case_run, seam, side_effect=lambda value, digest: Path(value)))
        return patches

    def _run(self, family: str, tier: str, **overrides):
        self._pass_result = _local_pass_result(family)
        profile_path = _write_json(self.root, 'profile-' + family + '-' + tier + '.json',
                                   _profile(family, tier))
        state_dir = self.root / ('state-' + family + '-' + tier)
        binary, digest = _binary_fixture(self.root)
        run_id = 'dual-family-' + family.replace('_', '-') + '-r1'
        kwargs = {
            'family': family, 'profile_path': profile_path, 'state_dir': state_dir,
            'run_id': run_id, 'allow_write': True, 'confirm_run_id': run_id,
            'binary': binary, 'binary_sha256': digest,
        }
        kwargs.update(overrides)
        patches = self._patch_seams(family)
        for patch in patches:
            patch.start()
        try:
            return dual_dynamic_family_case_run(**kwargs), state_dir
        finally:
            for patch in patches:
                patch.stop()

    def test_command_constants_and_case_keys(self):
        self.assertEqual(DUAL_FIXED_LOOP_COMMAND, 'dual-fixed-loop-run')
        self.assertEqual(DUAL_NESTED_FIXED_LOOP_COMMAND, 'dual-nested-fixed-loop-run')
        self.assertEqual(DUAL_RESULT_QUEUE_COMMAND, 'dual-result-queue-run')
        self.assertEqual(DUAL_DRAFT_GROUP_COMMAND, 'dual-draft-group-run')
        self.assertEqual(
            DUAL_DYNAMIC_FAMILY_CASE_KEYS,
            frozenset({'fixed-loop', 'nested-fixed-loop', 'result-queue', 'draft-group'}))

    def test_contracts_complete_and_traceable(self):
        for contract in dynamic_families():
            self.assertTrue(contract.requirement_ids)
            self.assertTrue(contract.assertion_ids)
            self.assertEqual(set(contract.assertion_requirement_ids), set(contract.assertion_ids))
            for site, owners in contract.assertion_requirement_ids.items():
                self.assertTrue(owners)
                for owner in owners:
                    self.assertIn(owner, contract.requirement_ids)
            self.assertTrue(contract.preprod_gaps)
            for gap in contract.preprod_gaps:
                self.assertTrue(gap and gap == gap.upper() and ' ' not in gap)
            self.assertEqual(contract.local_status, contract.local_status.upper())

    def test_local_leg_pass_receipt(self):
        for family in STATION_ROUTES:
            with self.subTest(family=family):
                overrides = {}
                if family == 'nested_fixed_loop':
                    overrides['cli_binary'] = _cli_fixture(self.root)
                    overrides['driver'] = '20261026_xq102n2b:39464'
                report, state_dir = self._run(family, 'local', **overrides)
                contract = dynamic_family_contract(family)
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['case']['status'], 'PASS')
                self.assertEqual(report['case']['case_id'], contract.case_id)
                self.assertEqual(report['execution']['status'], 'PASS')
                self.assertEqual(report['execution']['completed_count'],
                                 len(contract.requirement_ids))
                self.assertEqual(report['execution']['total_count'],
                                 len(contract.requirement_ids))
                self.assertEqual(report['obligation_summary']['expected_assertion_count'],
                                 len(contract.assertion_ids))
                self.assertEqual(report['obligation_summary']['assertion_projection'],
                                 contract.traceability())
                self.assertEqual(report['documented_gaps'], [])
                self.assertEqual(report['preprod_verdict'], 'NOT_RUN')
                self.assertEqual(report['remote_validation'], 'NOT_RUN')
                self.assertFalse(report['grants_release_credit'])
                self.assertEqual(report['credential_kind'], 'owned-local-ephemeral')
                self.assertEqual(report['local_owned_run']['status'], contract.local_status)
                self.assertEqual(len(report['station_route_gates']),
                                 len(STATION_ROUTES[family]))
                for gate in report['station_route_gates']:
                    self.assertEqual(gate['status'], 'NOT_RUN_OFFLINE')
                self.assertEqual(stat.S_IMODE(Path(report['state_dir']).stat().st_mode) & 0o777,
                                 0o700)

    def test_local_leg_writes_profile_binding_and_report(self):
        family = 'fixed_loop'
        report, state_dir = self._run(family, 'local')
        contract = dynamic_family_contract(family)
        prefix = _prefix(family)
        profile_file = state_dir / (prefix + 'profile.json')
        binding_file = state_dir / (prefix + 'binding.json')
        report_file = state_dir / (prefix + 'dual-run.json')
        for path in (profile_file, binding_file, report_file):
            self.assertTrue(path.is_file(), path)
        saved_profile = json.loads(profile_file.read_text(encoding='utf-8'))
        self.assertEqual(saved_profile['family'], family)
        self.assertEqual(saved_profile['environment']['tier'], 'local')
        binding = json.loads(binding_file.read_text(encoding='utf-8'))
        self.assertEqual(binding['case_id'], contract.case_id)
        self.assertEqual(binding['source_case_id'], contract.source_case_id)
        self.assertEqual(binding['family'], family)
        self.assertEqual(binding['expected_assertion_count'], len(contract.assertion_ids))
        self.assertEqual(binding['contract'], contract.report_contract)
        self.assertEqual(binding['requirement_ids'], list(contract.requirement_ids))
        written = json.loads(report_file.read_text(encoding='utf-8'))
        self.assertEqual(written['status'], 'PASS')
        self.assertEqual(written['case']['case_id'], contract.case_id)

    def test_preprod_leg_is_honest_blocked_gap_receipt(self):
        family = 'fixed_loop'
        report, state_dir = self._run(family, 'preprod')
        contract = dynamic_family_contract(family)
        self.assertEqual(report['status'], 'BLOCKED_INVENTORIED')
        self.assertEqual(report['case']['status'], 'BLOCKED')
        self.assertEqual(report['documented_gaps'], list(contract.preprod_gaps))
        self.assertEqual(report['preprod_verdict'], 'NOT_RUN')
        self.assertEqual(report['remote_validation'], 'NOT_RUN')
        self.assertFalse(report['grants_release_credit'])
        self.assertEqual(report['credential_kind'], 'environment-reference-name-only')
        self.assertNotIn('local_owned_run', report)
        gap_path = Path(report['preprod_gap_receipt'])
        self.assertTrue(gap_path.is_file(), gap_path)
        gap = json.loads(gap_path.read_text(encoding='utf-8'))
        self.assertEqual(gap['status'], 'BLOCKED_INVENTORIED')
        self.assertEqual(gap['preprod_verdict'], 'NOT_RUN')
        self.assertEqual(gap['documented_gaps'], list(contract.preprod_gaps))
        self.assertEqual(gap['station_routes'], list(STATION_ROUTES[family]))
        self.assertEqual(gap['auth_face']['header_reference_names_only'],
                         ['BCS_E2E_DRIVER_TOKEN', 'BCS_E2E_HUMAN_COOKIE'])
        self.assertEqual(gap['expected_assertion_count'], len(contract.assertion_ids))
        self.assertFalse(gap['grants_release_credit'])

    def test_preprod_leg_writes_only_gap_receipt(self):
        family = 'draft_group'
        _report, state_dir = self._run(family, 'preprod')
        written = sorted(path.name for path in state_dir.iterdir())
        prefix = _prefix(family)
        self.assertEqual(written, sorted([
            prefix + 'profile.json', prefix + 'binding.json',
            prefix + 'dual-run.json', prefix + 'preprod-gap.json']))

    def test_unsupported_family_is_rejected(self):
        with self.assertRaises(ConfigError):
            dual_dynamic_family_case_run(
                family='unknown_family', profile_path=self.root / 'unused.json',
                state_dir=self.root / 'unused-state', run_id='dual-family-unknown-r1',
                allow_write=True, confirm_run_id='dual-family-unknown-r1',
                binary=self.root / 'unused-binary', binary_sha256='0' * 64)

    def test_write_permission_and_run_confirmation_gates(self):
        family = 'fixed_loop'
        profile_path = _write_json(self.root, 'gate-profile.json',
                                   _profile(family, 'local'))
        binary, digest = _binary_fixture(self.root)
        run_id = 'dual-family-fixed-loop-r1'
        base = dict(family=family, profile_path=profile_path,
                    state_dir=self.root / 'gate-state', run_id=run_id,
                    binary=binary, binary_sha256=digest)
        with self.assertRaises(ConfigError):
            dual_dynamic_family_case_run(allow_write=False, confirm_run_id=run_id, **base)
        with self.assertRaises(ConfigError):
            dual_dynamic_family_case_run(allow_write=True, confirm_run_id='other-run', **base)

    def test_fresh_private_state_directory_gates(self):
        family = 'fixed_loop'
        profile_path = _write_json(self.root, 'fresh-profile.json',
                                   _profile(family, 'local'))
        binary, digest = _binary_fixture(self.root)
        run_id = 'dual-family-fixed-loop-r1'
        base = dict(family=family, profile_path=profile_path, run_id=run_id,
                    allow_write=True, confirm_run_id=run_id, binary=binary,
                    binary_sha256=digest)
        existing = self.root / 'existing-state'
        existing.mkdir()
        with self.assertRaises(ConfigError):
            dual_dynamic_family_case_run(state_dir=existing, **base)
        with self.assertRaises(ConfigError):
            dual_dynamic_family_case_run(state_dir=Path('relative-state'), **base)
        with self.assertRaises(ConfigError):
            dual_dynamic_family_case_run(
                state_dir=self.root / 'missing-parent' / 'state', **base)

    def test_binary_digest_admission(self):
        family = 'fixed_loop'
        profile_path = _write_json(self.root, 'digest-profile.json',
                                   _profile(family, 'local'))
        binary, digest = _binary_fixture(self.root)
        wrong = hashlib.sha256(b'other binary content\n').hexdigest()
        run_id = 'dual-family-fixed-loop-r1'
        base = dict(family=family, profile_path=profile_path, run_id=run_id,
                    allow_write=True, confirm_run_id=run_id, binary=binary)
        with self.assertRaisesRegex(ConfigError, 'digest differs'):
            dual_dynamic_family_case_run(state_dir=self.root / 'digest-state-a',
                                         binary_sha256=wrong, **base)
        with self.assertRaisesRegex(ConfigError, 'digest is invalid'):
            dual_dynamic_family_case_run(state_dir=self.root / 'digest-state-b',
                                         binary_sha256='tooshort', **base)

    def test_validator_rejection_maps_to_config_error(self):
        family = 'fixed_loop'
        self._pass_result = _local_pass_result(family)
        profile_path = _write_json(self.root, 'admission-profile.json',
                                   _profile(family, 'local'))
        binary, digest = _binary_fixture(self.root)
        patches = self._patch_seams(family)
        for patch in patches:
            patch.start()
        try:
            with mock.patch.object(dynamic_case_run, 'validate_fixed_loop_binary',
                                   side_effect=RuntimeError('binary quarantine')):
                with self.assertRaisesRegex(ConfigError, 'offline admission'):
                    dual_dynamic_family_case_run(
                        family=family, profile_path=profile_path,
                        state_dir=self.root / 'admission-state',
                        run_id='dual-family-fixed-loop-r1', allow_write=True,
                        confirm_run_id='dual-family-fixed-loop-r1', binary=binary,
                        binary_sha256=digest)
        finally:
            for patch in patches:
                patch.stop()

    def test_nested_local_requires_cli_binary_and_driver(self):
        family = 'nested_fixed_loop'
        self._pass_result = _local_pass_result(family)
        profile_path = _write_json(self.root, 'nested-gate-profile.json',
                                   _profile(family, 'local'))
        binary, digest = _binary_fixture(self.root)
        run_id = 'dual-nested-fixed-loop-r1'
        base = dict(family=family, profile_path=profile_path, run_id=run_id,
                    allow_write=True, confirm_run_id=run_id, binary=binary,
                    binary_sha256=digest)
        patches = self._patch_seams(family)
        for patch in patches:
            patch.start()
        try:
            # no driver and no CLI binary declared
            with self.assertRaisesRegex(ConfigError, 'driver Bot ID'):
                dual_dynamic_family_case_run(state_dir=self.root / 'nested-gate-a', **base)
            # driver declared but the product CLI binary does not exist
            with self.assertRaisesRegex(ConfigError, 'product CLI binary'):
                dual_dynamic_family_case_run(
                    state_dir=self.root / 'nested-gate-b',
                    driver='20261026_xq102n2b:39464',
                    cli_binary=self.root / 'missing-cli-binary', **base)
        finally:
            for patch in patches:
                patch.stop()

    def test_wrappers_delegate_all_four_families(self):
        self._pass_result = _local_pass_result('fixed_loop')
        binary, digest = _binary_fixture(self.root)
        cli_binary = _cli_fixture(self.root)
        wrappers = (
            ('fixed_loop', dual_fixed_loop_case_run, {}),
            ('nested_fixed_loop', dual_nested_fixed_loop_case_run,
             {'cli_binary': cli_binary, 'driver': '20261026_xq102n2b:39464'}),
            ('result_queue', dual_result_queue_case_run, {}),
            ('draft_group', dual_draft_group_case_run, {}),
        )
        for family, wrapper, extra in wrappers:
            with self.subTest(family=family):
                self._pass_result = _local_pass_result(family)
                profile_path = _write_json(self.root, 'wrap-' + family + '.json',
                                           _profile(family, 'local'))
                run_id = 'dual-family-' + family.replace('_', '-') + '-wrap-r1'
                state_dir = self.root / ('wrap-state-' + family)
                kwargs = dict(profile_path=profile_path, state_dir=state_dir, run_id=run_id,
                              allow_write=True, confirm_run_id=run_id, binary=binary,
                              binary_sha256=digest)
                kwargs.update(extra)
                patches = self._patch_seams(family)
                for patch in patches:
                    patch.start()
                try:
                    report = wrapper(**kwargs)
                finally:
                    for patch in patches:
                        patch.stop()
                contract = dynamic_family_contract(family)
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['family'], family)
                self.assertEqual(report['case']['case_id'], contract.case_id)
                self.assertEqual(report['execution']['completed_count'],
                                 len(contract.requirement_ids))

    def test_profile_admission_is_strict(self):
        good = _profile('fixed_loop', 'local')

        def _bad(name, mutate):
            value = json.loads(json.dumps(good))
            mutate(value)
            return _write_json(self.root, name, value)

        with self.assertRaises(ConfigError):
            load_dynamic_family_profile(_bad(
                'bad-station.json', lambda v: v.update(station_routes=['legacy-groups'])))
        with self.assertRaises(ConfigError):
            load_dynamic_family_profile(_bad(
                'extra-key.json', lambda v: v.update(undeclared_key=True)))
        with self.assertRaises(ConfigError):
            load_dynamic_family_profile(_bad(
                'bad-family.json', lambda v: v.update(family='unknown_family')))
        with self.assertRaises(ConfigError):
            load_dynamic_family_profile(_bad(
                'bad-tier.json',
                lambda v: v['environment'].update(tier='staging')))
        preprod = _profile('result_queue', 'preprod')
        preprod['entrypoint']['origin'] = 'http://bcn-pre.teamclaw.example'
        with self.assertRaises(ConfigError):
            load_dynamic_family_profile(
                _write_json(self.root, 'bad-origin.json', preprod))
        missing = self.root / 'does-not-exist.json'
        with self.assertRaises(ConfigError):
            load_dynamic_family_profile(missing)


if __name__ == '__main__':
    unittest.main()
