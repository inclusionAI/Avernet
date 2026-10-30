"""Guards for the frozen group-workspace density contract and its projector.

Covers: builder reproducibility and anchor-drift rejection, committed-contract
shape and source-hash pinning, full-table PASS projection, the NOT_RUN/FAIL/
INCOMPLETE/BLOCKED honesty rules, dual settings forms, any_of predicates, the
delete not-found state, and credential-free bindings (including the closed
identity-role snapshot path and the retained-fixture fallback).
"""
import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.group_workspace_density import (
    ATOM_COUNT,
    CONTRACT_PATH,
    FAMILIES,
    JOURNALS,
    VALID_STATUS,
    density_summary,
    load_group_workspace_density_contract,
    project_density,
    validate_group_workspace_density_contract,
    workspace_density_bindings,
)
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import RunStateError
import bcs_e2e.build_group_workspace_density as builder

E2E_TEST_ROOT = ROOT.parent / 'e2e-test'

IDS = {'primary': 'grp-owned-primary-0001',
       'members': 'grp-owned-members-0002',
       'cli_primary': 'grp-cli-primary-0003',
       'cli_termination': 'grp-cli-termination-0004',
       'incident': 'grp-incident-0005'}
ROLES = {'CEO': 'bot-ceo-0001', 'PM': 'bot-pm-0002',
         'ENG': 'bot-eng-0003', 'QA': 'bot-qa-0004'}


class _Roles:
    """Minimal role-identity face; role_bot_ids must never expose tokens."""

    def __init__(self, mapping):
        self._mapping = mapping

    def role_bot_ids(self):
        return dict(self._mapping)


class _ClosedRoles:
    """Simulates the identity context after close() cleared its entries."""

    def role_bot_ids(self):
        return {}


def _resolve(expected):
    if isinstance(expected, dict) and set(expected) == {'binding'}:
        dotted = expected['binding']
        if dotted.startswith('roles.'):
            return ROLES[dotted.split('.', 1)[1]]
        return IDS[dotted]
    return expected


def _ensure_op(journal, operation):
    for event in journal.setdefault('operations', []):
        if isinstance(event, dict) and event.get('operation') == operation:
            return event
    event = {'operation': operation, 'status': 'ACKNOWLEDGED'}
    journal['operations'].append(event)
    return event


def _set(event, dotted, value):
    keys = dotted.split('.')
    target = event
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value


def _satisfy(event, check):
    kind = check.get('type')
    if kind == 'status_eq':
        event['http_status'] = check['value']
    elif kind == 'field_eq':
        _set(event, check['path'], _resolve(check['expected']))
    elif kind == 'field_not_empty':
        _set(event, check['path'], 'observed-non-empty')
    elif kind == 'field_len_eq':
        _set(event, check['path'], ['x'] * check['value'])
    elif kind == 'field_ge':
        _set(event, check['path'], check['value'])
    elif kind == 'exit_zero':
        event['cli_exit_code'] = 0
    elif kind == 'stdout_contains':
        event['stdout'] = (event.get('stdout') or '') + str(_resolve(check['value'])) + '\n'
    elif kind == 'stdout_contains_any':
        event['stdout'] = (event.get('stdout') or '') + str(check['values'][0]) + '\n'
    elif kind == 'stdout_regex':
        event['stdout'] = (event.get('stdout') or '') + '  ID: ' + IDS['cli_primary'] + '\n'
    elif kind == 'any_of':
        _satisfy(event, check['checks'][-1])
    elif kind in ('settings_retired_form', 'settings_policy_form'):
        event['http_status'] = 410
        event['value'] = {'error': 'service_policy_retired'}
    elif kind in ('read_present', 'read_field_eq', 'read_field_not_empty',
                  'read_field_len_eq', 'read_field_ge', 'not_found_state'):
        pass
    else:
        raise AssertionError('unhandled predicate in synthesizer: ' + str(kind))


