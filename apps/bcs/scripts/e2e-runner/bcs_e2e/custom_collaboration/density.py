"""Atomic S14 business clauses, separate from the three frozen parent IDs.

One vocabulary, one structure: the 34 atoms use the flat
``density_requirements`` rows of custom-collaboration-migration-contract.json
(IDs anchored to cli-stories.sh lines).  A missing event is NOT_RUN, never a
fabricated PASS; a present-but-failing observation is FAIL.  Definition
clauses only prove the submitted, successfully validated frozen fixture, not
runtime consumption.  The owned-local runner and the dual product-CLI
executor both feed the same event/bindings contract into ``project``.
The three frozen parent obligations ride first with
``legacy-parent`` evidence; their statuses come from the caller's
``base_statuses``; the 34 atomic rows follow.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..local_operator_cli_contract import (
    CUSTOM_COLLABORATION_REQUIREMENTS,
    custom_collaboration_source_binding,
)
from ..run_state import RunStateError

PREFIX = 'CUSTOM-COLLABORATION.'
# Flat contract atom IDs (cli-stories.sh anchors live on each row).
ATOMS = (
    'yaml-valid', 'yaml-role-count', 'yaml-node-count', 'yaml-planner-required',
    'yaml-reviewer-required', 'yaml-plan-assignee', 'yaml-review-assignee',
    'yaml-acyclic', 'yaml-plan-review-edge', 'yaml-review-final',
    'trial-group-id', 'trial-session-id', 'human-member', 'human-consultant',
    'permission-allowed', 'permission-session', 'permission-group',
    'run-id', 'run-session', 'run-group', 'graph-run-id', 'pending-human-empty',
    'respond-refused', 'respond-not-pending', 'panel-component', 'panel-run',
    'cancel-terminal', 'session-running', 'persistent-id', 'persistent-driver',
    'persistent-reviewer', 'persistent-strategy', 'trial-cleanup',
    'persistent-cleanup',
)
# The seven submitted-fixture syntax clauses ride on the frozen definition
# (case-owned, hashed); the three round-trip atoms ride on the live response.
DEFINITION_SNIPPETS = {
    'yaml-planner-required': '  planner:\n    display_name: Release planner\n    required: true',
    'yaml-reviewer-required': '  reviewer:\n    display_name: Engineering reviewer\n    required: true',
    'yaml-acyclic': '    graph_mode: acyclic',
    'yaml-plan-assignee': '      plan:\n        kind: bot_task\n        display_name: Plan release\n        assignee:\n          type: bot_binding\n          binding: planner',
    'yaml-review-assignee': '      review:\n        kind: bot_task\n        display_name: Review release\n        assignee:\n          type: bot_binding\n          binding: reviewer',
    'yaml-plan-review-edge': '        transitions:\n          complete:\n            targets: [review]',
    'yaml-review-final': '        final_output: true',
}


def requirements() -> list[dict]:
    """Flat 34-atom obligation rows, source-anchored, single structure."""
    binding = custom_collaboration_source_binding()
    rows = binding.get('density_requirements')
    if (not isinstance(rows, list) or len(rows) != len(ATOMS)
            or [row['id'] for row in rows] != [PREFIX + key for key in ATOMS]):
        raise RunStateError('custom collaboration density clause set changed')
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('method'), str)
                or not isinstance(row.get('expectation'), str) or not row['expectation']
                or not isinstance(row.get('command'), str) or not row['command']):
            raise RunStateError('custom collaboration density obligation row changed')
    return [dict(row) for row in rows]


def parent_requirements() -> list[dict]:
    """Frozen three parent obligations (contract source of truth)."""
    binding = custom_collaboration_source_binding()
    rows = binding.get('requirements')
    if (not isinstance(rows, list)
            or len(rows) != len(CUSTOM_COLLABORATION_REQUIREMENTS)
            or [row.get('id') for row in rows]
            != list(CUSTOM_COLLABORATION_REQUIREMENTS)):
        raise RunStateError('custom collaboration parent obligation set changed')
    return [dict(row) for row in rows]


def definition_evidence(text: str) -> dict:
    """Check only the frozen submitted fixture; do not parse arbitrary YAML."""
    from ..local_custom_collaboration_run import DEFINITION_YAML
    return {'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'canonical': text == DEFINITION_YAML,
            'clauses': {key: snippet in text for key, snippet in DEFINITION_SNIPPETS.items()}}


def panel_evidence(history: list, run_id: str) -> dict:
    """Require the panel marker and the Run in the same message record."""
    panels = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in history
              if isinstance(item, dict)
              and 'bcsPanel.StateMachineRunView' in json.dumps(item, ensure_ascii=False)]
    return {'panel_present': bool(panels),
            'panel_run_bound': bool(run_id) and any(run_id in panel for panel in panels)}


def project(events: dict, bindings: dict, base_statuses: dict) -> dict:
    checks: dict[str, tuple[str, bool]] = {}

    def check(key: str, event: str, predicate: bool) -> None:
        checks[key] = (event, predicate is True)

    def identity(value: object, expected: object) -> bool:
        return isinstance(expected, str) and bool(expected) and value == expected

    valid = events.get('validate', {})
    summary = valid.get('summary', {}) if isinstance(valid, dict) else {}
    definition = events.get('definition', {})
    canonical = definition.get('canonical') is True and definition.get('clauses', {})
    syntax_pass = valid.get('valid') is True
    check('yaml-valid', 'validate', syntax_pass)
    check('yaml-role-count', 'validate',
          type(summary.get('participants')) is int and summary.get('participants') == 2)
    check('yaml-node-count', 'validate',
          type(summary.get('nodes')) is int and summary.get('nodes') == 2)
    for key in ('yaml-planner-required', 'yaml-reviewer-required', 'yaml-plan-assignee',
                'yaml-review-assignee', 'yaml-acyclic', 'yaml-plan-review-edge',
                'yaml-review-final'):
        check(key, 'definition', syntax_pass and canonical.get(key) is True)
    trial_group = bindings.get('trial_group') or ''
    trial_session = bindings.get('trial_session') or ''
    run_id = bindings.get('run_id') or ''
    persistent = bindings.get('persistent_group') or ''
    trial = events.get('trial', {})
    check('trial-group-id', 'trial', bool(trial_group))
    check('trial-session-id', 'trial', identity(trial.get('session_id'), trial_session))
    member = events.get('member', {})
    rows = member.get('participants', []) if isinstance(member, dict) else []
    human_rows = [r for r in rows if isinstance(r, dict)
                  and (r.get('bot_uuid') or r.get('actor_id')) == bindings.get('human')]
    bound = identity(member.get('session_id') or member.get('id'), trial_session)
    check('human-member', 'member', bound and bool(human_rows))
    check('human-consultant', 'member', bound and any(r.get('role') == 'consultant' for r in human_rows))
    permission = events.get('permission', {})
    check('permission-allowed', 'permission', permission.get('allowed') is True)
    check('permission-session', 'permission', identity(permission.get('session_id'), trial_session))
    check('permission-group', 'permission', identity(permission.get('group_id'), trial_group))
    started = events.get('run', {})
    check('run-id', 'run', identity(started.get('run_id'), run_id))
    check('run-session', 'run', identity(started.get('session_id'), trial_session))
    check('run-group', 'run', identity(started.get('group_id'), trial_group))
    check('graph-run-id', 'graph', identity(events.get('graph', {}).get('run_id'), run_id))
    check('pending-human-empty', 'pending', events.get('pending') == [])
    guard = events.get('guard', {})
    check('respond-refused', 'guard', type(guard.get('exit_code')) is int and guard.get('exit_code') != 0)
    check('respond-not-pending', 'guard', guard.get('not_pending') is True)
    panel = events.get('panel', {})
    check('panel-component', 'panel', panel.get('panel_present') is True)
    check('panel-run', 'panel', panel.get('panel_run_bound') is True)
    cancel = events.get('cancel', {})
    cancel_run = cancel.get('run', {}) if isinstance(cancel, dict) else {}
    check('cancel-terminal', 'cancel', identity(cancel_run.get('run_id'), run_id)
          and cancel_run.get('status') in ('aborted', 'completed', 'failed'))
    preserved = events.get('session', {})
    check('session-running', 'session',
          identity(preserved.get('session_id') or preserved.get('id'), trial_session)
          and identity(preserved.get('group_id'), trial_group)
          and preserved.get('status') == 'running')
    deleted = events.get('trial_deleted', {})
    check('trial-cleanup', 'trial_deleted', identity(deleted.get('id'), trial_group)
          and deleted.get('deleted') is True)
    created = events.get('persistent', {})
    participants = created.get('participants', []) if isinstance(created, dict) else []
    ids = [r if isinstance(r, str) else (r.get('bot_uuid') or r.get('actor_id')) for r in participants]
    check('persistent-id', 'persistent', identity(created.get('id'), persistent))
    check('persistent-driver', 'persistent', bindings.get('driver') in ids)
    check('persistent-reviewer', 'persistent', bindings.get('reviewer') in ids)
    readback = events.get('persistent_read', {})
    check('persistent-strategy', 'persistent_read', identity(readback.get('id'), persistent)
          and readback.get('group_strategy') == 'state_machine')
    removed = events.get('persistent_deleted', {})
    check('persistent-cleanup', 'persistent_deleted', identity(removed.get('id'), persistent)
          and removed.get('deleted') is True)
    rows_out = [
        {**row, 'status': base_statuses.get(row['id'], 'NOT_RUN'),
         'evidence_event': 'legacy-parent'}
        for row in parent_requirements()
    ]
    for requirement in requirements():
        key = requirement['id'][len(PREFIX):]
        if key in checks:
            event, passed = checks[key]
            status = 'NOT_RUN' if event not in events else ('PASS' if passed else 'FAIL')
        else:
            event, status = 'legacy-parent', base_statuses.get(requirement['id'], 'NOT_RUN')
        rows_out.append({**requirement, 'status': status, 'evidence_event': event})
    missing = [row['id'] for row in rows_out if row['status'] not in ('PASS', 'NOT_RUN', 'FAIL')]
    if missing:
        raise RunStateError('custom collaboration density projector emitted an invalid status')
    return {'schema_version': 1, 'contract': 'custom-collaboration-density-v2',
            'status': 'PASS' if all(row['status'] == 'PASS' for row in rows_out) else 'BLOCKED',
            'parsed_requirement_count': len(rows_out), 'legacy_parent_count': 3,
            'added_atomic_count': len(ATOMS), 'requirements': rows_out,
            'observations': events, 'bindings': bindings,
            'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
            'presence': 'REMOTE_ZOMBIE_UNRESOLVED_NOT_PROBED',
            'scope': ('Source atomic observations; definition syntax clauses bound to the '
                      'frozen case fixture; not Bot reasoning or remote presence proof')}


def verify(receipt: dict) -> bool:
    try:
        base = {r['id']: r['status'] for r in receipt['requirements'][:3]}
        return receipt == project(receipt['observations'], receipt['bindings'], base)
    except (KeyError, TypeError, ValueError, RunStateError):
        return False
