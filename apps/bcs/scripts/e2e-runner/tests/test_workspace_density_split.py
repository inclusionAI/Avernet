"""Split-invariant charter for the workspace_density plane split.

Locks the lossless contract.py -> frozen/catalog/gates/plumbing/contract
five-plane split:

- the historical 105-name module surface is re-exported from contract.py
- frozen.py carries the live-true address census (36/27/9), replacing the
  stale inline 31/5 decomposition
- the plumbing scanner is wired (its missing TAIL_LINE_START import is
  fixed) and its 44-entry census matches the frozen register
- fail-closed behavior is byte-stable (oracle quote drift on
  WORKSPACE.cli.get.exit still raises exactly as pre-split)
- the three disclosed owning-wave semantic defects stay fail-closed and
  are named here until the owning wave adjudicates them:
  1. single-line quote exact-match vs the oracle row's substring quote
  2. cross-reference arithmetic guard vs the deprecated-pair
     committed-liveness design
  3. leaf projection rows missing story_calls/additional_story_calls keys
- every plane stays under the 1000-line module gate
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.run_state import RunStateError  # noqa: E402
from bcs_e2e.workspace_density import catalog, contract, frozen, gates, plumbing  # noqa: E402

CATALOG_SURFACE = [
    "Obligation", "group_sh_lines", "stories_sh_lines", "load_oracle",
    "load_field_ledger", "load_migration_story", "address_key", "_sha256",
    "_read_gated", "_load_json_gated", "_function_spans", "_span_contains",
    "_fold", "_verify_atom_quotes", "_operation_display", "_field_subjects",
]
GATES_SURFACE = [
    "leaf_atoms_id_parent", "_gates_classification", "_gates_rollup_projection",
    "_gates_plumbing", "_is_liveness_atom", "_gates_cross_reference",
    "_legacy_requirements", "_gates_address_census", "_roster",
    "_verify_story_steps",
]


class WorkspaceDensitySplitCharter(unittest.TestCase):
    def _live_rows(self):
        oracle = catalog.load_oracle()
        rows = list(oracle["density_requirements"])
        content = [row for row in rows
                   if {check.get("type") for check in row["checks"]} - frozen.LIVENESS_ONLY_TYPES]
        liveness = [row for row in rows
                    if {check.get("type") for check in row["checks"]} <= frozen.LIVENESS_ONLY_TYPES]
        return oracle, rows, content, liveness

    def test_historical_surface_is_reexported_from_contract(self):
        frozen_tree = ast.parse(Path(frozen.__file__).read_text())
        frozen_owned = []
        for node in frozen_tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                frozen_owned.append(node.name)
            elif isinstance(node, ast.Assign):
                frozen_owned.extend(t.id for t in node.targets
                                    if isinstance(t, ast.Name) and not t.id.startswith("__"))
        for name in frozen_owned:
            self.assertTrue(hasattr(contract, name), f"frozen name not re-exported: {name}")
        for name in CATALOG_SURFACE:
            self.assertTrue(hasattr(contract, name), f"catalog name not re-exported: {name}")
        for name in GATES_SURFACE:
            self.assertTrue(hasattr(contract, name), f"gates name not re-exported: {name}")
        self.assertIs(contract.Obligation, catalog.Obligation)
        self.assertIs(contract.load_oracle, catalog.load_oracle)
        self.assertIs(contract._scan_plumbing, plumbing.scan_plumbing)
        self.assertIs(contract.leaf_atoms_id_parent, gates.leaf_atoms_id_parent)

    def test_address_census_adopts_live_truth(self):
        _, rows, content, _ = self._live_rows()
        all_keys = {catalog.address_key(row["address"]) for row in rows}
        leaf_keys = {catalog.address_key(row["address"]) for row in content}
        self.assertEqual(
            (len(all_keys), len(leaf_keys), len(all_keys) - len(leaf_keys)),
            (frozen.ADDRESS_KEY_COUNT, frozen.LEAF_ADDRESS_KEY_COUNT,
             frozen.EXCLUSION_ONLY_ADDRESS_KEY_COUNT))
        self.assertEqual((frozen.ADDRESS_KEY_COUNT, frozen.LEAF_ADDRESS_KEY_COUNT,
                          frozen.EXCLUSION_ONLY_ADDRESS_KEY_COUNT), (36, 27, 9))

    def test_fail_closed_quote_drift_is_byte_stable(self):
        with self.assertRaises(RunStateError) as ctx:
            contract.workspace_density_plan()
        self.assertIn("atom quote drifted: WORKSPACE.cli.get.exit", str(ctx.exception))

    def test_gates_are_green_on_live_frozen_inputs(self):
        oracle, rows, content, liveness = self._live_rows()
        contract._gates_classification(content, liveness)
        ledger = catalog.load_field_ledger()
        story = catalog.load_migration_story()
        rollup = dict(ledger.get("baseline_atom_rollup") or {})
        committed_field = contract._committed_field_map(ledger)
        calls = contract._story_step_calls(catalog.stories_sh_lines())
        steps = story.get("steps") or []
        contract._verify_story_steps(calls, steps)
        parents = contract._build_parents(story.get("requirements") or [], calls,
                                          catalog.stories_sh_lines(), steps)
        leaves = contract._build_leaves(content, rollup, committed_field)
        exclusions = contract._build_exclusions(liveness, rows, rollup, committed_field)
        contract._gates_rollup_projection(rollup, rows, leaves, exclusions)
        group_lines = catalog.group_sh_lines()
        stories_lines = catalog.stories_sh_lines()
        register = plumbing.scan_plumbing(
            group_lines, stories_lines, catalog._function_spans(group_lines),
            catalog._function_spans(stories_lines), story.get("corrections") or [])
        self.assertEqual(len(register), frozen.PLUMBING_TOTAL)
        contract._gates_plumbing(register, rows, parents)
        census = contract._gates_address_census(rows, content)
        self.assertEqual(census["total"], frozen.ADDRESS_KEY_COUNT)
        roster = contract._roster(parents, leaves)
        self.assertEqual(len(roster), frozen.PARSED_COUNT)
        self.assertEqual(contract.residual_of(frozen.ATOM_COUNT, frozen.LEAF_COUNT,
                                              frozen.EXCLUSION_COUNT), 0)
        self.assertEqual(contract.residual_of(frozen.PARSED_COUNT, frozen.PARENT_COUNT,
                                              frozen.LEAF_COUNT), 0)

    def test_disclosed_owning_wave_defects_stay_fail_closed(self):
        _, rows, content, liveness = self._live_rows()
        ledger = catalog.load_field_ledger()
        story = catalog.load_migration_story()
        rollup = dict(ledger.get("baseline_atom_rollup") or {})
        committed_field = contract._committed_field_map(ledger)
        calls = contract._story_step_calls(catalog.stories_sh_lines())
        steps = story.get("steps") or []
        # defect 2: cross-reference arithmetic guard contradicts the
        # deprecated-pair committed-liveness design
        with self.assertRaises(RunStateError) as ctx:
            contract._gates_cross_reference(content, liveness, ledger)
        self.assertIn("cross-reference arithmetic broke", str(ctx.exception))
        # defect 3: leaf projection rows lack the Obligation projection keys
        leaves = contract._build_leaves(content, rollup, committed_field)
        self.assertNotIn("story_calls", leaves[0])
        self.assertNotIn("additional_story_calls", leaves[0])
        # defect 1 is locked by test_fail_closed_quote_drift_is_byte_stable

    def test_every_plane_stays_under_the_module_line_gate(self):
        for module in (frozen, catalog, gates, plumbing, contract):
            path = Path(module.__file__).resolve()
            self.assertLess(len(path.read_text().splitlines()), 1000,
                            f"{path.name} must stay under the 1000-line module gate")


if __name__ == "__main__":
    unittest.main()
