"""Version-one authority reports projected from frozen owned evidence.

The owned adapter held the only credential-bearing traffic; this offline
projection only ever hashes the bounded journals and checks the frozen run
receipts against the controlled BCS runtime record. Digests detect drift,
not a malicious rewrite by the owner of the Run files. This slice is
promoted into the reviewed release denominator, so the projection never
claims release credit and keeps remote validation open.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import binding_matches_reviewed_release, executor_digest
from .local_authority_contract import (
    CALLS, CASE_ID, COMPLETION, EXPECTED_HTTP_CALLS, PARENT, PRIMARY,
    REQUIREMENTS, STAFF_B, STAFF_MINE, STATUS, SUITE,
)
from .profile import fingerprint
from .run_state import RunStateError, read

BINARY_KIND = 'bcs'
STRICT_AUTH = {'chain': ['local', 'session'], 'require_authentication': False,
               'allow_mock_headers': True}
FORBIDDEN_JOURNAL_SUBSTRINGS = ('bot_token', 'X-Avernet-Principal', 'register?token')
SOURCES = ('authority-binding.json', 'authority-flow-report.json', 'bcs-config.json',
           'owned-bcs.json', 'local-authority.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
EXPECTED_ASSERTIONS = 16


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Authority evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8 * 1024 * 1024,
                 'Authority evidence must be a bounded regular unshared file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024, 'Authority evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(raw).hexdigest()


def _sources(root):
    return {name for name in SOURCES if (root / name).is_symlink() or (root / name).exists()}


def _hex64(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in '0123456789abcdef' for character in value))


def _bound(root):
    binding = read(root / 'authority-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('case_id') == CASE_ID and binding.get('run_id')
             and binding.get('runtime_dir') == str(root.resolve())
             and binding.get('binary_kind') == BINARY_KIND and binding.get('auto_build') is False
             and binding.get('store_messages') is False
             and binding.get('executor_digest') == executor_digest()
             and binding.get('selected_requirement_ids') == list(REQUIREMENTS)
             and _hex64(binding.get('bcs_binary_sha256'))
             and binding.get('environment') == {'local': 'LOCAL_EXECUTOR_ONLY',
                                                'preprod': 'REMOTE_UNSUPPORTED'}
             and binding.get('source_binding', {}).get('parent') == PARENT
             and binding.get('source_binding', {}).get('suite') == SUITE
             and binding.get('source_binding', {}).get('case_id') == CASE_ID
             and binding.get('source_binding', {}).get('release_parent_denominator') is True
             and binding.get('source_binding', {}).get('independent_slice') is False
             and binding.get('source_binding', {}).get('remote_validation') == 'NOT_RUN'
             and binding.get('source_binding', {}).get('grants_release_credit') is False
             and binding.get('source_binding', {}).get('evidence_granularity') == 'OWNED_PARENT_JOURNAL'
             and [row['id'] for row in binding['source_binding']['requirements']] == list(REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Authority frozen binding is invalid')
    return binding


def _render(template, case):
    if template is None:
        return None
    value = template
    created = case['resources']['created']
    for token in ('{bot_id}', '{owner_user_id}', '{target_user_id}',
                  '{mine_user_id}', '{never_user_id}', '{target_owner_actor_id}'):
        if token not in value:
            continue
        actual = {'{bot_id}': created[0],
                  '{owner_user_id}': case['owner_human_id'],
                  '{target_user_id}': STAFF_B,
                  '{mine_user_id}': STAFF_MINE,
                  '{never_user_id}': case['never_user_id'],
                  '{target_owner_actor_id}': 'human_' + STAFF_B}[token]
        _require(isinstance(actual, str) and actual, 'authority template used an unbound identity')
        value = value.replace(token, actual)
    _require('{' not in value and '}' not in value,
             'authority template left an unbound placeholder')
    return value


def _flow(root):
    path = root / 'authority-flow-report.json'
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _case(root):
    path = root / 'local-authority.json'
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _runtime(root):
    path = root / 'owned-bcs.json'
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _config(root):
    # The BCS server configuration is not a run-state file: it carries no
    # schema_version, so it gets a bounded JSON parse instead of run read.
    path = root / 'bcs-config.json'
    if path.is_symlink() or not path.exists():
        return {}
    raw = path.read_bytes()
    _require(len(raw) <= 8 * 1024 * 1024, 'Authority configuration evidence exceeded byte limit')
    return json.loads(raw.decode('utf-8'))


def _call_expectation(requirement, index):
    method, template, expect_status, actor, query = CALLS[requirement][index]
    return method, template, expect_status, actor, query


def _matrix(case):
    """Validate the replayed 24-call V1 matrix against the frozen contract."""
    operations = case.get('operations', [])
    if len(operations) != len(REQUIREMENTS):
        return False
    request_ids = []
    for position, requirement in enumerate(REQUIREMENTS):
        row = operations[position]
        method, template, actor = PRIMARY[requirement]
        if (row.get('operation') != requirement or row.get('method') != method
                or row.get('actor') != actor or row.get('path') != _render(template, case)):
            return False
        if row.get('status') != 'VERIFIED' or row.get('transitions') != ['INTENDED', 'VERIFIED']:
            return False
        expected_calls = CALLS[requirement]
        calls = row.get('calls', [])
        if len(calls) != len(expected_calls):
            return False
        for index, expected in enumerate(expected_calls):
            call = calls[index]
            method, template, expect_status, label, query = expected
            if (call.get('method') != method or call.get('path') != _render(template, case)
                    or call.get('expect_status') != expect_status or call.get('actor') != label
                    or call.get('query') != query or call.get('http_status') != expect_status
                    or call.get('status') != 'VERIFIED'
                    or call.get('transitions') != ['INTENDED', 'VERIFIED']):
                return False
            submitted = call.get('submitted_request_id')
            if not isinstance(submitted, str) or not submitted:
                return False
            request_ids.append(submitted)
            if not _call_observation(requirement, index, call, case):
                return False
    return len(request_ids) == EXPECTED_HTTP_CALLS and len(set(request_ids)) == EXPECTED_HTTP_CALLS


def _call_observation(requirement, index, call, case):
    observed = call.get('observed')
    created = case.get('resources', {}).get('created', [])
    if requirement == 'AUTHORITY.trusted-registration':
        if index % 2 == 0:
            return observed == {'token_minted': True}
        return (isinstance(observed, dict) and observed.get('bot_registered') is True
                and isinstance(observed.get('bot_uuid'), str) and bool(observed['bot_uuid'])
                and observed['bot_uuid'] in created)
    if not isinstance(observed, dict):
        return False
    if requirement == 'AUTHORITY.ownership-read':
        return observed == {'bot_id': created[0] if created else None,
                            'owner_user_id': case.get('owner_human_id')}
    if requirement == 'AUTHORITY.empty-manager-page':
        return observed == {'total': 0}
    if requirement == 'AUTHORITY.owner-not-manager-polarity':
        return observed == {'error_code': 'owner_not_manager'}
    if requirement == 'AUTHORITY.manager-add-list-remove-idempotent':
        if index == 0:
            return observed == {'added': 1}
        if index == 1:
            return (observed == {'total': 1, 'items': [STAFF_B]}
                    and STAFF_B in observed.get('items', []))
        if index == 2:
            return observed == {'removed': 1}
        return observed == {'removed': 0}
    if requirement == 'AUTHORITY.manager-restore':
        return observed == {'added': 1}
    if requirement == 'AUTHORITY.mine-ensured-human-read':
        return observed == {'mine_user_id': STAFF_MINE}
    if requirement == 'AUTHORITY.mine-ensured-manager-add':
        return observed == {'added': 1}
    if requirement == 'AUTHORITY.never-seen-manager-unknown':
        return observed == {'error_code': 'bot_authority_human_unknown'}
    if requirement == 'AUTHORITY.mine-ensured-manager-remove':
        return observed == {'removed': 1}
    if requirement == 'AUTHORITY.transfer-initiate-refresh':
        if index == 0:
            return (observed == {'status': 'pending', 'target_owner_user_id': STAFF_B,
                                 'expires_at': observed.get('expires_at')}
                    if isinstance(observed.get('expires_at'), int) else False)
        return isinstance(observed.get('expires_at'), int)
    if requirement == 'AUTHORITY.transfer-read-accept':
        return observed == ({'status': 'pending'} if index == 0 else {'status': 'accepted'})
    if requirement == 'AUTHORITY.transferred-ownership':
        return observed == {'bot_id': created[0] if created else None,
                            'owner_user_id': STAFF_B}
    if requirement == 'AUTHORITY.transferred-manager-ledger':
        items = observed.get('manager_items')
        return (observed.get('owner_user_id') == STAFF_B and isinstance(items, list)
                and case.get('owner_human_id') in items)
    if requirement == 'AUTHORITY.transfer-inboxes':
        return observed == {'contains_bot': True}
    return False


def _observations(case):
    """Validate the per-obligation observed projections and their literals."""
    operations = case.get('operations', [])
    created = case.get('resources', {}).get('created', [])
    expected = {
        'AUTHORITY.trusted-registration': lambda row: (
            row == {'bots': created} and len(created) == 2
            and all(isinstance(item, str) and item for item in created)
            and created[0] != created[1]),
        'AUTHORITY.ownership-read': lambda row: row == {'bot_id': created[0],
                                                        'owner_user_id': case['owner_human_id']},
        'AUTHORITY.empty-manager-page': lambda row: row == {'total': 0},
        'AUTHORITY.owner-not-manager-polarity': lambda row: row == {'error_code': 'owner_not_manager'},
        'AUTHORITY.manager-add-list-remove-idempotent': lambda row: row == {'added': 1, 'total': 1,
                                                                            'removed': 1, 'removed_absent': 0},
        'AUTHORITY.manager-restore': lambda row: row == {'added': 1},
        'AUTHORITY.mine-ensured-human-read': lambda row: row == {'mine_user_id': STAFF_MINE},
        'AUTHORITY.mine-ensured-manager-add': lambda row: row == {'added': 1},
        'AUTHORITY.never-seen-manager-unknown': lambda row: row == {'error_code': 'bot_authority_human_unknown'},
        'AUTHORITY.mine-ensured-manager-remove': lambda row: row == {'removed': 1},
        'AUTHORITY.transfer-initiate-refresh': lambda row: (
            row == {'status': 'pending', 'target_owner_user_id': STAFF_B,
                    'initiated_expires_at': row.get('initiated_expires_at'),
                    'refreshed_expires_at': row.get('refreshed_expires_at')}
            if isinstance(row.get('initiated_expires_at'), int)
            and isinstance(row.get('refreshed_expires_at'), int) else False),
        'AUTHORITY.transfer-read-accept': lambda row: row == {'read_status': 'pending',
                                                              'accept_status': 'accepted'},
        'AUTHORITY.transferred-ownership': lambda row: row == {'bot_id': created[0],
                                                               'owner_user_id': STAFF_B},
        'AUTHORITY.transferred-manager-ledger': lambda row: (
            row.get('owner_user_id') == STAFF_B and isinstance(row.get('manager_items'), list)
            and case['owner_human_id'] in row['manager_items']),
        'AUTHORITY.transfer-inboxes': lambda row: row == {'received_contains_bot': True,
                                                          'sent_contains_bot': True},
        'AUTHORITY.story-scoped-disposition': lambda row: row == {'http_call_count': EXPECTED_HTTP_CALLS,
                                                                  'bots_retained': 2},
    }
    for requirement in REQUIREMENTS[:-1]:
        row = next((item for item in operations if item.get('operation') == requirement), None)
        if row is None or not expected[requirement](row.get('observed', {})):
            return False
    initiate = operations[10].get('observed', {})
    window = (isinstance(initiate.get('initiated_expires_at'), int)
              and isinstance(initiate.get('refreshed_expires_at'), int)
              and initiate['refreshed_expires_at'] >= initiate['initiated_expires_at']
              and initiate['refreshed_expires_at'] - initiate['initiated_expires_at'] < 300000)
    return bool(expected[REQUIREMENTS[-1]](operations[-1].get('observed', {}))) and window


def _project(root, binding):
    flow = _flow(root)
    case = _case(root)
    runtime = _runtime(root)
    config = _config(root)
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('binary_kind') == BINARY_KIND
             and flow.get('bcs_binary_sha256') == binding['bcs_binary_sha256']
             and flow.get('auto_build') is False,
             'Authority final result differs from its frozen declaration')
    _require(flow.get('case') == case and flow.get('shutdown') == runtime,
             'Authority journals disagree about the run result')
    expected = [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS]
    created = case.get('resources', {}).get('created', []) if case else []
    journals = (bool(case) and case.get('status') == STATUS
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and case.get('binding_digest') == fingerprint(binding)
                and case.get('source_binding_digest') == fingerprint(binding['source_binding'])
                and case.get('http_call_count') == EXPECTED_HTTP_CALLS
                and case.get('resource_disposition') == 'TWO_BOTS_CREATED_AND_RETAINED'
                and case.get('resources') == {'created': created, 'deleted': [], 'unknown': []}
                and isinstance(created, list) and len(created) == 2
                and all(isinstance(item, str) and item for item in created) and created[0] != created[1]
                and case.get('assertions') == expected
                and [row.get('operation') for row in case.get('operations', [])] == list(REQUIREMENTS)
                and case.get('owner_actor_id') == 'human_' + case.get('owner_human_id', '')
                and case.get('target_user_id') == STAFF_B
                and case.get('target_actor_id') == 'human_' + STAFF_B
                and case.get('mine_user_id') == STAFF_MINE
                and case.get('mine_actor_id') == 'human_' + STAFF_MINE
                and isinstance(case.get('never_user_id'), str) and bool(case['never_user_id']))
    matrix = journals and _matrix(case) and _observations(case)
    credentials = flow.get('credentials', {})
    flow_complete = (flow.get('status') == STATUS and flow.get('suite') == SUITE
                     and credentials == {'credential_memory': 'NOT_CAPTURED', 'fixtures': 'NONE',
                                         'role_identity': 'NONE', 'ambient_local_identity': False,
                                         'strict_mock_header_admission': True}
                     and flow.get('remote_validation') == 'NOT_RUN'
                     and flow.get('full_release_status') == 'NOT_QUALIFIED'
                     and flow.get('ready_to_execute') is False)
    runtime_complete = (runtime.get('run_id') == binding['run_id']
                        and runtime.get('binary_sha256') == binding['bcs_binary_sha256'])
    config_complete = (config.get('auth') == STRICT_AUTH and config.get('store_messages') is False)
    journal_clean = (root / 'local-authority.json').is_file() and not any(
        token in (root / 'local-authority.json').read_text() for token in FORBIDDEN_JOURNAL_SUBSTRINGS)
    complete = (bool(matrix) and bool(flow_complete) and bool(runtime_complete)
                and bool(config_complete) and journal_clean and set(SOURCES) == _sources(root))
    stopped = runtime.get('status') == 'STOPPED' and runtime.get('returncode') == 0
    cleared = credentials.get('credential_memory') == 'NOT_CAPTURED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == STATUS else
              'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'TWO_BOTS_CREATED_AND_RETAINED' if complete else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next(
                (row['status'] for row in case.get('assertions', []) if row.get('id') == name), 'NOT_RUN')}
                for name in REQUIREMENTS]}


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    case_requirements = projected.get('requirements', [])
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID],
                      'legacy_inventory_count': len(binding['legacy_inventory']),
                      'evidence_granularity': 'OWNED_PARENT_JOURNAL',
                      'independent_slice': False, 'release_parent_denominator': True,
                      'environment': binding['environment'],
                      'full_release_status': 'NOT_QUALIFIED',
                      'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                      'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest'],
                       'binary_kind': BINARY_KIND, 'auto_build': False,
                       'required_arguments': ['--binary', '--binary-sha256']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'requirements': case_requirements or [{'id': name, 'status': 'NOT_RUN'} for name in REQUIREMENTS],
            'reports': {'expected_assertion_count': EXPECTED_ASSERTIONS,
                        'observed_assertion_count': len(case_requirements)},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
            'routes': [],
            'cleanup': {'verification_scope': 'OWNED_LOCAL_RUNTIME_DIRECTORY',
                        'physical_data_erasure': 'FIXTURE_BOTS_RETAINED_BESIDE_STOPPED_RUNTIME',
                        'associated_history_purge': 'NOT_PERFORMED'},
            'verification_boundary': {'not_proven': [
                'remote authentication and authorization', 'Bot engine reasoning',
                'external engine completion', 'remote preprod reachability',
                'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
            'ready_to_execute': False}


def publish_authority_report(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Authority source changed before report publication')
    _require(read(root / 'authority-flow-report.json') == flow
             and read(root / 'owned-bcs.json') == runtime._record(),
             'Authority publication must follow the actual run result')
    pending = root / 'authority-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    # The black-box adapter owns the journal; a run blocked before its first
    # obligation legitimately publishes without it (mirroring the service
    # policy NOT_CREATED receipt). PASS still requires the frozen set.
    hashes = {name: _sha(root / name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root / 'authority-completion.json', completion)
    _require(_sources(root) == set(hashes)
             and all(_sha(root / name) == digest for name, digest in hashes.items())
             and all(_sha(root / name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root / 'authority-completion.json') == completion_digest,
             'Authority source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_authority_report(root: Path):
    _require(not root.is_symlink(), 'Authority report requires original non-symlink root')
    root = Path(root).resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root / 'authority-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),
                 'Authority publication is incomplete')
        completion = read(root / 'authority-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Authority completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS),
                 'Authority completion does not match the frozen evidence set')
        projected = _project(root, binding)
        _require(all(_sha(root / name) == digest for name, digest in hashes.items())
                 and all(_sha(root / name) == digest for name, digest in outputs.items())
                 and read(root / 'authority-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Authority evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Authority completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
