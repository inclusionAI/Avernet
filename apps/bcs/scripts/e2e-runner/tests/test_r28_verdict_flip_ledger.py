"""r28 executor registration-face verdict flip ledger (offline charter).

Pins the eleven r28 rows produced by run 20261101-regression-r28-bulk (bulk
head ef6114af0; evidence leaf ~/.codex/bcs-e2e/preprod-live-evidence/
20261101-regression-r28-bulk/) plus the r28fix provider-publishes admission
leg (run 20261101-regression-r28fix-provider-publishes, landed local commit
467c6bc4a): seven committed verdict flips and four notes-only disclosures.

Purely offline contract assertions: the json face, the loader, the fence and
counts closure, the reviewed 31-story denominator, and the per-row leg notes
(byte-pinned via the shared constants in test_migration_inventory). No
network, no credentials, no remote resources.
"""
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.inventory import (  # noqa: E402
    EXECUTOR_MIGRATION_NOTES, KNOWN_UNMAPPED_LEGACY_IDS,
    load_executor_migration, reviewed_release_inventory)
from bcs_e2e.provider_publishes.density_run import WAVE_TOKEN  # noqa: E402
from test_migration_inventory import (  # noqa: E402
    R28_FRESH_OPERATOR_CLI_LEG49_DUAL_PASS,
    R28_FRESH_SERVICE_POLICY_LEG59_DUAL_PASS,
    R28_FRESH_PROVIDER_OWNER_LEG56_BLOCKED,
    R28_FRESH_SESSION_OWNER_LEG55_BLOCKED,
    R28_FRESH_DRAFT_DELIVERY_LEG57_BLOCKED,
    R28_FRESH_AUTHORITY_JOURNEY_LEG54_BLOCKED,
    R28_FRESH_PROVIDER_SUBBOTS_LEG58_BLOCKED,
    R28FIX_PROVIDER_PUBLISHES_V2_LIVE_ADMISSION_SEAM_PENDING,
    R28_SESSION_PUBLIC_GATEWAY_EFFECT_BLOCKED_LEG53,
    R28_FRIEND_CLI_GATEWAY_FRIENDS_REQUESTS_404_LEG48,
    R28_CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_RECONFIRMED_LEG47)

MIGRATION_PATH = ROOT / 'executor-migration.json'

# (story id, mode, committed verdict, leg note, old head anchors in order)
R28_ROWS = (
    ('story_operator_coordinates_with_cli', 'DUAL', 'PASS',
     R28_FRESH_OPERATOR_CLI_LEG49_DUAL_PASS,
     ('row #9 story_operator_coordinates_with_cli',)),
    ('story_retired_service_policy', 'DUAL', 'PASS',
     R28_FRESH_SERVICE_POLICY_LEG59_DUAL_PASS,
     ('SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031',
      'SERVICE_POLICY_DENSITY_15_OBLIGATIONS_20261031')),
    ('story_provider_owner_isolation', 'DUAL', 'BLOCKED_INVENTORIED',
     R28_FRESH_PROVIDER_OWNER_LEG56_BLOCKED,
     ('row #35 story_provider_owner_isolation',)),
    ('story_session_owner_permissions', 'DUAL', 'BLOCKED_INVENTORIED',
     R28_FRESH_SESSION_OWNER_LEG55_BLOCKED,
     ('row #36 story_session_owner_permissions',)),
    ('story_collaboration_draft_delivery', 'DUAL', 'BLOCKED_INVENTORIED',
     R28_FRESH_DRAFT_DELIVERY_LEG57_BLOCKED,
     ('DRAFT_DELIVERY_DENSITY_24_OBLIGATIONS_20261031',)),
    ('story_authority_user_journey', 'DUAL', 'BLOCKED_INVENTORIED',
     R28_FRESH_AUTHORITY_JOURNEY_LEG54_BLOCKED,
     ('AUTHORITY_DENSITY_42_OBLIGATIONS_20261031',)),
    ('story_provider_operator_manages_subbots', 'DUAL',
     'BLOCKED_INVENTORIED', R28_FRESH_PROVIDER_SUBBOTS_LEG58_BLOCKED,
     ('DUAL_SUBBOTS_DENSITY_D4_131_LEAVES',)),
    ('story_provider_operator_publishes_agent', 'DUAL', 'PASS',
     R28FIX_PROVIDER_PUBLISHES_V2_LIVE_ADMISSION_SEAM_PENDING,
     (WAVE_TOKEN,)),
    ('story_user_runs_and_shares_sessions', 'DUAL', 'PASS',
     R28_SESSION_PUBLIC_GATEWAY_EFFECT_BLOCKED_LEG53,
     ('WPB_SESSION_SHARING_87_OBLIGATIONS_20261031',)),
    ('story_user_builds_trusted_team', 'DUAL', 'PASS',
     R28_FRIEND_CLI_GATEWAY_FRIENDS_REQUESTS_404_LEG48,
     ('FRESH_FIXTURE_R5G',)),
    ('story_cli_operator_validates_channel_management', 'DUAL',
     'BLOCKED_INVENTORIED',
     R28_CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_RECONFIRMED_LEG47,
     ('row #11 story_cli_operator_validates_channel_management',)),
)