def _resolve_read_path(template):
    if template == '/groups?list-all':
        return '/groups?offset=0&limit=50&group_kind=all'
    return (template.replace('{primary}', IDS['primary'])
            .replace('{members}', IDS['members']))


def synth_journals():
    """Retained journals that satisfy every frozen atom (or expose NOT_RUN gaps)."""
    contract = load_group_workspace_density_contract()
    journals = {name: {} for name in JOURNALS}
    journals['api']['resources'] = {
        'group-create-primary': {'id': IDS['primary'], 'handle': 'h-primary'},
        'group-create-with-members': {'id': IDS['members'], 'handle': 'h-members'}}
    journals['cli']['resources'] = {
        'primary': {'id': IDS['cli_primary'], 'handle': 'h-cli-primary'},
        'termination': {'id': IDS['cli_termination'], 'handle': 'h-cli-termination'}}
    journals['incident']['resources'] = {'incident': {'id': IDS['incident'], 'handle': 'h-incident'}}
    journals['messages']['group_id'] = IDS['incident']
    journals['delete']['group_id'] = IDS['incident']
    for row in contract['density_requirements']:
        address = row['address']
        if address['kind'] == 'operation':
            event = _ensure_op(journals[address['journal']], address['operation'])
            event['status'] = 'ACKNOWLEDGED'
            for check in row['checks']:
                _satisfy(event, check)
        elif address['kind'] == 'read':
            path = _resolve_read_path(address['path_template'])
            entries = journals['api'].setdefault('reads', [])
            entry = next((item for item in entries
                          if item.get('path') == path
                          and item.get('after_operation') == address.get('after_operation')), None)
            if entry is None:
                entry = {'method': 'GET', 'path': path,
                         'after_operation': address.get('after_operation'), 'value': {}}
                entries.append(entry)
            for check in row['checks']:
                kind = check.get('type')
                if kind == 'read_present':
                    continue
                dotted = check['path']
                if kind == 'read_field_eq':
                    entry['value'][dotted] = _resolve(check['expected'])
                elif kind == 'read_field_not_empty':
                    entry['value'][dotted] = 'observed-non-empty'
                elif kind == 'read_field_len_eq':
                    entry['value'][dotted] = ['x'] * check['value']
                elif kind == 'read_field_ge':
                    entry['value'][dotted] = check['value']
                else:
                    raise AssertionError('unhandled read predicate: ' + kind)
        else:
            journals[address['journal']][address['state_key']] = {
                'status': 'PASS', 'http_status': 404, 'group_id': IDS['incident']}
    return journals


def _find_atom(density, atom_id):
    return next(row for row in density['requirements'] if row['id'] == atom_id)


