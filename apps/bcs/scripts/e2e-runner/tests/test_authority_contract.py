"""Frozen contract, drift fences and today-frozen surface shared facts."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.authority import contract, story
from bcs_e2e.inventory import (KNOWN_UNMAPPED_LEGACY_IDS, current_manifest,
                               load_executor_migration, reviewed_release_inventory)
from bcs_e2e.run_state import RunStateError

SOURCE_REVISION = contract.SOURCE_REVISION
SHA = contract.SOURCE_SHA256


class AuthorityContractTests(unittest.TestCase):
    def setUp(self):
        self.binding = contract.authority_source_binding()

    # ------------------------------------------------------------ frozen map
    def test_binding_freezes_the_29_key_machine_map(self):
        self.assertEqual(set(self.binding), {
            'schema_version', 'parent', 'suite', 'case_id', 'dual_case_id', 'dual_suite',
            'obligation_prefix', 'environment', 'sources', 'source_revision',
            'source_file_revisions', 'story_checks', 'story_markers', 'business_envelope',
            'registration_plane', 'principal', 'authority_constants', 'owned_runtime_binary',
            'cli_runtime_binary', 'authorization_note', 'redaction_tokens',
            'bootstrap_preconditions', 'requirements', 'remote_validation',
            'grants_release_credit', 'release_parent_denominator', 'independent_slice',
            'evidence_granularity', 'legacy_observer'})
        self.assertEqual(self.binding['schema_version'], 1)
        self.assertEqual(self.binding['parent'], 'story_authority_user_journey')
        self.assertEqual(self.binding['suite'], 'owned-local-authority-dual-v1')
        self.assertEqual(self.binding['case_id'], 'LOCAL-AUTHORITY-JOURNEY-02')
        self.assertEqual(self.binding['dual_case_id'], 'DUAL-AUTHORITY-JOURNEY-01')
        self.assertEqual(self.binding['dual_suite'], 'dual-authority-v1')
        self.assertEqual(self.binding['obligation_prefix'], 'AUTHORITY-JOURNEY.')
        self.assertEqual(self.binding['environment'],
                         {'local': 'LOCAL_EXECUTOR_ONLY',
                          'preprod': 'REMOTE_ADMISSION_GATED_READ_ONLY'})
        self.assertEqual(self.binding['sources'], {'authority.sh': str(contract.STORY_SOURCE)})
        self.assertEqual(Path(self.binding['sources']['authority.sh']).resolve(),
                         contract.STORY_SOURCE.resolve())
        self.assertEqual(self.binding['source_revision'], SOURCE_REVISION)
        self.assertEqual(self.binding['source_file_revisions'],
                         {'authority.sh': SOURCE_REVISION})
        self.assertEqual(self.binding['story_checks'], 42)
        self.assertEqual(self.binding['story_markers'],
                         ['Bot authority journey: {checks} live HTTP assertions passed'])
        self.assertEqual(self.binding['redaction_tokens'],
                         ['bot_token', 'X-Avernet-Principal', 'register?token', 'Bearer '])
        self.assertEqual(self.binding['remote_validation'], 'NOT_RUN')
        self.assertIs(self.binding['grants_release_credit'], False)
        self.assertIs(self.binding['release_parent_denominator'], False)
        self.assertIs(self.binding['independent_slice'], True)
        self.assertEqual(self.binding['evidence_granularity'], 'OWNED_CHILD_RECEIPT')

    def test_requirements_freeze_42_fine_grained_obligations_in_upstream_order(self):
        rows = self.binding['requirements']
        self.assertEqual(len(rows), 42)
        self.assertEqual([row['id'] for row in rows], list(contract.REQUIREMENTS))
        lines = contract.STORY_SOURCE.read_text(encoding='utf-8').splitlines()
        for row in rows:
            self.assertEqual(set(row), {'id', 'expectation', 'story_lines', 'anchors',
                                        'markers', 'primary'})
            self.assertEqual(row['markers'], ['authority-journey-compliance'])
            slug = row['id'][len(contract.OBLIGATION_PREFIX):]
            expected = contract.PRIMARY[slug]
            self.assertEqual((row['primary']['method'], row['primary']['path_template'],
                              row['primary']['status'], row['primary']['actor'],
                              row['primary']['query']), expected)
            span = row['story_lines']
            self.assertIsInstance(span, list)
            self.assertEqual(len(span), 2)
            self.assertLessEqual(span[0], span[1])
            for anchor in row['anchors']:
                self.assertEqual(set(anchor), {'line', 'literal'})
                self.assertGreaterEqual(anchor['line'], span[0])
                self.assertLessEqual(anchor['line'], span[1])
                self.assertIn(anchor['literal'], lines[anchor['line'] - 1])
        # ranges are minimal per-obligation windows: a status row cites the
        # upstream call plus its require_status assertion, value rows cite only
        # their own assertion lines; siblings sharing one upstream response stay
        # strictly ordered upstream and pairwise disjoint (anchors below prove
        # the shared call through the anchored lines)
        ranges = [tuple(row['story_lines']) for row in rows]
        self.assertEqual(len(set(ranges)), len(rows))
        for (start, end), following in zip(ranges, ranges[1:]):
            self.assertLess(end, following[0])
        self.assertGreaterEqual(ranges[0][0], 1)
        self.assertLessEqual(ranges[-1][1], len(lines))

    def test_authority_sh_is_the_frozen_upstream_source(self):
        digest = hashlib.sha256(contract.STORY_SOURCE.read_bytes()).hexdigest()
        self.assertEqual(digest, SHA)
        self.assertEqual(contract._git_tip_revision(), SOURCE_REVISION)
        self.assertEqual(self.binding['source_revision'], SOURCE_REVISION)
        text = contract.STORY_SOURCE.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('#!/bin/bash'))
        self.assertIsNotNone(
            re.search(r'^story_authority_user_journey\(\) \{$', text, re.M))
        self.assertLess(contract.STORY_SOURCE.stat().st_size, 1_048_576)

    def test_frozen_faces_binaries_and_constants(self):
        owned = self.binding['owned_runtime_binary']
        self.assertEqual(owned['package'], 'bcs')
        self.assertEqual(owned['sha256'], contract.BCS_BINARY_SHA256)
        self.assertEqual(len(contract.BCS_BINARY_SHA256), 64)
        self.assertIs(owned['auto_build'], False)
        self.assertEqual(owned['required_arguments'], ['--binary', '--binary-sha256'])
        self.assertEqual(owned['role'], 'owned-bcs-server')
        cli = self.binding['cli_runtime_binary']
        self.assertEqual(cli['package'], 'bcs-cli')
        self.assertEqual(cli['sha256'], contract.CLI_BINARY_SHA256)
        self.assertIs(cli['auto_build'], False)
        self.assertEqual(cli['required_arguments'], ['--cli-binary', '--cli-binary-sha256'])
        self.assertEqual(cli['role'], 'product-cli-readiness-gate-only')
        self.assertEqual(cli['authority_business_verbs'], 'none-frozen')
        principal = self.binding['principal']
        self.assertEqual(principal['algorithm'], 'HS256')
        self.assertEqual(principal['header'], 'X-Avernet-Principal')
        self.assertEqual(principal['audience'], 'bcs')
        self.assertEqual(principal['tenant'], 'bcs-e2e')
        self.assertEqual(principal['signing_key_env'],
                         contract.PRINCIPAL_SIGNING_KEY_ENV)
        self.assertIs(principal['minted_fresh_per_business_request'], True)
        envelope = self.binding['business_envelope']
        self.assertEqual(envelope['required_keys'],
                         ['code', 'message', 'request_id', 'data'])
        self.assertIs(envelope['counted'], False)
        plane = self.binding['registration_plane']
        self.assertEqual(plane['mint_path'], '/register/token')
        self.assertEqual(plane['register_path'], '/register')
        self.assertEqual(plane['register_query'], ['token', 'bot-name'])
        self.assertEqual(plane['admission_headers'], ['X-Mock-User-Id', 'X-Mock-Nick-Name'])
        self.assertEqual(plane['bots_committed'], 2)
        self.assertTrue(plane['mock_headers_forbidden_on_business_paths'])
        constants = self.binding['authority_constants']
        self.assertEqual(constants['target_user_id'], '002')
        self.assertEqual(constants['mine_user_id'], '137')
        self.assertEqual(constants['http_call_count'], 24)
        self.assertEqual(constants['bootstrap_http_calls'], 4)
        self.assertEqual(constants['business_http_calls'], 20)
        self.assertEqual(constants['transfer_window_max_renewal_ms'], 300000)
        self.assertEqual(constants['expires_at_unit'], 'epoch-milliseconds')
        self.assertEqual(self.binding['authorization_note'],
                         'Preprod admission resolves only named environment references '
                         'BCS_E2E_DRIVER_TOKEN and BCS_E2E_HUMAN_COOKIE from the live '
                         'process environment; the frozen preprod column is '
                         'REMOTE_ADMISSION_GATED_READ_ONLY, the mutating journey never '
                         'executes against preprod in this slice, no leg stores a '
                         'credential, and no release credit is granted.')

    # ----------------------------------------------------- today-frozen facts
    def test_today_frozen_review_and_executor_migration_facts(self):
        reviewed = reviewed_release_inventory()
        self.assertEqual(len(reviewed), 31)
        self.assertIn(contract.PARENT, {row['id'] for row in reviewed})
        self.assertNotIn(contract.PARENT, KNOWN_UNMAPPED_LEGACY_IDS)
        self.assertEqual(load_executor_migration()[contract.PARENT],
                         {'mode': 'DUAL', 'preprod_verdict': 'NOT_RUN',
                          'gates': [],
                          'notes': ['AUTHORITY_DENSITY_42_OBLIGATIONS_20261031']})
        legacy_ids = [row['id'] for row in current_manifest()['legacy_inventory']]
        self.assertEqual(legacy_ids.count(contract.PARENT), 1)

    def test_legacy_16_obligation_journal_map_stays_observer_only(self):
        legacy = json.loads((contract.ROOT / 'authority-migration-contract.json')
                            .read_text(encoding='utf-8'))
        ids = [row['id'] for row in legacy['requirements']]
        self.assertEqual(len(ids), 16)
        self.assertTrue(all(obligation.startswith('AUTHORITY.') for obligation in ids))
        self.assertEqual(legacy['case_id'], 'LOCAL-AUTHORITY-JOURNEY-01')
        # the dual slice never mutates the legacy journal map
        self.assertNotIn('DUAL-AUTHORITY-JOURNEY-01',
                         json.dumps(legacy['requirements']))

    def test_dual_contract_json_file_is_the_binding_source(self):
        raw = json.loads((contract.ROOT / contract.CONTRACT_NAME)
                         .read_text(encoding='utf-8'))
        self.assertEqual(set(raw), set(self.binding))
        self.assertEqual(len(raw['requirements']), 42)
        self.assertEqual(raw['dual_case_id'], contract.DUAL_CASE_ID)
        self.assertEqual(raw['dual_suite'], contract.DUAL_SUITE)
        self.assertEqual(raw['environment'], contract.ENVIRONMENT)

    def test_story_module_and_contract_stay_count_consistent(self):
        self.assertEqual(contract.STORY_CHECKS, 42)
        self.assertEqual(story.EXPECTED_CHECKS, 42)
        self.assertEqual(len(contract.REQUIREMENTS), 42)
        self.assertEqual(story.expectation_rows() if hasattr(story, 'expectation_rows')
                         else 42, 42)

    # ------------------------------------------------------------ tamper map
    def test_source_binding_fail_closes_on_every_frozen_fact_drift(self):
        def mutate_row(value, index, key, payload):
            row = copy.deepcopy(value['requirements'][index])
            row[key] = payload
            value['requirements'][index] = row
            return value

        def replace(value, key, payload):
            value[key] = payload
            return value

        cases = (
            ('top-key-dropped', lambda v: v.pop('principal')),
            ('top-key-added', lambda v: v.__setitem__('extra', 1)),
            ('schema-version', lambda v: replace(v, 'schema_version', 2)),
            ('parent-id', lambda v: replace(v, 'parent', 'story_other')),
            ('suite', lambda v: replace(v, 'suite', 'owned-local-authority-v1')),
            ('case-id-legacy-resync', lambda v: replace(v, 'case_id', 'LOCAL-AUTHORITY-JOURNEY-01')),
            ('dual-case-id', lambda v: replace(v, 'dual_case_id', 'AUTHORITY-JOURNEY-01')),
            ('dual-suite', lambda v: replace(v, 'dual_suite', 'authority-dual-v1')),
            ('obligation-prefix', lambda v: replace(v, 'obligation_prefix', 'AUTHORITY.')),
            ('environment-column', lambda v: replace(
                v, 'environment', {'local': 'LOCAL_EXECUTOR_ONLY',
                                   'preprod': 'REMOTE_UNSUPPORTED'})),
            ('sources-add-file', lambda v: replace(
                v, 'sources', dict(v['sources'], other='x'))),
            ('source-revision', lambda v: replace(v, 'source_revision', '0' * 40)),
            ('source-file-revisions', lambda v: replace(
                v, 'source_file_revisions', {'authority.sh': '0' * 40})),
            ('story-checks-41', lambda v: replace(v, 'story_checks', 41)),
            ('story-markers', lambda v: replace(v, 'story_markers', ['other marker'])),
            ('redaction-tokens', lambda v: replace(
                v, 'redaction_tokens', list(contract.REDACTION_TOKENS) + ['Cookie '])),
            ('requirements-order', lambda v: v.__setitem__(
                'requirements', [v['requirements'][1], v['requirements'][0]]
                + v['requirements'][2:])),
            ('requirement-shape', lambda v: mutate_row(v, 0, 'extra', 1)),
            ('anchor-out-of-range', lambda v: mutate_row(
                v, 0, 'anchors', [{'line': 999999, 'literal': 'never'}])),
            ('anchor-literal', lambda v: mutate_row(
                v, 0, 'anchors', [{'line': v['requirements'][0]['story_lines'][0],
                                   'literal': 'definitely not an upstream literal'}])),
            ('story-lines-order', lambda v: mutate_row(v, 1, 'story_lines', [300, 130])),
            ('story-lines-range', lambda v: mutate_row(v, 1, 'story_lines', [1, 999999])),
            ('primary-method', lambda v: mutate_row(v, 2, 'primary', dict(
                v['requirements'][2]['primary'], method='DELETE'))),
            ('primary-status', lambda v: mutate_row(v, 2, 'primary', dict(
                v['requirements'][2]['primary'], status=201))),
            ('primary-path', lambda v: mutate_row(v, 2, 'primary', dict(
                v['requirements'][2]['primary'], path_template='/api/v1/other'))),
            ('business-envelope-keys', lambda v: replace(
                v, 'business_envelope', dict(
                    v['business_envelope'], required_keys=['code', 'message']))),
            ('registration-bots', lambda v: replace(
                v, 'registration_plane', dict(
                    v['registration_plane'], bots_committed=3))),
            ('principal-algorithm', lambda v: replace(
                v, 'principal', dict(v['principal'], algorithm='RS256'))),
            ('owned-binary-sha', lambda v: replace(
                v, 'owned_runtime_binary', dict(
                    v['owned_runtime_binary'], sha256='0' * 64))),
            ('cli-role', lambda v: replace(
                v, 'cli_runtime_binary', dict(
                    v['cli_runtime_binary'], role='owned-bcs-server'))),
            ('cli-required-arguments', lambda v: replace(
                v, 'cli_runtime_binary', dict(
                    v['cli_runtime_binary'],
                    required_arguments=['--binary', '--binary-sha256', '--extra']))),
            ('bootstrap-literal', lambda v: v.__setitem__(
                'bootstrap_preconditions', [dict(v['bootstrap_preconditions'][0],
                                                literal='not an upstream line')])),
            ('authority-constants-target', lambda v: replace(
                v, 'authority_constants', dict(
                    v['authority_constants'], target_user_id='003'))),
            ('authority-constants-transfer-window', lambda v: replace(
                v, 'authority_constants', dict(
                    v['authority_constants'], transfer_window_max_renewal_ms=299999))),
            ('legacy-obligations', lambda v: replace(
                v, 'legacy_observer', dict(v['legacy_observer'], obligations=17))),
            ('legacy-case-id', lambda v: replace(
                v, 'legacy_observer', dict(
                    v['legacy_observer'], case_id='LOCAL-AUTHORITY-JOURNEY-02'))),
            ('remote-validation', lambda v: replace(v, 'remote_validation', 'PASS')),
            ('release-credit', lambda v: replace(v, 'grants_release_credit', True)),
            ('release-parent-denominator', lambda v: replace(
                v, 'release_parent_denominator', True)),
            ('independent-slice', lambda v: replace(v, 'independent_slice', False)),
            ('evidence-granularity', lambda v: replace(
                v, 'evidence_granularity', 'OWNED_JOURNAL_LINE')),
        )
        for name, mutate in cases:
            with self.subTest(mutation=name):
                mutated = mutate(copy.deepcopy(self.binding))
                with patch('bcs_e2e.authority.contract.read', return_value=mutated):
                    with self.assertRaises(RunStateError):
                        contract.authority_source_binding()
        # unmutated map under the same patched read stays the positive control
        with patch('bcs_e2e.authority.contract.read', return_value=copy.deepcopy(self.binding)):
            self.assertEqual(contract.authority_source_binding()['story_checks'], 42)

    def test_upstream_source_byte_drift_fail_closes(self):
        # Patching only the frozen constant makes the map's pinned source path
        # disagree first, so the map/path-identity fence raises the generic
        # binding-changed RunStateError — still fail-closed before anything runs.
        with tempfile.TemporaryDirectory() as temp:
            tampered = Path(temp) / 'authority.sh'
            tampered.write_text(contract.STORY_SOURCE.read_text(encoding='utf-8') + ' ',
                                encoding='utf-8')
            with patch('bcs_e2e.authority.contract.STORY_SOURCE', tampered):
                with self.assertRaises(RunStateError):
                    contract.authority_source_binding()
            # Re-pinning the map to the tampered file passes the path-identity
            # fence, so the byte drift itself reaches the dedicated sha fence.
            drifted = copy.deepcopy(self.binding)
            drifted['sources'] = {'authority.sh': str(tampered)}
            with patch('bcs_e2e.authority.contract.STORY_SOURCE', tampered), \
                    patch('bcs_e2e.authority.contract.read',
                          return_value=drifted):
                with self.assertRaisesRegex(RunStateError, 'source drift'):
                    contract.authority_source_binding()

    def test_upstream_git_tip_drift_fail_closes(self):
        with patch('bcs_e2e.authority.contract._git_tip_revision',
                   return_value='0' * 40):
            with self.assertRaisesRegex(RunStateError, 'git tip drift'):
                contract.authority_source_binding()


if __name__ == '__main__':
    unittest.main()