R28_TOKENS = tuple(note.split(':')[0] for _, _, _, note, _ in R28_ROWS)

# committed verdict flips with fresh preprod PASS legs
PASS_FLIPS = (
    'story_operator_coordinates_with_cli',
    'story_retired_service_policy',
)
# committed verdict flips fail-closed on fresh preprod BLOCKED legs
BI_FLIP_GAPS = {
    'story_provider_owner_isolation':
        ('PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED', 24),
    'story_session_owner_permissions':
        ('PREPROD_SESSION_OWNER_FULL_STORY_NOT_EXECUTED', 9),
    'story_collaboration_draft_delivery':
        ('PREPROD_DRAFT_DELIVERY_STORY_BLOCKED', 32),
    'story_authority_user_journey':
        ('PREPROD_AUTHORITY_ADMISSION_BLOCKED', 42),
    'story_provider_operator_manages_subbots':
        ('PREPROD_PROVIDER_CONTROL_PLANE_ABSENT', 15),
}
# notes-only disclosures (verdicts unchanged)
DISCLOSES = {
    'story_provider_operator_publishes_agent': 'PASS',
    'story_user_runs_and_shares_sessions': 'PASS',
    'story_user_builds_trusted_team': 'PASS',
    'story_cli_operator_validates_channel_management':
        'BLOCKED_INVENTORIED',
}