class ContractGuardTests(unittest.TestCase):
    def test_committed_contract_validates_matches_sources_and_shape(self):
        contract = load_group_workspace_density_contract()
        validate_group_workspace_density_contract(contract)
        self.assertEqual(contract['atom_count'], ATOM_COUNT)
        self.assertEqual(len(contract['density_requirements']), 63)
        ids = [row['id'] for row in contract['density_requirements']]
        self.assertEqual(len(set(ids)), 63)
        self.assertEqual(contract['families'], {'api': 26, 'cli': 11, 'tail': 26})
        import hashlib
        for name in ('group.sh', 'stories.sh'):
            digest = hashlib.sha256((E2E_TEST_ROOT / name).read_bytes()).hexdigest()
            self.assertEqual(contract['sources'][name], digest,
                             'upstream ' + name + ' drifted from the frozen contract')

    def test_builder_reproduces_the_committed_contract(self):
        importlib.reload(builder)
        committed = load_group_workspace_density_contract()
        out = Path(tempfile.mkdtemp()) / 'rebuilt.json'
        builder.OUTPUT_PATH = out
        payload = builder.validate_and_emit()
        self.assertEqual(payload, committed)
        self.assertEqual(json.loads(out.read_text(encoding='utf-8')), committed)

    def test_builder_rejects_removed_anchor(self):
        importlib.reload(builder)
        src = Path(tempfile.mkdtemp())
        shutil.copy(E2E_TEST_ROOT / 'group.sh', src / 'group.sh')
        shutil.copy(E2E_TEST_ROOT / 'stories.sh', src / 'stories.sh')
        text = (src / 'group.sh').read_text(encoding='utf-8')
        self.assertIn('assert_eq "create group returns 200" "$HTTP_STATUS" "200"', text)
        (src / 'group.sh').write_text(
            text.replace('assert_eq "create group returns 200" "$HTTP_STATUS" "200"',
                         'assert_eq "create group returns 201" "$HTTP_STATUS" "200"', 1),
            encoding='utf-8')
        builder.E2E_TEST_ROOT = src
        builder.OUTPUT_PATH = src / 'out.json'
        with self.assertRaisesRegex(ValueError, 'anchor not unique'):
            builder.validate_and_emit()

    def test_builder_rejects_duplicated_anchor(self):
        importlib.reload(builder)
        src = Path(tempfile.mkdtemp())
        shutil.copy(E2E_TEST_ROOT / 'group.sh', src / 'group.sh')
        shutil.copy(E2E_TEST_ROOT / 'stories.sh', src / 'stories.sh')
        quote = 'assert_eq "create group returns 200" "$HTTP_STATUS" "200"'
        text = (src / 'group.sh').read_text(encoding='utf-8')
        (src / 'group.sh').write_text(text.replace(quote, quote + '\n' + quote, 1),
                                      encoding='utf-8')
        builder.E2E_TEST_ROOT = src
        builder.OUTPUT_PATH = src / 'out.json'
        with self.assertRaisesRegex(ValueError, 'anchor not unique \\(2\\)'):
            builder.validate_and_emit()

    def test_validation_rejects_shape_drift(self):
        contract = load_group_workspace_density_contract()
        contract['density_requirements'] = contract['density_requirements'][:62]
        with self.assertRaisesRegex(RunStateError, 'atom count changed'):
            validate_group_workspace_density_contract(contract)


