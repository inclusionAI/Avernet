"""Density projector for the group-workspace parent story.

Projects the 63 source-anchored atoms of ``group-workspace-density-contract.json``
onto the retained Workspace journals produced by the owned-local executors
(API values/reads, CLI exits/stdout, incident configuration, message exchange
and exact deletion).

Honesty rules (same as the custom-collaboration density seam):

- a missing event, a missing journal or an absent asserted field is
  ``NOT_RUN`` — never a fabricated PASS;
- present but contradicting evidence is ``FAIL``;
- the retired settings face admits both product forms explicitly (station
  ``410 + service_policy_retired`` and legacy ``200 + service_spec``), recording
  ``observed_form``;
- the projector never grants release credit and never mutates a journal.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit, parse_qs

from .profile import fingerprint
from .run_state import RunStateError

RUNNER_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = RUNNER_ROOT / 'group-workspace-density-contract.json'
STORY = 'story_user_operates_group_workspace'
CONTRACT_ID = 'group-workspace-density-v1'
ATOM_COUNT = 63
FAMILIES = ('api', 'cli', 'tail')
JOURNALS = ('api', 'cli', 'incident', 'messages', 'delete')
VALID_STATUS = ('PASS', 'FAIL', 'NOT_RUN')

_MISSING = object()
_POSIX_PARTS = {'groups': 1}


def _non_empty(observed) -> bool:
    """Shell-equivalent non-emptiness.

    The legacy harness asserts on ``$(json_field ...)`` strings, so any JSON
    scalar (including 0) serializes to a non-empty shell string. Containers
    still need actual length; only a missing field stays inconclusive.
    """
    if isinstance(observed, (str, list, dict)):
        return len(observed) > 0
    return observed is not None


def load_group_workspace_density_contract(path: Path | None = None) -> dict:
    return json.loads((path or CONTRACT_PATH).read_text(encoding='utf-8'))


def validate_group_workspace_density_contract(contract: dict) -> dict:
    if contract.get('schema_version') != 1 or contract.get('contract') != CONTRACT_ID:
        raise RunStateError('group workspace density contract identity changed')
    rows = contract.get('density_requirements')
    if not isinstance(rows, list) or len(rows) != ATOM_COUNT:
        raise RunStateError('group workspace density atom count changed')
    ids = [row.get('id') for row in rows]
    if len(set(ids)) != ATOM_COUNT or not all(isinstance(value, str) for value in ids):
        raise RunStateError('group workspace density atom ids changed')
    if sorted(row['family'] for row in rows) != sorted(
            ['api'] * 26 + ['cli'] * 11 + ['tail'] * 26):
        raise RunStateError('group workspace density family counts changed')
    for row in rows:
        for key in ('expectation', 'source_file', 'source_function', 'source_quote',
                    'address', 'checks'):
            if key not in row:
                raise RunStateError('group workspace density row lost its ' + key)
        if row['source_file'] not in ('group.sh', 'stories.sh'):
            raise RunStateError('group workspace density source file drifted')
    return contract


def workspace_density_bindings(identities, journals: dict,
                               roles: dict | None = None) -> dict:
    """Assemble runtime bindings: IDs only, never credentials.

    ``roles`` is a snapshot taken while the role identity context is still
    open; its close() clears the entries, so callers must pass the snapshot.
    Without it the projection falls back to the (possibly closed) identity
    object or to the retained fixture faces.
    """
    bindings: dict = {'primary': None, 'members': None, 'cli_primary': None,
                      'cli_termination': None, 'incident': None, 'roles': {}}
    api = journals.get('api') or {}
    cli = journals.get('cli') or {}
    incident = journals.get('incident') or {}
    messages = journals.get('messages') or {}
    delete = journals.get('delete') or {}
    for key, resource in (api.get('resources') or {}).items():
        if key == 'group-create-primary':
            bindings['primary'] = resource.get('id')
        elif key == 'group-create-with-members':
            bindings['members'] = resource.get('id')
    for key, resource in (cli.get('resources') or {}).items():
        if key == 'primary':
            bindings['cli_primary'] = resource.get('id')
        elif key == 'termination':
            bindings['cli_termination'] = resource.get('id')
    bindings['incident'] = ((incident.get('resources') or {}).get('incident') or {}).get('id') \
        or messages.get('group_id') or delete.get('group_id')
    if roles is None:
        if identities is not None and hasattr(identities, 'role_bot_ids'):
            roles = identities.role_bot_ids()
        else:
            roles = {}
            for actor in (api.get('fixture_visibility') or {}).get('actors') or []:
                if isinstance(actor, dict) and actor.get('role') and actor.get('bot_id'):
                    roles[actor['role']] = actor['bot_id']
            for event in cli.get('operations') or []:
                if (isinstance(event, dict) and event.get('operation') == 'fixture-qa-visibility-public'
                        and isinstance(event.get('path'), str)):
                    segments = event['path'].strip('/').split('/')
                    if len(segments) >= 2 and segments[-1] == 'visibility':
                        roles['QA'] = segments[-2]
    bindings['roles'] = roles
    return bindings


def bindings_capture(bindings, dotted: str):
    """Resolve a plain dotted binding name (e.g. ``roles.PM``) against bindings."""
    value = bindings
    for part in dotted.split('.'):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _resolve_binding(expected, bindings):
    if isinstance(expected, dict) and set(expected) == {'binding'}:
        dotted = expected['binding']
        value = bindings
        for part in dotted.split('.'):
            if not isinstance(value, dict) or part not in value:
                return _MISSING
            value = value[part]
        return value
    return expected


def _dig(source: dict, dotted: str):
    current = source
    for part in dotted.split('.'):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _read_matches(entry: dict, address: dict, bindings) -> bool:
    path = entry.get('path') or ''
    template = address.get('path_template') or ''
    if template == '/groups?list-all':
        if not path.startswith('/groups?'):
            return False
        query = parse_qs(urlsplit(path).query)
        return (query.get('group_kind') == ['all'] and query.get('limit') == ['50'])
    resolved = template
    for token in ('primary', 'members'):
        resolved = resolved.replace('{' + token + '}', quote(bindings.get(token) or '', safe=''))
    if path != resolved:
        return False
    after = address.get('after_operation')
    return after is None or entry.get('after_operation') == after


def _check(check: dict, context) -> tuple[bool, object]:
    """One predicate. Returns (passed, observed); `passed=None` means inconclusive."""
    kind = check.get('type')
    if kind == 'status_eq':
        observed = context['event'].get('http_status')
        return (observed == check['value'], observed)
    if kind == 'exit_zero':
        observed = context['event'].get('cli_exit_code')
        return (observed == 0, observed)
    if kind == 'field_eq':
        observed = _dig(context['event'], check['path'])
        expected = _resolve_binding(check.get('expected'), context['bindings'])
        if observed is _MISSING or expected is _MISSING:
            return (None, 'field_or_binding_absent:' + str(observed is _MISSING))
        return (observed == expected, observed)
    if kind == 'field_not_empty':
        observed = _dig(context['event'], check['path'])
        if observed is _MISSING:
            return (None, 'field_absent')
        passed = _non_empty(observed)
        return (passed, 'present' if passed else 'empty')
    if kind == 'read_present':
        return (context['read'] is not None, 'retained_read' if context['read'] is not None else 'no_read')
    if kind == 'read_field_eq':
        observed = _dig({'value': (context['read'] or {}).get('value')}, 'value.' + check['path'])
        expected = _resolve_binding(check.get('expected'), context['bindings'])
        if observed is _MISSING or expected is _MISSING:
            return (None, 'field_or_binding_absent')
        return (observed == expected, observed)
    if kind == 'read_field_not_empty':
        observed = _dig({'value': (context['read'] or {}).get('value')}, 'value.' + check['path'])
        if observed is _MISSING:
            return (None, 'field_absent')
        passed = _non_empty(observed)
        return (passed, 'present' if passed else 'empty')
    if kind == 'read_field_len_eq':
        observed = _dig({'value': (context['read'] or {}).get('value')}, 'value.' + check['path'])
        if observed is _MISSING or not isinstance(observed, list):
            return (None, 'field_absent_or_not_list')
        return (len(observed) == check['value'], len(observed))
    if kind == 'read_field_ge':
        observed = _dig({'value': (context['read'] or {}).get('value')}, 'value.' + check['path'])
        if observed is _MISSING or not isinstance(observed, int):
            return (None, 'field_absent_or_not_int')
        return (observed >= check['value'], observed)
    if kind == 'stdout_contains':
        expected = _resolve_binding(check.get('value'), context['bindings'])
        stdout = context['event'].get('stdout') or ''
        if expected is _MISSING:
            return (None, 'binding_absent')
        return (str(expected) in stdout, expected)
    if kind == 'stdout_contains_any':
        stdout = context['event'].get('stdout') or ''
        hits = [value for value in check['values'] if value in stdout]
        return (bool(hits), hits)
    if kind == 'stdout_regex':
        match = re.search(check['pattern'], context['event'].get('stdout') or '', re.MULTILINE)
        if not match:
            return (False, 'no_match')
        capture = check.get('capture_eq')
        if isinstance(capture, str):
            expected = bindings_capture(context['bindings'], capture)
        else:
            expected = _resolve_binding(capture, context['bindings'])
        if expected is _MISSING:
            return (None, 'binding_absent')
        return (match.group(1) == expected, match.group(1))
    if kind == 'any_of':
        outcomes = []
        for sub in check['checks']:
            passed, observed = _check(sub, context)
            if passed is True:
                return (True, observed)
            outcomes.append(observed)
        return (False, outcomes)
    if kind == 'settings_retired_form':
        event = context['event']
        status = event.get('http_status')
        value = _dig(event, 'value')
        if status == 410:
            return (True, {'observed_form': 'retired_410'})
        if status == 200 and isinstance(value, dict) and isinstance(value.get('service_spec'), dict):
            return (True, {'observed_form': 'legacy_service_spec'})
        return (False, {'observed_form': 'unexpected', 'http_status': status})
    if kind == 'settings_policy_form':
        event = context['event']
        status = event.get('http_status')
        value = _dig(event, 'value')
        if status == 410 and isinstance(value, dict) and value.get('error') == 'service_policy_retired':
            return (True, {'observed_form': 'retired_410', 'error': 'service_policy_retired'})
        if (status == 200 and isinstance(value, dict)
                and value.get('service_spec') == {'max_concurrency': 2}):
            return (True, {'observed_form': 'legacy_service_spec', 'service_spec': value['service_spec']})
        return (False, {'observed_form': 'unexpected', 'http_status': status})
    if kind == 'not_found_state':
        observed = context['event'].get('http_status')
        return (observed == check['value'], observed)
    raise RunStateError('unknown group workspace density predicate: ' + str(kind))


def _operation_event(journal: dict, operation: str):
    for event in journal.get('operations') or []:
        if isinstance(event, dict) and event.get('operation') == operation:
            return event
    return None


def _project_row(requirement: dict, journals: dict, bindings: dict) -> dict:
    address = requirement['address']
    journal_name = address.get('journal')
    journal = journals.get(journal_name) or {}
    observed = None
    if address.get('kind') == 'operation':
        event = _operation_event(journal, address.get('operation') or '')
        if not isinstance(event, dict) or event.get('status') != 'ACKNOWLEDGED':
            return {**requirement, 'status': 'NOT_RUN', 'evidence_event': address.get('operation'),
                    'observed': 'event_missing_or_unproven'}
        context = {'event': event, 'bindings': bindings, 'read': None, 'journal': journal}
        evidence = address.get('operation')
    elif address.get('kind') == 'read':
        selected = None
        for entry in journal.get('reads') or []:
            if isinstance(entry, dict) and _read_matches(entry, address, bindings):
                selected = entry
                break
        if selected is None:
            return {**requirement, 'status': 'NOT_RUN',
                    'evidence_event': 'read:' + (address.get('path_template') or '?'),
                    'observed': 'retained_read_missing'}
        context = {'event': {'value': selected.get('value')}, 'bindings': bindings,
                   'read': selected, 'journal': journal}
        evidence = 'read:' + selected.get('path', '?')
    else:
        state = journal.get(address.get('state_key') or '')
        context = {'event': state or {}, 'bindings': bindings, 'read': None, 'journal': journal}
        evidence = 'state:' + (address.get('state_key') or '?')
        if not isinstance(state, dict):
            return {**requirement, 'status': 'NOT_RUN', 'evidence_event': evidence,
                    'observed': 'state_missing'}
    statuses = []
    for check in requirement['checks']:
        passed, observation = _check(check, context)
        observed_observation = observation
        statuses.append(passed)
        if passed is False:
            return {**requirement, 'status': 'FAIL', 'evidence_event': evidence,
                    'observed': observed_observation, 'failed_check': check.get('type')}
        if passed is None:
            observed = observed if observed is not None else ('inconclusive:' + str(observed_observation))
        elif observation is not None:
            observed = observation
    if any(status is None for status in statuses):
        return {**requirement, 'status': 'NOT_RUN', 'evidence_event': evidence,
                'observed': observed or 'inconclusive'}
    return {**requirement, 'status': 'PASS', 'evidence_event': evidence,
            'observed': observed if observed is not None else 'confirmed'}


def project_density(contract: dict, journals: dict, bindings: dict) -> dict:
    """Project the frozen 63-atom table onto retained Workspace journals."""
    validate_group_workspace_density_contract(contract)
    journal_keys = {name: journals.get(name) for name in JOURNALS}
    rows = [_project_row(row, journal_keys, bindings)
            for row in contract['density_requirements']]
    counts = {status: sum(1 for row in rows if row['status'] == status)
              for status in VALID_STATUS}
    family_counts = {family: {status: sum(1 for row in rows
                                        if row['family'] == family and row['status'] == status)
                              for status in VALID_STATUS} for family in FAMILIES}
    if not any(journal_keys.values()):
        verdict = 'NOT_RUN'
    elif counts['FAIL']:
        verdict = 'BLOCKED'
    elif counts['PASS'] == ATOM_COUNT:
        verdict = 'PASS'
    else:
        verdict = 'INCOMPLETE'
    return {
        'schema_version': 1, 'contract': CONTRACT_ID, 'parent': STORY,
        'status': verdict, 'atom_count': ATOM_COUNT, 'counts': counts,
        'family_counts': family_counts, 'requirements': rows,
        'bindings': {'primary': bindings.get('primary'), 'members': bindings.get('members'),
                     'cli_primary': bindings.get('cli_primary'),
                     'cli_termination': bindings.get('cli_termination'),
                     'incident': bindings.get('incident'),
                     'roles': bindings.get('roles') or {}},
        'digest': fingerprint({'contract': CONTRACT_ID, 'rows': [row['id'] for row in rows]}),
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'scope': ('Retained owned-local journal projections of the frozen 63-atom table; '
                  'remote preprod projection and gate integration follow in G2'),
    }


def density_summary(density: dict) -> dict:
    if not isinstance(density, dict):
        return {'contract': CONTRACT_ID, 'status': 'ERROR'}
    return {'contract': density.get('contract', CONTRACT_ID),
            'status': density.get('status'),
            'atom_count': density.get('atom_count', ATOM_COUNT),
            'counts': density.get('counts') or {},
            'family_counts': density.get('family_counts') or {}}