class R28VerdictFlipLedgerTest(unittest.TestCase):
    """Offline r28 face ledger: face equality, pins, counters, closure."""

    def setUp(self):
        with open(MIGRATION_PATH, encoding='utf-8') as handle:
            payload = json.load(handle)
        self.assertEqual(set(payload), {'schema_version', 'rows'})
        self.json_rows = payload['rows']
        self.assertEqual(len(self.json_rows), 41)
        self.face = load_executor_migration()

    def test_json_face_equals_loader(self):
        json_face = {row['id']: row['executor_migration']
                     for row in self.json_rows}
        self.assertEqual(json_face, self.face)

    def test_eleven_rows_pinned_with_leg_notes(self):
        for story_id, mode, verdict, leg_note, old_heads in R28_ROWS:
            with self.subTest(row=story_id):
                row = self.face[story_id]
                self.assertEqual(row['mode'], mode)
                self.assertEqual(row['preprod_verdict'], verdict)
                self.assertEqual(row['gates'], [])
                notes = row['notes']
                self.assertEqual(len(notes), len(old_heads) + 1)
                for head, note in zip(old_heads, notes[:-1]):
                    self.assertTrue(note.startswith(head),
                                    f'{story_id}: old head drifted: {head}')
                self.assertEqual(notes[-1], leg_note)
                self.assertIn(leg_note, EXECUTOR_MIGRATION_NOTES)
                self.assertEqual(leg_note.split(':')[0],
                                 leg_note.split(':')[0])

    def test_leg_tokens_unique_and_registered(self):
        tokens = [note.split(':')[0] for _, _, _, note, _ in R28_ROWS]
        self.assertEqual(len(tokens), 11)
        self.assertEqual(len(set(tokens)), 11)
        carriers = {token: [] for token in tokens}
        for row in self.json_rows:
            for note in row['executor_migration']['notes']:
                token = note.split(':')[0]
                if token in carriers:
                    carriers[token].append(row['id'])
        for token, story_ids in carriers.items():
            with self.subTest(token=token):
                self.assertEqual(len(story_ids), 1,
                                 f'{token} must ride exactly its own row')

    def test_pass_leg_markers(self):
        for story_id in PASS_FLIPS:
            with self.subTest(row=story_id):
                note = self.face[story_id]['notes'][-1]
                self.assertIn('rc=0', note)
                self.assertIn('remote_validation=PREPROD_LIVE', note)
                self.assertIn('grants_release_credit=true', note)
                self.assertIn('ef6114af0', note)
                self.assertIn('20261101-regression-r28-bulk', note)
        self.assertIn('13/13 steps PASS',
                      self.face['story_operator_coordinates_with_cli']
                      ['notes'][-1])
        self.assertIn('8/8 steps PASS',
                      self.face['story_retired_service_policy']
                      ['notes'][-1])

    def test_blocked_leg_markers(self):
        for story_id, (gap, steps) in BI_FLIP_GAPS.items():
            with self.subTest(row=story_id):
                note = self.face[story_id]['notes'][-1]
                self.assertTrue(note.startswith(
                    note.split(':')[0] + ':'))
                self.assertIn(gap, note)
                self.assertIn(f'0/{steps} steps NOT_RUN', note)
                self.assertIn('rc=2', note)
                self.assertIn('remote_validation=NOT_RUN', note)
                self.assertIn('grants_release_credit=false', note)

    def test_disclosures_verdict_unchanged(self):
        for story_id, verdict in DISCLOSES.items():
            with self.subTest(row=story_id):
                row = self.face[story_id]
                self.assertEqual(row['preprod_verdict'], verdict)
                note = row['notes'][-1]
                self.assertIn('remote_validation=NOT_RUN', note)
                self.assertIn('grants_release_credit=false', note)
                self.assertTrue(note.split(':')[0].startswith('R28'))
        publishes = self.face['story_provider_operator_publishes_agent']
        self.assertEqual(publishes['notes'][0], WAVE_TOKEN)
        fix_note = publishes['notes'][-1]
        for marker in ('PREPROD_PROVIDER_PUBLISHES_JOURNEY_SEAM_PENDING',
                       '98 obligations NOT_RUN', 'NOT_QUALIFIED',
                       'NO_REMOTE_RESOURCES_CREATED', '467c6bc4a',
                       '3/3 PASS HTTP 200'):
            self.assertIn(marker, fix_note)
        self.assertIn('GATEWAY_EFFECT_BLOCKED',
                      self.face['story_user_runs_and_shares_sessions']
                      ['notes'][-1])
        self.assertIn('FriendCliRunnerError',
                      self.face['story_user_builds_trusted_team']
                      ['notes'][-1])
        self.assertIn('FRIEND.cli-accept',
                      self.face['story_user_builds_trusted_team']
                      ['notes'][-1])
        channel = self.face['story_cli_operator_validates_channel_management']
        self.assertIn('ChannelCliRunnerError', channel['notes'][-1])
        self.assertIn('CHANNEL-CLI.bind-redacted', channel['notes'][-1])
        self.assertIn('0/9 steps NOT_RUN', channel['notes'][-1])

    def test_full_denominator_counts(self):
        states = Counter((row['mode'], row['preprod_verdict'])
                         for row in self.face.values())
        self.assertEqual(states, Counter({
            ('DUAL', 'PASS'): 21,
            ('DUAL', 'BLOCKED_INVENTORIED'): 15,
            ('DUAL', 'NOT_RUN'): 4,
            ('EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'): 1}))
        gates = Counter(gate for row in self.face.values()
                        for gate in row['gates'])
        self.assertEqual(gates, Counter({'MOCK': 2}))

    def test_reviewed_denominator_counts(self):
        reviewed = reviewed_release_inventory()
        self.assertEqual(len(reviewed), 31)
        states = Counter((row['executor_migration']['mode'],
                          row['executor_migration']['preprod_verdict'])
                         for row in reviewed)
        self.assertEqual(states, Counter({
            ('DUAL', 'PASS'): 20,
            ('DUAL', 'BLOCKED_INVENTORIED'): 8,
            ('DUAL', 'NOT_RUN'): 2,
            ('EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'): 1}))

    def test_reviewed_denominator_membership(self):
        for story_id in BI_FLIP_GAPS:
            if story_id == 'story_authority_user_journey':
                self.assertNotIn(story_id, KNOWN_UNMAPPED_LEGACY_IDS)
            else:
                self.assertIn(story_id, KNOWN_UNMAPPED_LEGACY_IDS)
        for story_id in PASS_FLIPS:
            self.assertNotIn(story_id, KNOWN_UNMAPPED_LEGACY_IDS)
        for story_id in DISCLOSES:
            self.assertNotIn(story_id, KNOWN_UNMAPPED_LEGACY_IDS)

    def test_fence_and_face_closure(self):
        face_notes = [note for row in self.face.values()
                      for note in row['notes']]
        self.assertEqual(len(face_notes), 54)
        self.assertEqual(len(set(face_notes)), 54)
        self.assertEqual(set(face_notes), set(EXECUTOR_MIGRATION_NOTES))
        for _, _, _, leg_note, _ in R28_ROWS:
            self.assertEqual(face_notes.count(leg_note), 1)

    def test_no_release_credit_outside_live_pass_legs(self):
        for story_id in list(BI_FLIP_GAPS) + list(DISCLOSES):
            with self.subTest(row=story_id):
                self.assertIn(
                    'grants_release_credit=false',
                    self.face[story_id]['notes'][-1])
                self.assertIn(
                    'remote_validation=NOT_RUN',
                    self.face[story_id]['notes'][-1])


if __name__ == '__main__':
    unittest.main()