class DensityProjectionTests(unittest.TestCase):
    def setUp(self):
        self.journals = synth_journals()
        self.bindings = workspace_density_bindings(_Roles(ROLES), self.journals)

    def test_full_projection_passes_all_sixty_three_atoms(self):
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        self.assertEqual(density['status'], 'PASS')
        self.assertEqual(density['atom_count'], ATOM_COUNT)
        self.assertEqual(density['counts'], {'PASS': 63, 'FAIL': 0, 'NOT_RUN': 0})
        self.assertEqual(density['family_counts'], {family: {'PASS': count, 'FAIL': 0, 'NOT_RUN': 0}
                                                    for family, count in
                                                    (('api', 26), ('cli', 11), ('tail', 26))})

    def test_summary_and_honesty_fields(self):
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        summary = density_summary(density)
        self.assertEqual(summary['status'], 'PASS')
        self.assertEqual(summary['atom_count'], ATOM_COUNT)
        self.assertEqual(summary['counts']['PASS'], 63)
        self.assertEqual(density['grants_release_credit'], False)
        self.assertEqual(density['remote_validation'], 'NOT_RUN')
        self.assertEqual(density['parent'], 'story_user_operates_group_workspace')
        self.assertEqual(density['digest'], fingerprint(
            {'contract': 'group-workspace-density-v1',
             'rows': [row['id'] for row in density['requirements']]}))
        self.assertEqual(density_summary('bogus'),
                         {'contract': 'group-workspace-density-v1', 'status': 'ERROR'})

    def test_read_atoms_reference_the_retained_read_address(self):
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        row = _find_atom(density, 'WORKSPACE.api.detail.group-id')
        self.assertEqual(row['status'], 'PASS')
        self.assertTrue(row['evidence_event'].startswith('read:/groups/'))

    def test_unacknowledged_event_stays_not_run(self):
        event = self.journals['api']['operations'][0]
        event['status'] = 'INTENDED'
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        row = _find_atom(density, 'WORKSPACE.api.create.status')
        self.assertEqual(row['status'], 'NOT_RUN')
        self.assertEqual(row['observed'], 'event_missing_or_unproven')
        self.assertEqual(density['status'], 'INCOMPLETE')

    def test_present_but_contradicting_evidence_fails_and_blocks(self):
        self.journals['api']['operations'][0]['http_status'] = 500
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        row = _find_atom(density, 'WORKSPACE.api.create.status')
        self.assertEqual(row['status'], 'FAIL')
        self.assertEqual(row['observed'], 500)
        self.assertEqual(row['failed_check'], 'status_eq')
        self.assertEqual(density['status'], 'BLOCKED')

    def test_missing_field_is_not_run_never_fabricated_pass(self):
        del self.journals['api']['operations'][0]['value']['id']
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        row = _find_atom(density, 'WORKSPACE.api.create.group-id')
        self.assertIn(row['status'], ('NOT_RUN', 'FAIL'))
        self.assertNotEqual(row['status'], 'PASS')
        self.assertNotEqual(density['status'], 'PASS')

    def test_missing_cli_journal_yields_not_run_cli_atoms(self):
        self.journals['cli'] = {}
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        counts = density['family_counts']['cli']
        self.assertEqual(counts['NOT_RUN'], 11)
        self.assertEqual(counts['PASS'], 0)
        self.assertEqual(density['status'], 'INCOMPLETE')

    def test_all_journals_missing_is_not_run(self):
        density = project_density(load_group_workspace_density_contract(),
                                  {name: {} for name in JOURNALS}, self.bindings)
        self.assertEqual(density['status'], 'NOT_RUN')
        self.assertEqual(density['counts']['NOT_RUN'], 63)

    def test_settings_admits_both_product_forms(self):
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        for atom in ('WORKSPACE.tail.settings.gone-or-legacy',
                     'WORKSPACE.tail.settings.policy-identified'):
            self.assertEqual(_find_atom(density, atom)['status'], 'PASS')
        self.assertEqual(_find_atom(density, 'WORKSPACE.tail.settings.gone-or-legacy')
                         ['observed']['observed_form'], 'retired_410')

    def test_settings_legacy_form_also_passes(self):
        for event in self.journals['incident']['operations']:
            if event['operation'] == 'settings-patch':
                event['http_status'] = 200
                event['value'] = {'service_spec': {'max_concurrency': 2}}
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        for atom in ('WORKSPACE.tail.settings.gone-or-legacy',
                     'WORKSPACE.tail.settings.policy-identified'):
            row = _find_atom(density, atom)
            self.assertEqual(row['status'], 'PASS', row['observed'])
            self.assertEqual(row['observed']['observed_form'], 'legacy_service_spec')

    def test_settings_legacy_form_mismatch_fails(self):
        for event in self.journals['incident']['operations']:
            if event['operation'] == 'settings-patch':
                event['http_status'] = 200
                event['value'] = {'service_spec': {'max_concurrency': 3}}
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        self.assertEqual(_find_atom(density, 'WORKSPACE.tail.settings.gone-or-legacy')['status'],
                         'PASS')
        self.assertEqual(_find_atom(density, 'WORKSPACE.tail.settings.policy-identified')['status'],
                         'FAIL')
        self.assertEqual(density['status'], 'BLOCKED')

    def test_fuse_any_of_accepts_payload_without_zero_exit(self):
        for event in self.journals['cli']['operations']:
            if event['operation'] == 'fuse':
                event['cli_exit_code'] = 1
                event['stdout'] = '{"fused": true}\n'
                event.pop('value', None)
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        self.assertEqual(_find_atom(density, 'WORKSPACE.cli.fuse.exit-or-json')['status'], 'PASS')

    def test_fuse_any_of_rejects_neither_branch(self):
        for event in self.journals['cli']['operations']:
            if event['operation'] == 'fuse':
                event['cli_exit_code'] = 1
                event['stdout'] = 'pending\n'
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        self.assertEqual(_find_atom(density, 'WORKSPACE.cli.fuse.exit-or-json')['status'], 'FAIL')
        self.assertEqual(density['status'], 'BLOCKED')

    def test_terminate_reflection_any_of(self):
        for event in self.journals['cli']['operations']:
            if event['operation'] == 'get-termination':
                event['cli_exit_code'] = 1
                event['stdout'] = 'Group terminated\n'
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        self.assertEqual(_find_atom(density, 'WORKSPACE.cli.terminate.reflects')['status'], 'PASS')

    def test_delete_not_found_state(self):
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        self.assertEqual(_find_atom(density, 'WORKSPACE.tail.delete.gone')['status'], 'PASS')
        self.journals['delete']['not_found']['http_status'] = 200
        density = project_density(load_group_workspace_density_contract(),
                                  self.journals, self.bindings)
        self.assertEqual(_find_atom(density, 'WORKSPACE.tail.delete.gone')['status'], 'FAIL')

    def test_projector_never_mutates_journals(self):
        before = fingerprint(self.journals)
        project_density(load_group_workspace_density_contract(), self.journals, self.bindings)
        self.assertEqual(fingerprint(self.journals), before)


