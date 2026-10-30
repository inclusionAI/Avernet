"""Run-frozen Case inventory and executor artifact identity.

Checksums detect drift, not malicious rewrites by someone who owns the Run files.
Historical reports can be read offline; executing with changed code is refused.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from .artifact import process_digest
from .effect_policy import validate_effect_policy
from .profile import ConfigError, fingerprint, identifier
from .run_state import MAX_STATE_BYTES, RunStateError, read

ROOT = Path(__file__).resolve().parents[1]


def validate_manifest(value: dict) -> dict:
    try:
        if (set(value) != {'schema_version', 'baseline_commit', 'suite', 'legacy_inventory', 'cases'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['baseline_commit'], str)
                or len(value['baseline_commit']) != 40
                or any(c not in '0123456789abcdef' for c in value['baseline_commit'])):
            raise ValueError
        identifier(value['suite'], 'manifest suite')
        cases, legacy = value['cases'], value['legacy_inventory']
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
            raise ValueError
        if not isinstance(legacy, list) or not 1 <= len(legacy) <= 500:
            raise ValueError
        for items in (cases, legacy):
            ids = [identifier(item['id'], 'manifest item ID') for item in items]
            if len(set(ids)) != len(ids): raise ValueError
        for case in cases:
            if type(case['version']) is not int or case['version'] < 1: raise ValueError
            for field in ('assertions', 'effects', 'steps', 'roles', 'required_evidence'):
                items = case[field]
                if not isinstance(items, list) or not 1 <= len(items) <= 1000: raise ValueError
                if any(not isinstance(item, str) or not item or len(item) > 512 for item in items): raise ValueError
                if len(set(items)) != len(items): raise ValueError
            if any(not item.startswith(case['id'] + '.') for item in case['assertions']): raise ValueError
        return json.loads(json.dumps(value, allow_nan=False))
    except (KeyError, TypeError, ValueError, ConfigError):
        raise RunStateError('manifest schema, versions or inventory are invalid') from None


KNOWN_UNMAPPED_LEGACY_IDS = frozenset({
    # Pre-existing five, unmapped since the reviewed-release cut:
    'story_cli_operator_manages_group_context',
    'story_nested_fixed_loop_workflows',
    'story_state_machine_result_queue',
    'test_ep_dept_scope_parses_object_friend_ext_entries',
    'test_ep_tc_friend_auth_sync_readdressed_principal',
    # Long-unregistered upstream stories (harness-defined before the freeze,
    # never part of the reviewed release denominator; registered 2026-10-29
    # for migration with the fresh re-freeze, see migration-freeze-review-20261029.md):
    'story_provider_owner_isolation',
    'story_session_owner_permissions',
    'story_provider_operator_manages_subbots',
    'story_collaboration_draft_delivery',
    'story_collaboration_draft_group_launch',
})
REVIEWED_RELEASE_PARENT_COUNT = 31

EXECUTOR_MIGRATION_PATH = ROOT / 'executor-migration.json'
EXECUTOR_MIGRATION_MODES = frozenset({
    'DUAL', 'LOCAL_ONLY', 'DYNAMIC_OWNED_LOCAL', 'EDGE_OBLIGATION_IN_FAMILY',
})
PREPROD_VERDICTS = frozenset({
    'PASS', 'BLOCKED_INVENTORIED', 'NOT_RUN', 'NOT_APPLICABLE',
})
EXECUTOR_MIGRATION_GATES = frozenset({'MOCK'})
EXECUTOR_MIGRATION_NOTES = frozenset({
    'ROSTER_LAG', 'STATION_DEFECT_R5_CAVEAT', 'FRESH_FIXTURE_R5G',
    'DUAL_STRUCTURED_DENSITY_R12_PREPROD_PASS_20261031',
    'SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031',
    'DIRECT_DENSITY_14_OBLIGATIONS_20261031',
    'CUSTOM_COLLAB_DENSITY_37_OBLIGATIONS_20261031',
    'DUAL_SUBBOTS_DENSITY_D4_131_LEAVES',
})
_VALID_EXECUTOR_MIGRATION_STATES = frozenset({
    ('DUAL', 'PASS'),
    ('DUAL', 'BLOCKED_INVENTORIED'),
    ('DUAL', 'NOT_RUN'),
    ('LOCAL_ONLY', 'NOT_RUN'),
    ('DYNAMIC_OWNED_LOCAL', 'NOT_APPLICABLE'),
    ('EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'),
})
_EXPECTED_EXECUTOR_MIGRATION_COUNTS = {
    ('DUAL', 'PASS'): 19,
    ('DUAL', 'BLOCKED_INVENTORIED'): 11,
    ('DUAL', 'NOT_RUN'): 10,
    ('EDGE_OBLIGATION_IN_FAMILY', 'NOT_APPLICABLE'): 1,
}
_EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS = {'MOCK': 2}
_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS = {
    'STATION_DEFECT_R5_CAVEAT': 1,
    'FRESH_FIXTURE_R5G': 1,
    'ROSTER_LAG': 1,
    'DUAL_STRUCTURED_DENSITY_R12_PREPROD_PASS_20261031': 1,
    'SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031': 1,
    'DIRECT_DENSITY_14_OBLIGATIONS_20261031': 1,
    'CUSTOM_COLLAB_DENSITY_37_OBLIGATIONS_20261031': 1,
    'DUAL_SUBBOTS_DENSITY_D4_131_LEAVES': 1,
}


def load_executor_migration() -> dict[str, dict]:
    """Load the single 41-row executor migration status source by story ID."""
    try:
        value = read(EXECUTOR_MIGRATION_PATH)
    except RunStateError:
        raise RunStateError(
            'executor migration status source is missing or unreadable') from None
    try:
        if (set(value) != {'schema_version', 'rows'}
                or not isinstance(value['rows'], list)
                or len(value['rows']) != 41):
            raise ValueError
        statuses = {}
        for item in value['rows']:
            if not isinstance(item, dict) or set(item) != {'id', 'executor_migration'}:
                raise ValueError
            story_id = identifier(item['id'], 'executor migration ID')
            if story_id in statuses:
                raise ValueError
            status = item['executor_migration']
            if (not isinstance(status, dict)
                    or set(status) != {'mode', 'preprod_verdict', 'gates', 'notes'}):
                raise ValueError
            mode, verdict = status['mode'], status['preprod_verdict']
            if ((mode, verdict) not in _VALID_EXECUTOR_MIGRATION_STATES
                    or mode not in EXECUTOR_MIGRATION_MODES
                    or verdict not in PREPROD_VERDICTS):
                raise ValueError
            gates, notes = status['gates'], status['notes']
            if (not isinstance(gates, list) or len(set(gates)) != len(gates)
                    or any(gate not in EXECUTOR_MIGRATION_GATES for gate in gates)
                    or not isinstance(notes, list) or len(set(notes)) != len(notes)
                    or any(note not in EXECUTOR_MIGRATION_NOTES for note in notes)):
                raise ValueError
            statuses[story_id] = {
                'mode': mode, 'preprod_verdict': verdict,
                'gates': list(gates), 'notes': list(notes),
            }
        states = Counter(
            (status['mode'], status['preprod_verdict']) for status in statuses.values())
        gates = Counter(
            gate for status in statuses.values() for gate in status['gates'])
        notes = Counter(
            note for status in statuses.values() for note in status['notes'])
        if (states != Counter(_EXPECTED_EXECUTOR_MIGRATION_COUNTS)
                or gates != Counter(_EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS)
                or notes != Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS)):
            raise ValueError
        return statuses
    except (KeyError, TypeError, ValueError, ConfigError):
        raise RunStateError(
            'executor migration status source is invalid or drifted') from None


def legacy_inventory_with_executor_migration(manifest: dict | None = None) -> list[dict]:
    """Return every legacy row with its executor_migration object joined by ID."""
    value = current_manifest() if manifest is None else manifest
    try:
        legacy = value['legacy_inventory']
        if not isinstance(legacy, list) or any(
                not isinstance(row, dict) or 'id' not in row for row in legacy):
            raise ValueError
        statuses = load_executor_migration()
        legacy_ids = [row['id'] for row in legacy]
        if len(legacy_ids) != len(set(legacy_ids)):
            raise ValueError
        missing = set(legacy_ids) - set(statuses)
        extra = set(statuses) - set(legacy_ids)
        if missing or extra:
            raise RunStateError(
                'executor migration status join is incomplete: '
                f'missing={len(missing)} extra={len(extra)}')
        rows = []
        for row in legacy:
            status = statuses[row['id']]
            enriched = dict(row)
            enriched['executor_migration'] = {
                'mode': status['mode'],
                'preprod_verdict': status['preprod_verdict'],
                'gates': list(status['gates']),
                'notes': list(status['notes']),
            }
            rows.append(enriched)
        return rows
    except (KeyError, TypeError, ValueError):
        raise RunStateError('executor migration status join is invalid') from None


def reviewed_release_inventory(manifest: dict | None = None) -> list[dict]:
    """Return the reviewed 31-Story release denominator from the current 41-row legacy inventory.

    Ten legacy parents sit outside the reviewed release denominator: the
    pre-existing backlog rows plus five registered 2026-10-29 during the
    harness re-freeze (long-unregistered upstream stories). They are known
    backlog, not silently missing or absorbed into this release denominator.
    """
    value = current_manifest() if manifest is None else manifest
    legacy = legacy_inventory_with_executor_migration(value)
    ids = [row['id'] for row in legacy]
    reviewed = [row for row in legacy if row['id'] not in KNOWN_UNMAPPED_LEGACY_IDS]
    if len(ids) != len(set(ids)) or len(reviewed) != REVIEWED_RELEASE_PARENT_COUNT:
        raise RunStateError(
            'reviewed release denominator changed; review the known '
            'unmapped stories separately')
    return reviewed


def binding_matches_reviewed_release(binding_inventory: object) -> bool:
    """Guard: a frozen binding inventory covers the reviewed 31-story denominator.

    Bindings frozen before the reviewed cut carry the whole 41-row manifest
    snapshot, so any id set between the reviewed denominator and the full
    manifest is admitted; every row id must come from the manifest and be
    distinct. The ten known-unmapped backlog rows are exempt from drift
    detection by design; the reviewed rows are the enforced surface.
    """
    try:
        if not isinstance(binding_inventory, list) or not binding_inventory:
            return False
        ids = [identifier(row['id'], 'binding legacy inventory row ID') for row in binding_inventory]
        if len(ids) != len(set(ids)):
            return False
        manifest_ids = {row['id'] for row in current_manifest()['legacy_inventory']}
        reviewed_ids = {row['id'] for row in reviewed_release_inventory()}
        return reviewed_ids <= set(ids) <= manifest_ids
    except (KeyError, TypeError, ValueError, ConfigError, RunStateError):
        return False


def binding_freezes_reviewed_release_rows(binding_inventory: object) -> bool:
    """Guard: the reviewed rows inside a frozen binding match the manifest verbatim.

    Stronger than the id-coverage guard: each reviewed row (all fields, in
    manifest order) must be byte-equal to the manifest snapshot. Backlog rows
    stay exempt so pre-cut bindings remain readable; reviewed drift fails closed.
    """
    try:
        if not isinstance(binding_inventory, list) or not binding_inventory:
            return False
        ids = [identifier(row['id'], 'binding legacy inventory row ID') for row in binding_inventory]
        if len(ids) != len(set(ids)):
            return False
        manifest = current_manifest()['legacy_inventory']
        manifest_ids = {row['id'] for row in manifest}
        reviewed_ids = {row['id'] for row in reviewed_release_inventory()}
        mapping = {row['id']: row for row in binding_inventory}
        if not reviewed_ids <= set(ids) <= manifest_ids:
            return False
        base = [row for row in manifest if row['id'] in reviewed_ids]
        return [mapping[row['id']] for row in base] == base
    except (KeyError, TypeError, ValueError, ConfigError, RunStateError):
        return False


def assert_reviewed_parent(parent_id: str, *, family: str) -> str:
    """Fail closed unless a dual parent is a reviewed release parent.

    Known unmapped backlog parents stay executable and are named explicitly;
    anything outside the reviewed release denominator or the unmapped backlog
    is rejected before any binding is frozen.
    """
    reviewed = {row['id'] for row in reviewed_release_inventory()}
    if not isinstance(parent_id, str) or (
            parent_id not in reviewed and parent_id not in KNOWN_UNMAPPED_LEGACY_IDS):
        raise RunStateError(
            family + ' parent is outside the reviewed release denominator')
    return parent_id


def current_manifest(suite: str = 'preprod-pilot-v1') -> dict:
    paths = {'preprod-pilot-v1': 'manifest.json', 'preprod-retained-v1': 'retained-manifest.json',
             'preprod-retained-file-v1': 'retained-file-manifest.json',
             'owned-local-trusted-team-v1': 'trusted-team-manifest.json'}
    if suite not in paths:
        raise RunStateError('unsupported executor suite')
    manifest = validate_manifest(read(ROOT / paths[suite]))
    if manifest['suite'] != suite:
        raise RunStateError('executor manifest has a different suite identity')
    validate_effect_policy(manifest)
    validate_evidence_policy(manifest)
    return manifest


def executor_digest() -> str:
    try:
        return process_digest()
    except (ValueError, OSError):
        raise RunStateError('executor artifact differs from the process-import snapshot') from None


def freeze_manifest(state_dir: Path, manifest: dict) -> str:
    manifest = validate_manifest(manifest)
    validate_effect_policy(manifest)
    validate_evidence_policy(manifest)
    path = state_dir / 'manifest.json'
    digest = fingerprint(manifest)
    if path.exists() or path.is_symlink():
        if fingerprint(validate_manifest(read(path))) != digest:
            raise RunStateError('existing Run manifest differs; never overwrite its inventory')
        return digest
    payload = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    if len(payload) > MAX_STATE_BYTES:
        raise RunStateError('manifest exceeds the supported size limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return digest


def frozen_manifest(state_dir: Path, state: dict) -> dict:
    manifest = validate_manifest(read(state_dir / 'manifest.json'))
    if state.get('manifest_digest') != fingerprint(manifest):
        raise RunStateError('Run manifest digest is missing or mismatched')
    artifact = state.get('executor_digest')
    if not isinstance(artifact, str) or len(artifact) != 64 or any(c not in '0123456789abcdef' for c in artifact):
        raise RunStateError('Run executor binding is missing or invalid')
    return manifest


def executable_case(state_dir: Path, state: dict, case_id: str) -> dict:
    manifest = frozen_manifest(state_dir, state)
    if fingerprint(current_manifest(manifest['suite'])) != state['manifest_digest']:
        raise RunStateError('Case inventory changed after prepare; start a new Run')
    if executor_digest() != state['executor_digest']:
        raise RunStateError('executor artifact changed after prepare; start a new Run')
    for case in manifest['cases']:
        if case['id'] == case_id:
            return case
    raise RunStateError('Case is absent from the frozen Run inventory')


# --- Finite evidence contracts (consolidated from evidence_policy.py) ---
# Admission only: declarations are not observation receipts or qualification.
# Historical snapshots remain readable without applying today's execution policy.

_REQUIRED = {
    'owned-local-trusted-team-v1': {
        'LOCAL-TRUST-API-CLI-01': {'api', 'cli', 'sqlite'},
    },
    'preprod-pilot-v1': {
        'PRE-01': {'api', 'ob'},
        'PRE-02': {'api', 'websocket', 'ob'},
        'PRE-03': {'api', 'bytes', 'ob'},
    },
    'preprod-retained-v1': {
        'RETAIN-GROUP-01': {'api', 'ob'},
        'RETAIN-SESSION-01': {'api', 'websocket', 'ob'},
    },
    'preprod-retained-file-v1': {
        'RETAIN-FILE-01': {'api', 'bytes', 'ob'},
    },
}
_DIAGNOSTIC = {'request_correlation', 'logs'}


def validate_evidence_policy(manifest: dict) -> None:
    """Reject unsupported new execution, not historical schema or rendering.

    ``ob`` names the semantic persistence requirement in the shared inventory;
    the owned-local adapter explicitly substitutes SQLite, never remote OB.
    New mandatory sources require an executor/preflight contract, not just JSON.
    """
    contracts = _REQUIRED.get(manifest['suite'])
    if contracts is None:
        raise RunStateError('unsupported suite evidence contract')
    for case in manifest['cases']:
        required = set(case['required_evidence'])
        diagnostic = case.get('diagnostic_evidence')
        if (case['id'] not in contracts or required != contracts[case['id']]):
            raise RunStateError('Case required evidence differs from its executor contract')
        if (not isinstance(diagnostic, list) or len(diagnostic) > len(_DIAGNOSTIC)
                or any(not isinstance(item, str) or item not in _DIAGNOSTIC for item in diagnostic)
                or len(set(diagnostic)) != len(diagnostic)
                or required.intersection(diagnostic)):
            raise RunStateError('Case diagnostic evidence is invalid or unsupported')
