"""Version-one operator CLI reports projected from frozen evidence.

Digests detect drift, not a malicious rewrite by the owner of the
Run files. Offline verification never resolves identities or
contacts the old runtime. Observation plans pin every recorded
CLI command, HTTP path template and allowed observation key, so
the projection rejects foreign calls, leaked token-bearing URLs
and unknown observations without trusting the executor again.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import binding_matches_reviewed_release, executor_digest
from .local_operator_cli import (
    CLI_TIMEOUT_SECONDS,
    RESOURCE_DISPOSITION,
    ROLES,
)
from .local_operator_cli_contract import (
    CASE_ID,
    COMMANDS,
    PARENT,
    REQUIREMENTS,
    SUITE,
)
from .profile import fingerprint
from .run_state import RunStateError, read

SOURCES = ('operator-cli-binding.json', 'operator-cli-flow-report.json',
           'bcs-config.json', 'owned-bcs.json', 'local-fixtures.json',
           'local-operator-cli.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-operator-cli-completion-v1'

# Per-obligation observation plan: ('cli', command[, allowed-extra-keys...]),
# ('http', method, path-template[, 'observed']), or ('bot_ws', endpoint).
OBSERVATION_PLANS = (
    (('cli', 'bcs-cli health'),),
    (('cli', 'bcs-cli list'),),
    (('cli', 'bcs-cli get <uuid>', 'target'),),
    (('cli', 'bcs-cli discover --query', 'query'),),
    (('cli', 'bcs-cli visibility get'),
     ('cli', 'bcs-cli visibility set public'),
     ('cli', 'bcs-cli visibility get', 'observed'),
     ('cli', 'bcs-cli visibility set restore'),
     ('cli', 'bcs-cli visibility get', 'observed')),
    (('cli', 'bcs-cli connect'),),
    (('cli', 'bcs-cli onboard --web', 'probe_suffix'),
     ('http', 'GET', '/bots?onboarded=true')),
    (('http', 'GET', '/register/token'),
     ('http', 'POST', '/register'),
     ('cli', 'bcs-cli onboard'),
     ('http', 'GET', '/bots/{id}'),
     ('http', 'DELETE', '/bots/{id}'),
     ('http', 'GET', '/bots/{id}')),
    (('http', 'GET', '/bots/{id}', 'observed'),
     ('cli', 'bcs-cli update-status idle'),
     ('http', 'GET', '/bots/{id}', 'observed'),
     ('cli', 'bcs-cli update-status busy')),
    (('cli', 'bcs-cli create-group'),
     ('cli', 'bcs-cli get-group'),
     ('http', 'DELETE', '/groups/{id}?bot_id={driver}'),
     ('http', 'GET', '/groups/{id}')),
    (('bot_ws', '/ws/bot'),
     ('http', 'POST', '/friends/request'),
     ('http', 'POST', '/friends/requests/{id}/accept'),
     ('cli', 'bcs-cli chat', 'detach_fallback')),
    (('cli', 'bcs-cli list-groups'),),
    (('http', 'POST', '/friends/request'),
     ('http', 'POST', '/friends/requests/{id}/accept'),
     ('cli', 'bcs-cli friend list')),
)
ASSERTION_COUNT = 13


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(),
             'Operator CLI evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_size <= 8*1024*1024,
                 'Operator CLI evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Operator CLI evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
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
    return {name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}


def _bound(root):
    binding = read(root/'operator-cli-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root)
             and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == PARENT
             and [r['id'] for r in binding['source_binding']['requirements']] == list(REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', []))
             and binding.get('store_messages') is False
             and isinstance(binding.get('cli_binary_sha256'), str)
             and len(binding['cli_binary_sha256']) == 64
             and all(c in '0123456789abcdef' for c in binding['cli_binary_sha256']),
             'Operator CLI frozen complete binding is invalid')
    return binding


def _valid_observation(index, position, observation):
    plan = OBSERVATION_PLANS[index][position]
    if not isinstance(observation, dict):
        return False
    # Every observation carries 'kind' plus the fixed key set its
    # transport always records, plus only the plan's allowed extras.
    if plan[0] == 'cli':
        if set(observation) != {'kind', 'command', 'exit_code'} | set(plan[2:]):
            return False
        return (observation['kind'] == 'cli'
                and observation['command'] == plan[1]
                and type(observation.get('exit_code')) is int)
    if plan[0] == 'http':
        # The plan tuple is (kind, method, path-template[, extras]):
        # the path template is data, not an observation key, so
        # allowed extras begin at plan[3:].
        if set(observation) != {'kind', 'method', 'path',
                                'http_status'} | set(plan[3:]):
            return False
        if (observation['kind'] != 'http' or observation['method'] != plan[1]
                or observation['path'] != plan[2]
                or type(observation.get('http_status')) is not int):
            return False
        if 'observed' in observation and not isinstance(observation['observed'], str):
            return False
        return True
    if plan[0] == 'bot_ws':
        if set(observation) != {'kind', 'endpoint', 'request_id'} | set(plan[2:]):
            return False
        return (observation['kind'] == 'bot_ws'
                and observation['endpoint'] == plan[1]
                and isinstance(observation.get('request_id'), str)
                and observation['request_id'])


def _business_evidence(root, binding, case):
    operations = case.get('operations')
    role_bot_ids = case.get('role_bot_ids')
    if (not isinstance(operations, list) or not isinstance(role_bot_ids, dict)
            or case.get('status') != 'LOCAL_OPERATOR_CLI_PASS'
            or case.get('assertions') != [{'id': name, 'status': 'PASS'}
                                             for name in REQUIREMENTS]
            or case.get('run_id') != binding['run_id']
            or case.get('case_id') != CASE_ID
            or case.get('executor_digest') != binding['executor_digest']
            or case.get('binding_digest') != fingerprint(binding)
            or case.get('source_binding_digest') != fingerprint(binding['source_binding'])
            or case.get('binding_digest') != fingerprint(binding)):
        return False
    if (case.get('human_actor') != case.get('human_actor')
            or not isinstance(case.get('human_actor'), str)
            or not case['human_actor'].startswith('human_')
            or case.get('driver_bot_id') != role_bot_ids.get('CEO')
            or case.get('fixture_roles') != list(ROLES)
            or set(role_bot_ids) != set(ROLES)
            or len(set(role_bot_ids.values())) != len(ROLES)
            or not isinstance(case.get('fixture_names'), dict)
            or set(case['fixture_names']) != set(ROLES)
            or case.get('cli_binary_sha256') != binding['cli_binary_sha256']
            or case.get('cli_timeout_seconds') != CLI_TIMEOUT_SECONDS):
        return False
    if len(operations) != ASSERTION_COUNT:
        return False
    for index, row in enumerate(operations):
        plan = OBSERVATION_PLANS[index]
        if (set(row) != {'operation', 'cli_command', 'status', 'transitions',
                         'observations'}
                or row['operation'] != REQUIREMENTS[index]
                or row['cli_command'] != COMMANDS[index]
                or row['status'] != 'VERIFIED'
                or row['transitions'] != ['INTENDED', 'VERIFIED']
                or not isinstance(row['observations'], list)
                or len(row['observations']) != len(plan)):
            return False
        for position, observation in enumerate(row['observations']):
            if not _valid_observation(index, position, observation):
                return False
    status_round_trip = case.get('status_round_trip')
    visibility = case.get('visibility_round_trip')
    direct = case.get('direct_onboard')
    group_create = case.get('group_create')
    chat = case.get('chat')
    friendship = case.get('friendship')
    if (not isinstance(status_round_trip, dict)
            or status_round_trip.get('registry_status') not in ('online', 'offline')
            or status_round_trip.get('dynamic_status') not in ('active', 'offline')
            or status_round_trip.get('broadcast') != 'idle'
            or status_round_trip.get('restore') != 'busy'
            or status_round_trip.get('registry_status_unchanged') is not True):
        return False
    if (not isinstance(visibility, dict)
            or visibility.get('original') not in ('public', 'protected', 'private')
            or visibility.get('round_trip_verified') is not True):
        return False
    if (not isinstance(direct, dict)
            or not isinstance(direct.get('bot_id'), str) or not direct['bot_id']
            or not isinstance(direct.get('bot_name'), str) or not direct['bot_name']
            or direct.get('onboarded') is not True
            or direct.get('disposition') != 'DELETED_404_VERIFIED'
            or direct['bot_id'] in role_bot_ids.values()):
        return False
    if (not isinstance(group_create, dict)
            or not isinstance(group_create.get('topic'), str) or not group_create['topic']
            or not isinstance(group_create.get('group_id'), str) or not group_create['group_id']
            or group_create.get('driver_bot') != role_bot_ids.get('PM')
            or group_create.get('participants') != [role_bot_ids.get('PM'), role_bot_ids.get('ENG')]
            or group_create.get('disposition') != 'DELETED_404_VERIFIED'):
        return False
    prerequisite = (chat or {}).get('prerequisite')
    connection = (prerequisite or {}).get('connection')
    if (not isinstance(chat, dict)
            or not isinstance(chat.get('run_id'), str) or not chat['run_id']
            or not isinstance(chat.get('session_id'), str) or not chat['session_id']
            or type(chat.get('detach_fallback')) is not bool
            or chat.get('disposition') != 'OWNED_RUNTIME_SCOPED'
            or not isinstance(prerequisite, dict)
            or prerequisite.get('caller_bot_id') != role_bot_ids['CEO']
            or prerequisite.get('target_bot_id') != role_bot_ids['PM']
            or not isinstance(prerequisite.get('request_id'), str)
            or not prerequisite['request_id']
            or prerequisite.get('admission') != 'ACCEPTED'
            or not isinstance(connection, dict)
            or connection.get('endpoint') != '/ws/bot'
            or connection.get('bot_id') != role_bot_ids['PM']
            or not isinstance(connection.get('request_id'), str)
            or not connection['request_id']
            or connection.get('reconnected') is not True):
        return False
    if (not isinstance(friendship, dict)
            or friendship.get('caller_bot_id') != role_bot_ids['CEO']
            or friendship.get('target_bot_id') != role_bot_ids['ENG']
            or not isinstance(friendship.get('request_id'), str) or not friendship['request_id']
            or friendship.get('admission') != 'ACCEPTED'):
        return False
    resources = case.get('resources')
    if (not isinstance(resources, dict)
            or set(resources) != {'created', 'deleted', 'unknown'}
            or resources['unknown'] != []):
        return False
    created = resources['created']
    deleted = resources['deleted']
    if not isinstance(created, list) or not isinstance(deleted, list):
        return False
    created_kinds = {row.get('kind') if isinstance(row, dict) else None
                     for row in created}
    deleted_kinds = {row.get('kind') if isinstance(row, dict) else None
                     for row in deleted}
    if (not {'bot', 'group', 'chat_run', 'friendship'} <= created_kinds
            or not {'bot', 'group'} <= deleted_kinds):
        return False
    for row in created + deleted:
        if (not isinstance(row, dict) or set(row) != {'kind', 'id', 'disposition'}
                or not isinstance(row['id'], str) or not row['id']
                or not isinstance(row['disposition'], str) or not row['disposition']):
            return False
    for resource, kind in ((direct['bot_id'], 'bot'), (group_create['group_id'], 'group')):
        matches = [row for row in created if row['kind'] == kind and row['id'] == resource]
        if len(matches) != 1 or matches[0]['disposition'] != 'deleted':
            return False
        if not any(row['kind'] == kind and row['id'] == resource
                   and row['disposition'] == 'deleted' for row in deleted):
            return False
    chat_run_ids = [row['id'] for row in created if row['kind'] == 'chat_run']
    if len(chat_run_ids) != 1 or chat_run_ids[0] != chat['run_id']:
        return False
    friendship_ids = [row['id'] for row in created if row['kind'] == 'friendship']
    expected_pairs = {role_bot_ids['CEO']+':'+role_bot_ids['PM'],
                      role_bot_ids['CEO']+':'+role_bot_ids['ENG']}
    if (set(friendship_ids) != expected_pairs
            or len(friendship_ids) != 2
            or any(row['disposition'] != 'owned-runtime-scoped'
                   for row in created if row['kind'] == 'friendship')):
        return False
    return case.get('resource_disposition') == RESOURCE_DISPOSITION


def _project(root, binding):
    flow = read(root/'operator-cli-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = (read(root/'local-operator-cli.json')
            if (root/'local-operator-cli.json').exists() else {})
    _require(flow.get('run_id') == binding['run_id']
             and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case
             and flow.get('remote_validation') == 'NOT_RUN',
             'Operator CLI final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Operator CLI runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_OPERATOR_CLI_PASS'
                and assertions == expected
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = (runtime.get('status') == 'STOPPED'
               and type(runtime.get('returncode')) is int
               and runtime['returncode'] == 0)
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared
              and flow.get('status') == 'LOCAL_OPERATOR_CLI_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else
              'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next(
                (r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                for name in REQUIREMENTS], 'assertions': assertions}


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID],
                      'legacy_inventory_count': len(binding['legacy_inventory']),
                      'full_release_status': 'NOT_QUALIFIED',
                      'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS'
                      else 'NOT_QUALIFIED',
                      'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition',
                                                             'UNVERIFIED')}],
            'requirements': projected.get('requirements',
                                          [{'id': name, 'status': 'NOT_RUN'}
                                           for name in REQUIREMENTS]),
            'reports': {'expected_assertion_count': ASSERTION_COUNT,
                        'observed_assertion_count':
                            len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
            'routes': [],
            'cleanup': {'verification_scope': 'OPERATOR_CLI_OWNED_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED',
                        'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization',
                                             'Bot engine reasoning',
                                             'actual message delivery',
                                             'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
            'ready_to_execute': False}


def publish_operator_cli(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Operator CLI source changed before report publication')
    _require(read(root/'operator-cli-flow-report.json') == flow
             and read(root/'owned-bcs.json') == runtime._record(),
             'Operator CLI publication must follow actual runtime finalization')
    pending = root/'operator-cli-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in SOURCES}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION,
                  'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'operator-cli-completion.json', completion)
    _require(_sources(root) == set(hashes)
             and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root/'operator-cli-completion.json') == completion_digest,
             'Operator CLI source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_operator_cli_report(root: Path):
    _require(not root.is_symlink(), 'Operator CLI report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'operator-cli-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),
                 'Operator CLI publication is incomplete')
        completion = read(root/'operator-cli-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Operator CLI completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Operator CLI evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'operator-cli-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Operator CLI evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Operator CLI completion, source or output evidence is missing, '
                      'changed or invalid')
    return _summary(binding, projected, issues)