class BindingsTests(unittest.TestCase):
    def setUp(self):
        self.journals = synth_journals()

    def test_bindings_carry_ids_only(self):
        bindings = workspace_density_bindings(_Roles(ROLES), self.journals)
        self.assertEqual(bindings['primary'], IDS['primary'])
        self.assertEqual(bindings['members'], IDS['members'])
        self.assertEqual(bindings['cli_primary'], IDS['cli_primary'])
        self.assertEqual(bindings['cli_termination'], IDS['cli_termination'])
        self.assertEqual(bindings['incident'], IDS['incident'])
        self.assertEqual(bindings['roles'], ROLES)
        serialized = json.dumps(bindings).lower()
        for forbidden in ('token', 'bearer', 'cookie', 'authorization'):
            self.assertNotIn(forbidden, serialized)

    def test_roles_snapshot_survives_closed_identity_context(self):
        bindings = workspace_density_bindings(_ClosedRoles(), self.journals, roles=ROLES)
        self.assertEqual(bindings['roles'], ROLES)

    def test_fallback_derives_roles_from_retained_faces(self):
        # No identity object at all: roles must come from the retained faces.
        self.journals['api']['fixture_visibility'] = {'actors': [
            {'role': 'CEO', 'bot_id': 'bot-ceo-fallback'},
            {'role': 'PM', 'bot_id': 'bot-pm-fallback'},
            {'role': 'ENG', 'bot_id': 'bot-eng-fallback'},
            {'role': 'QA', 'bot_id': 'bot-qa-fallback'}]}
        self.journals['cli']['operations'] = [
            {'operation': 'fixture-qa-visibility-public',
             'path': '/bots/bot-qa-cli/visibility', 'status': 'ACKNOWLEDGED'}]
        bindings = workspace_density_bindings(None, self.journals)
        self.assertEqual(bindings['roles'], {'CEO': 'bot-ceo-fallback', 'PM': 'bot-pm-fallback',
                                             'ENG': 'bot-eng-fallback', 'QA': 'bot-qa-cli'})

    def test_incident_binding_falls_back_to_message_group_id(self):
        self.journals['incident'] = {}
        bindings = workspace_density_bindings(_Roles(ROLES), self.journals)
        self.assertEqual(bindings['incident'], IDS['incident'])


if __name__ == '__main__':
    unittest.main()
