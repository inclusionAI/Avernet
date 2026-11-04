"""Reviewed remote admission for the retained small-File Case only.

The common Group/Session retained contract deliberately remains unchanged.  This
module adds one separately versioned review and a finite adapter surface for the
shared File steps; it grants no delete, token, WebSocket, update, stop or retry
capability.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from .adapters import RemotePilotAdapter
from .file_transport import RemoteFileClient, FileTransportError
from .profile import ConfigError, fingerprint, identifier, object_keys
from .retained_remote import (FILENAME, ProtectedReadClient, RetainedMcpClient,
                              execution_generation, preflight as common_preflight,
                              require_remote_profile, review_summary)
from .retention_quota import validate_quota
from .run_state import read, require_profile_binding, write

CONTRACT = 'supervised-retained-file-v2'
ROUTE_CONTRACT = {'api_face': 'internal-v1', 'resource': 'file'}
MAX_UPLOAD_BYTES = 4096


def _sha256(value: object, where: str) -> str:
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ConfigError(where + ' requires an exact sha256 digest')
    return value


def validate_file_review(value: dict, profile: dict, run_id: str, case_id: str) -> dict:
    require_remote_profile(profile)
    optional = {'execution_generation'} if isinstance(value, dict) and 'execution_generation' in value else set()
    object_keys(value, {'schema_version', 'contract', 'profile_digest', 'run_id', 'case_id',
        'reviewed_at', 'expires_at', 'supervisor', 'effect_review', 'file_effect_review',
        'retention_quota'} | optional, 'retained File review')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['contract'] != CONTRACT or value['profile_digest'] != fingerprint(profile)
            or value['run_id'] != run_id or value['case_id'] != case_id
            or case_id != 'RETAIN-FILE-01'):
        raise ConfigError('retained File review does not bind this v2 contract, Run, Case and complete profile')
    identifier(run_id, 'retained File Run ID')
    if value['supervisor'] != profile['human']['user_id']:
        raise ConfigError('retained File review requires the bound Human supervisor')
    if 'execution_generation' in value:
        if (not isinstance(value['execution_generation'], str)
                or value['execution_generation'] != execution_generation(profile)):
            raise ConfigError('retained File review execution generation must equal the reviewed Profile admission generation')

    # Reuse the exact common effect declaration without allowing an old control
    # receipt to admit File operations.
    common = object_keys(value['effect_review'], {'reference', 'sha256', 'deployment_commit',
        'isolated_test_runtime', 'no_operational_channels', 'implicit_bot_execution_allowed'},
        'retained effect review')
    identifier(common['reference'], 'retained review reference')
    _sha256(common['sha256'], 'retained effect review')
    if not isinstance(common['deployment_commit'], str) or re.fullmatch('[0-9a-f]{40}', common['deployment_commit']) is None:
        raise ConfigError('retained effect review needs an exact deployment commit')
    if any(common[key] is not True for key in ('isolated_test_runtime', 'no_operational_channels',
                                               'implicit_bot_execution_allowed')):
        raise ConfigError('retained effects require isolated test runtimes and explicit implicit-effect permission')

    start, end = value['reviewed_at'], value['expires_at']
    import time
    if (type(start) is not int or type(end) is not int or not 0 < end - start <= 3600
            or not start <= int(time.time()) < end):
        raise ConfigError('retained File review is expired, not yet valid or exceeds one hour')

    file_review = object_keys(value['file_effect_review'], {
        'storage_backend', 'allowed_storage_origins', 'storage_key_prefix', 'max_upload_bytes', 'completion',
        'notifications', 'explicit_share_allowed', 'source_review', 'route_contract'},
        'retained File effect review')
    origins = profile.get('file_transfer', {}).get('allowed_storage_origins')
    if (not isinstance(origins, list) or not origins
            or file_review['allowed_storage_origins'] != origins
            or file_review['storage_backend'] != 'baas'
            or type(file_review['max_upload_bytes']) is not int
            or file_review['max_upload_bytes'] != MAX_UPLOAD_BYTES
            or file_review['explicit_share_allowed'] is not True
            or file_review['route_contract'] != ROUTE_CONTRACT
            or profile['entrypoint']['manifest_face'] != 'internal-v1'):
        raise ConfigError('retained File effects require exact reviewed BaaS origins, 4096-byte limit and internal-v1 File route')
    prefix = file_review['storage_key_prefix']
    if (not isinstance(prefix, str) or not 1 <= len(prefix) <= 512 or not prefix.isascii()
            or prefix.startswith('/') or prefix.endswith('/') or '//' in prefix):
        raise ConfigError('retained File storage key prefix must be canonical reviewed components')
    components = prefix.split('/')
    if any(re.fullmatch('[A-Za-z0-9_-]+', component) is None for component in components):
        raise ConfigError('retained File storage key prefix must use restricted non-traversing components')
    completion = object_keys(file_review['completion'], {'implicit_share', 'share_ttl_days'},
                             'retained File completion effects')
    if (type(completion['implicit_share']) is not bool or completion['implicit_share'] is not True
            or type(completion['share_ttl_days']) is not int or completion['share_ttl_days'] != 15):
        raise ConfigError('retained File completion must declare its implicit 15-day share effect')
    notifications = file_review['notifications']
    expected_bots = [profile['actors']['driver']['bot_id'], profile['actors']['member']['bot_id']]
    if not isinstance(notifications, list) or len(notifications) != 2:
        raise ConfigError('retained File completion requires exactly two owned Bot Inject declarations')
    for item, bot_id in zip(notifications, expected_bots):
        entry = object_keys(item, {'bot_id', 'operation', 'allowed'}, 'retained File notification effect')
        if (entry['bot_id'] != bot_id or entry['operation'] != 'Inject'
                or type(entry['allowed']) is not bool or entry['allowed'] is not True):
            raise ConfigError('retained File completion requires exactly two owned Bot Inject declarations')
    source = object_keys(file_review['source_review'], {'reference', 'sha256'},
                         'retained File source review')
    identifier(source['reference'], 'retained File source review reference')
    _sha256(source['sha256'], 'retained File source review')
    quota = validate_quota(value['retention_quota'])
    from .retention_quota_v2 import validate_group_limit_authorization
    validate_group_limit_authorization(quota, profile, run_id)
    if quota['contract'] != 'retained-resource-quota-v2':
        raise ConfigError('retained File remote admission requires quota v2')
    return copy.deepcopy(value)


def file_review_summary(value: dict, profile: dict | None = None) -> dict:
    summary = review_summary(value, profile)
    file_review = value['file_effect_review']
    summary['file_effects'] = {
        'storage_backend': file_review['storage_backend'],
        'max_upload_bytes': file_review['max_upload_bytes'],
        'storage_key_prefix_digest': fingerprint(file_review['storage_key_prefix']),
        'route_contract': copy.deepcopy(file_review['route_contract']),
        'explicit_share': 'ALLOWED',
        'completion_implicit_share': {'allowed': True, 'ttl_days': 15, 'outcome': 'NOT_VERIFIED'},
        'completion_notifications': {
            'operation': 'Inject',
            'bot_ids': [item['bot_id'] for item in file_review['notifications']],
            'allowed': True,
            'delivery': 'NOT_VERIFIED',
        },
    }
    return summary


class FileRetainedReview:
    def __init__(self, profile: dict, run_id: str, case_id: str, path: Path):
        self.profile = copy.deepcopy(profile)
        self.run_id, self.case_id, self.path = run_id, case_id, path
        self.summary = file_review_summary(validate_file_review(read(path), self.profile, run_id, case_id), self.profile)
        self.state_dir: Path | None = None

    def validate(self) -> dict:
        value = validate_file_review(read(self.path), self.profile, self.run_id, self.case_id)
        if file_review_summary(value, self.profile) != self.summary:
            raise ConfigError('retained File review changed after admission; no automatic renewal')
        if self.state_dir is not None:
            state = read(self.state_dir / 'run.json')
            require_profile_binding(state, self.profile)
            if state.get('run_id') != self.run_id or state.get('retained_review') != self.summary:
                raise ConfigError('retained File review no longer matches the prepared Run')
        return value

    def freeze(self, state_dir: Path) -> None:
        value = self.validate()
        state = read(state_dir / 'run.json')
        require_profile_binding(state, self.profile)
        if state.get('run_id') != self.run_id or state.get('status') != 'PREPARED':
            raise ConfigError('retained File review can only attach to its prepared Run')
        write(state_dir / FILENAME, value)
        state['retained_review'] = self.summary
        write(state_dir / 'run.json', state)
        self.path, self.state_dir = state_dir / FILENAME, state_dir
        self.validate()


def preflight(profile: dict, review: FileRetainedReview, receipt: Path, executable: Path) -> dict:
    result = common_preflight(profile, review, receipt, executable)
    if result['status'] == 'DISCOVERY_PASSED':
        check_anonymous_route(result, RemoteFileClient(profile), review)
    return result


def check_anonymous_route(result: dict, client: RemoteFileClient, review: FileRetainedReview) -> None:
    review.validate()
    try:
        observation, record = client.probe_anonymous_route()
        review.validate()
        result.setdefault('requests', []).append(record)
        result['anonymous_file_route'] = observation
    except FileTransportError as error:
        result['status'] = 'BLOCKED'
        result.setdefault('requests', []).extend(error.request_records)
        result['anonymous_file_route'] = {
            'status': 'BLOCKED', 'error_type': type(error).__name__,
            'reason': 'Anonymous token validation is unavailable; no Cookie or route fallback is allowed.',
            'file_access': 'NOT_TESTED'}


_OPERATION_LIMITS = {
    'group_create': 1,
    'session_create': 1,
    'file_prepare': 1,
    'file_upload': 1,
    'file_complete': 1,
    'file_list': 1,
    'file_metadata': 1,
    'file_download': 1,
    'file_share': 1,
    'shared_file_download': 1,
}


class RemoteRetainedFileAdapter:
    """Finite File-only product surface using the existing remote transports."""
    termination_mode = 'REMOTE_UNSUPPORTED'

    def __init__(self, profile: dict, review: FileRetainedReview, *, observer_client, binding):
        review.validate()
        self.profile, self.review = copy.deepcopy(profile), review
        self._binding = binding
        self._adapter = RemotePilotAdapter(profile, observer_client=observer_client,
                                           physical_binding_probe=binding)
        self._read = ProtectedReadClient(profile, review)
        self._remaining = dict(_OPERATION_LIMITS)
        self._preflight_done = False
        self._preflight_attempted = False
        self._uploaded: set[tuple[str, str]] = set()

    def _validate(self) -> None:
        self.review.validate()
        self._binding.validate()

    def preflight(self):
        self._validate()
        if self._preflight_attempted:
            raise ConfigError('retained File adapter preflight already attempted')
        self._preflight_attempted = True
        from .preflight import check
        result = check(self.profile, self._read)
        if result['status'] == 'DISCOVERY_PASSED':
            check_anonymous_route(result, self._adapter.files, self.review)
        self._preflight_done = result['status'] == 'DISCOVERY_PASSED'
        return result

    def semantic_observer(self):
        self._validate()
        return self._adapter.semantic_observer()

    def execution_observer(self):
        # File completion side effects are declared, not claimed observed.
        return None

    def _reserve(self, name: str) -> None:
        self._validate()
        if not self._preflight_done or self._remaining[name] <= 0:
            raise ConfigError('retained File operation budget or preflight does not admit this call')
        self._remaining[name] -= 1

    def _expected_payload(self) -> bytes:
        return f'bcs-e2e-pre03:{self.review.run_id}:small-file\n'.encode()

    def _validate_upload_target(self, session_id: str, file_id: str) -> None:
        prepared = self._adapter.files.prepared.get((session_id, file_id))
        if prepared is None:
            raise ConfigError('retained File upload lacks the exact prepared binding')
        target = prepared.get('target')
        if (not isinstance(target, dict) or target.get('file_id') != file_id
                or target.get('mode') != 'single' or target.get('method') != 'PUT'
                or not isinstance(target.get('upload_url'), str)):
            raise ConfigError('retained File upload requires one returned PUT storage target')
        url = target['upload_url']
        try:
            if self._adapter.files._same_origin(url):
                raise ConfigError('retained File upload target cannot use the product proxy')
            self._adapter.files._url(url, storage=True)
        except (ValueError, TypeError):
            raise ConfigError('retained File upload target is invalid') from None
        parts = urlsplit(url)
        try:
            decoded_path = unquote(parts.path, errors='strict')
        except UnicodeError:
            raise ConfigError('retained File upload target path is not valid UTF-8') from None
        prefix = self.review.validate()['file_effect_review']['storage_key_prefix']
        filename = f'e2e-{self.review.run_id}.txt'
        expected_prefix = '/' + prefix + '/' + session_id + '/'
        expected_suffix = '/' + filename
        if (not decoded_path.startswith(expected_prefix) or not decoded_path.endswith(expected_suffix)):
            raise ConfigError('retained File upload target is outside the reviewed owned-session key prefix')
        transfer_id = decoded_path[len(expected_prefix):-len(expected_suffix)]
        if re.fullmatch('[0-9a-f]{32}', transfer_id) is None:
            raise ConfigError('retained File upload target lacks the reviewed transfer ID shape')
        expected_path = expected_prefix + transfer_id + expected_suffix
        if decoded_path != expected_path:
            raise ConfigError('retained File upload target path differs from the reviewed source contract')

    def __getattr__(self, name):
        if name not in self._remaining:
            raise AttributeError(name)

        def call(*args, **kwargs):
            self._reserve(name)
            if kwargs:
                raise ConfigError('retained File operations require the shared positional contract')
            if name == 'group_create':
                actors = self.profile['actors']
                expected = {'group_kind': 'normal', 'name': f'E2E {self.review.run_id} file',
                    'context': 'BCS E2E pilot RETAIN-FILE-01',
                    'driver_bot_uuid': actors['driver']['bot_id'],
                    'participants': [{'actor_id': actors['driver']['bot_id'], 'role': 'driver'},
                                     {'actor_id': actors['member']['bot_id'], 'role': 'consultant'}],
                    'collaboration': {'strategy': 'chat'}}
                if args != (expected,):
                    raise ConfigError('retained File Group payload differs from the shared reviewed steps')
                return self._adapter.write.call('group.create', body=expected, expected=(200, 201))
            if name == 'session_create':
                expected = {'title': f'E2E {self.review.run_id} file session',
                            'acting_bot_id': 'human_' + self.profile['human']['user_id']}
                if len(args) != 2 or args[1] != expected:
                    raise ConfigError('retained File Session payload differs from the shared reviewed steps')
            elif name == 'file_prepare':
                payload = self._expected_payload()
                expected = {'file_name': f'e2e-{self.review.run_id}.txt', 'size': len(payload),
                            'mime_type': 'text/plain'}
                if len(args) != 2 or args[1] != expected or not 0 < len(payload) <= MAX_UPLOAD_BYTES:
                    raise ConfigError('retained File prepare payload differs or exceeds its reviewed maximum')
            elif name == 'file_upload':
                if (len(args) != 3 or type(args[2]) is not bytes or args[2] != self._expected_payload()
                        or not 0 < len(args[2]) <= MAX_UPLOAD_BYTES):
                    raise ConfigError('retained File upload bytes differ from the deterministic shared payload')
                self._validate_upload_target(args[0], args[1])
                result = self._adapter.file_upload(*args)
                self._uploaded.add((args[0], args[1]))
                return result
            elif name == 'file_complete':
                if len(args) != 2 or (args[0], args[1]) not in self._uploaded:
                    raise ConfigError('retained File complete requires the one confirmed reviewed upload')
                self._validate_upload_target(args[0], args[1])
            return getattr(self._adapter, name)(*args)
        return call


def adapter_factory(profile: dict, state_dir: Path, run_id: str, case_id: str, *, executable: Path):
    review = FileRetainedReview(profile, run_id, case_id, state_dir / FILENAME)
    review.state_dir = state_dir
    review.validate()
    from .remote_observer import remote_dependencies
    client, binding = remote_dependencies(profile, state_dir, run_id, executable=executable, allow_read=True)
    return RemoteRetainedFileAdapter(profile, review, observer_client=RetainedMcpClient(client, review),
                                     binding=binding)
