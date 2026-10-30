"""Guards: frozen binding inventories must cover the reviewed release denominator.

Covers both the id-coverage guard and the verbatim content guard, including
the backlog exemption, foreign/duplicate rejection, and manifest-drift
fail-close behaviour.
"""
import unittest
from unittest.mock import patch

from bcs_e2e.inventory import (
    REVIEWED_RELEASE_PARENT_COUNT,
    binding_freezes_reviewed_release_rows,
    binding_matches_reviewed_release,
    current_manifest,
    reviewed_release_inventory,
)
from bcs_e2e.run_state import RunStateError


class GuardBase(unittest.TestCase):
    def setUp(self):
        self.full = current_manifest()['legacy_inventory']
        self.reviewed_ids = {row['id'] for row in reviewed_release_inventory()}
        self.base = [row for row in self.full if row['id'] in self.reviewed_ids]

    def _reviewed_only(self):
        return [row for row in self.full if row['id'] in self.reviewed_ids]

    def _with_foreign_row(self):
        return self.full + [{'id': 'ghost-story'}]

    def _with_duplicate(self):
        return self.full + [self.full[0]]


class IdsGuardTests(GuardBase):
    def test_reviewed_and_full_denominators_are_intact(self):
        self.assertEqual(len(self.full), 41)
        self.assertEqual(len(self.reviewed_ids), REVIEWED_RELEASE_PARENT_COUNT)

    def test_full_manifest_snapshot_is_admitted(self):
        self.assertTrue(binding_matches_reviewed_release(self.full))

    def test_reviewed_only_snapshot_is_admitted(self):
        self.assertTrue(binding_matches_reviewed_release(self._reviewed_only()))

    def test_missing_reviewed_row_is_rejected(self):
        drifted = [row for row in self.full
                   if row['id'] != next(iter(self.reviewed_ids))]
        self.assertFalse(binding_matches_reviewed_release(drifted))

    def test_foreign_row_is_rejected(self):
        self.assertFalse(binding_matches_reviewed_release(self._with_foreign_row()))

    def test_duplicate_ids_are_rejected(self):
        self.assertFalse(binding_matches_reviewed_release(self._with_duplicate()))

    def test_empty_and_non_list_inputs_are_rejected(self):
        for value in ([], None, 'legacy', 41, [1]):
            self.assertFalse(binding_matches_reviewed_release(value))

    def test_non_dict_rows_are_rejected(self):
        self.assertFalse(binding_matches_reviewed_release([['not-a-dict']]))


class ContentGuardTests(GuardBase):
    def test_full_manifest_snapshot_matches_verbatim(self):
        self.assertTrue(binding_freezes_reviewed_release_rows(self.full))

    def test_reviewed_only_snapshot_matches_verbatim(self):
        self.assertTrue(binding_freezes_reviewed_release_rows(self._reviewed_only()))

    def test_tampered_reviewed_row_is_rejected(self):
        tampered = [dict(row) for row in self.full]
        target = next(iter(self.reviewed_ids))
        for row in tampered:
            if row['id'] == target:
                row['tampered'] = True
                break
        self.assertFalse(binding_freezes_reviewed_release_rows(tampered))
        self.assertTrue(binding_matches_reviewed_release(tampered))

    def test_tampered_backlog_row_is_exempt(self):
        tampered = [dict(row) for row in self.full]
        backlog = next(row['id'] for row in tampered if row['id'] not in self.reviewed_ids)
        for row in tampered:
            if row['id'] == backlog:
                row['tampered'] = True
                break
        self.assertTrue(binding_freezes_reviewed_release_rows(tampered))

    def test_enriched_rows_are_not_admitted_verbatim(self):
        enriched = reviewed_release_inventory()
        self.assertFalse(binding_freezes_reviewed_release_rows(enriched))

    def test_foreign_row_is_rejected(self):
        self.assertFalse(binding_freezes_reviewed_release_rows(self._with_foreign_row()))

    def test_duplicate_ids_are_rejected(self):
        self.assertFalse(binding_freezes_reviewed_release_rows(self._with_duplicate()))

    def test_malformed_inputs_are_rejected(self):
        for value in ([], None, 'legacy', 41, [{'id': 1}], [[1]]):
            self.assertFalse(binding_freezes_reviewed_release_rows(value))

    def test_manifest_drift_fails_closed(self):
        with patch('bcs_e2e.inventory.current_manifest',
                   return_value={'legacy_inventory': [{'id': 'only-one'}]}):
            with self.assertRaises(RunStateError):
                reviewed_release_inventory()
            self.assertFalse(binding_matches_reviewed_release(self.full))
            self.assertFalse(binding_freezes_reviewed_release_rows(self.full))


if __name__ == '__main__':
    unittest.main()
