import argparse
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.provider_publishes import contract, dual_case_run as dual
from bcs_e2e.provider_publishes.build_contract import generate
from bcs_e2e.run_state import RunStateError


class ContractTests(unittest.TestCase):
    def test_42_to_98_machine_alignment(self):
        c = contract.source_binding()
        self.assertEqual(c['baseline_obligations'], 42)
        self.assertEqual(c['story_checks'], 98)
        self.assertEqual(c['parsed_upstream_steps'], 98)
        self.assertEqual(len(c['requirements']), 98)
        self.assertEqual(len(c['operations']), 42)
        self.assertEqual(sum(r['classification'] == 'field-density' for r in c['requirements']), 56)
        self.assertEqual(len(c['admission_preconditions']), 2)

    def test_protocol_version_is_a_string_not_float(self):
        row = next(r for r in contract.source_binding()['requirements']
                   if r.get('path') == 'protocol_version')
        self.assertEqual(row['expected'], '2.0')
        self.assertIs(type(row['expected']), str)

    def test_generator_and_frozen_source_agree(self):
        self.assertEqual(generate(), contract.source_binding())

    def test_unique_non_plumbing_requirements(self):
        c = contract.source_binding()
        self.assertEqual(len(set(contract.REQUIREMENTS)), 98)
        self.assertEqual(len({(r['operation'], r['kind'], r.get('path'), r.get('expectation'))
                              for r in c['requirements']}), 98)
        self.assertTrue(all(r['quote'].startswith(('assert', 'require_status')) for r in c['requirements']))
        self.assertFalse(any(r['quote'].startswith(('_cli_story_run', '[[', '||')) for r in c['requirements']))
        self.assertFalse(c['grants_release_credit'])

    def test_unmapped_visibility_grey_release_not_invented(self):
        c = contract.source_binding()
        self.assertIn('no visibility enumeration', c['scope_note'])
        self.assertIn('32-operation', c['net_inflow_note'])
        self.assertEqual(c['extracted_json_actual'], 45)
        self.assertEqual(c['extracted_digest_claim'], 104)

    def test_family_is_subpackage_not_new_flat_module(self):
        self.assertEqual(Path(dual.__file__).parent.name, 'provider_publishes')
        self.assertTrue((Path(dual.__file__).parent / '__init__.py').is_file())

    def test_contract_source_hash_drift_fails_closed(self):
        value = contract.source_binding()
        value['sources']['stories.sh'] = '0' * 64
        original = Path.read_text
        def read(path, *a, **kw):
            return json.dumps(value) if path == contract.CONTRACT_FILE else original(path, *a, **kw)
        with patch.object(Path, 'read_text', read), self.assertRaises(RunStateError):
            contract.source_binding()

    def test_changed_expected_value_with_unchanged_quote_fails_closed(self):
        value = contract.source_binding()
        row = next(r for r in value['requirements'] if r.get('path') == 'protocol_version')
        row['expected'] = 'invented'
        original = Path.read_text
        def read(path, *a, **kw):
            return json.dumps(value) if path == contract.CONTRACT_FILE else original(path, *a, **kw)
        with patch.object(Path, 'read_text', read), self.assertRaises(RunStateError):
            contract.source_binding()

    def test_parser_nine_parameters_and_register_helper(self):
        p = dual.build_provider_publishes_dual_argv_parser()
        self.assertEqual({a.dest for a in p._actions if a.dest != 'help'}, set(dual.PARAMETERS))
        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers(dest='command')
        dual.register_provider_publishes_dual_case_commands(commands)
        registered = commands.choices[dual.COMMAND]
        self.assertEqual({a.dest for a in registered._actions if a.dest != 'help'}, set(dual.PARAMETERS))

    def test_registration_equivalent_to_planned_slice(self):
        from bcs_e2e.case_command_registration import CASE_COMMANDS
        from bcs_e2e.migration_inventory import migration_plan
        registered = dual.COMMAND in CASE_COMMANDS
        planned = any(row.get('execution_command') == dual.COMMAND
                      for row in migration_plan()['local_backlog_case_slices'])
        self.assertEqual(registered, planned)
