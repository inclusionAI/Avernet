"""Reviewed gateway attachment for one supervised, default-retain control slice.

The receipt records operator attestation, not server authorization or a verified
execution terminal. It never relaxes the original PRE bootstrap gate. Only this
composition exposes finite, non-delete operations under the retained manifest.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re
import time

from .adapters import RemotePilotAdapter
from .alisddbquery import AlisdDbQueryObserver
from .evidence import EvidenceError
from .delivery_observation import CONTRACTS as DELIVERY_CONTRACTS, DeliveryObserver
from .downstream_observation import CONTRACT as DOWNSTREAM_CONTRACT
from .observation_budget import BUDGETED_WAIT_CONTRACT, reviewed_budget
from .errors import primary_error
from .retention_quota import validate_quota
from .identity_binding import HUMAN_VIEW
from .inventory import current_manifest
from .observation_gate import REQUIRED_OBSERVATIONS
from .preflight import check
from .profile import ConfigError, fingerprint, identifier, object_keys, require_resolved, validate
from .remote_check import MetadataOnlyClient
from .remote_observer import remote_check_dependencies, remote_dependencies
from .run_state import read, require_profile_binding, write
from .transport import ObservationError, ReadClient

FILENAME = 'retained-review.json'
SUITE = 'preprod-retained-v1'
CONTRACT = 'supervised-retained-control-v1'


GATEWAY_HUMAN_VIEW_GENERATION = 'gateway-human-view'
BCS_DIRECT_DUAL_HEADER_GENERATION = 'bcs-direct-dual-header'
RETAINED_GENERATIONS = (GATEWAY_HUMAN_VIEW_GENERATION, BCS_DIRECT_DUAL_HEADER_GENERATION)


def execution_generation(profile: dict) -> str:
    """Pure admission-generation judgment for one resolved remote Profile.

    Generation G is the historical composition: gateway entrypoint with the
    reviewed authorized Human-view identity binding and exactly the Cookie
    header reference. Generation D is the separately reviewed dual-header
    composition: bcs-direct entrypoint WITHOUT any explicit identity binding
    (implicit native identity discovery, and the legacy business adapters that
    bind their own reviewed principals; an explicit NATIVE binding stays blocked
    by the pilot blocker) with both the driver Bot Authorization and Human
    Cookie references. No other composition, including singlebox/mock, gateway
    with a Bot token or bcs-direct with only Cookie, is admitted. The judgment
    is pure: no network, no journal rebasing and no new authority.
    """
    kind = profile['entrypoint']['kind']
    headers = set(profile['auth'].get('header_env', {}))
    if (kind == 'gateway' and profile.get('identity_binding') == HUMAN_VIEW
            and headers == {'Cookie'}):
        return GATEWAY_HUMAN_VIEW_GENERATION
    if (kind == 'bcs-direct' and 'identity_binding' not in profile
            and {'Authorization', 'Cookie'} <= headers):
        return BCS_DIRECT_DUAL_HEADER_GENERATION
    raise ConfigError('retained remote execution requires the reviewed gateway Human-view Cookie composition or the reviewed bcs-direct dual-header (driver token plus Human Cookie) composition')


def require_remote_profile(profile: dict) -> None:
    validate(copy.deepcopy(profile))
    require_resolved(profile)
    if (profile['environment']['tier'] != 'preprod'
            or profile['observer']['kind'] != 'alisddbquery'
            or profile['policy']['exclusive_use_confirmed'] is not True
            or execution_generation(profile) not in RETAINED_GENERATIONS):
        raise ConfigError('retained remote execution requires reviewed preprod exclusive fixtures and an admitted identity generation')


def validate_review(value: dict, profile: dict, run_id: str, case_id: str) -> dict:
    require_remote_profile(profile)
    optional = {k for k in ('execution_observation', 'downstream_observation', 'execution_observation_budget',
                'execution_generation') if isinstance(value, dict) and k in value}
    object_keys(value, {'schema_version', 'contract', 'profile_digest', 'run_id', 'case_id',
                        'reviewed_at', 'expires_at', 'supervisor', 'effect_review', 'retention_quota'} | optional, 'retained review')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['contract'] != CONTRACT or value['profile_digest'] != fingerprint(profile)
            or value['run_id'] != run_id or value['case_id'] != case_id
            or case_id not in {c['id'] for c in current_manifest(SUITE)['cases']}):
        raise ConfigError('retained review does not bind this contract, Run, Case and complete profile')
    identifier(run_id, 'retained Run ID')
    if value['supervisor'] != profile['human']['user_id']:
        raise ConfigError('retained review requires the bound Human as the available incident supervisor')
    if 'execution_generation' in value:
        if (not isinstance(value['execution_generation'], str)
                or value['execution_generation'] != execution_generation(profile)):
            raise ConfigError('retained review execution generation must equal the reviewed Profile admission generation')
    start, end = value['reviewed_at'], value['expires_at']
    if (type(start) is not int or type(end) is not int or not 0 < end-start <= 3600
            or not start <= int(time.time()) < end):
        raise ConfigError('retained review is expired, not yet valid or exceeds one hour')
    review = object_keys(value['effect_review'], {'reference', 'sha256', 'deployment_commit',
        'isolated_test_runtime', 'no_operational_channels', 'implicit_bot_execution_allowed'}, 'retained effect review')
    identifier(review['reference'], 'retained review reference')
    for key, size in [('sha256', 64), ('deployment_commit', 40)]:
        if not isinstance(review[key], str) or re.fullmatch('[0-9a-f]{'+str(size)+'}', review[key]) is None:
            raise ConfigError('retained effect review needs explicit source/deployment evidence')
    if any(review[key] is not True for key in ('isolated_test_runtime', 'no_operational_channels',
                                              'implicit_bot_execution_allowed')):
        raise ConfigError('retained effects require isolated test runtimes, no operational channels and explicit bootstrap permission')
    quota = validate_quota(value['retention_quota'])
    if quota['contract'] == 'retained-resource-quota-v2':
        from .retention_quota_v2 import validate_group_limit_authorization
        validate_group_limit_authorization(quota, profile, run_id)
    validate_observation_permission(value, profile)
    return copy.deepcopy(value)


def validate_observation_permission(value: dict, profile: dict) -> None:
    optional = set(value) & {'execution_observation', 'downstream_observation', 'execution_observation_budget'}
    if 'execution_observation' in optional and (not isinstance(value['execution_observation'], str)
                     or value['execution_observation'] not in DELIVERY_CONTRACTS):
        raise ConfigError('execution observation must explicitly select the fixed Session delivery contract')
    if 'execution_observation_budget' in optional and value.get('execution_observation') != BUDGETED_WAIT_CONTRACT:
        raise ConfigError('observation budget is only supported by the explicit wait-v2 review')
    reviewed_budget(value.get('execution_observation'), value.get('execution_observation_budget'))
    if 'delivery_pagination' in value:
        from .delivery_pagination import validate_limits
        if value.get('execution_observation') != BUDGETED_WAIT_CONTRACT:
            raise ConfigError('delivery pagination requires an explicit wait-v2 read plan')
        validate_limits(value['delivery_pagination'])
    if 'downstream_observation' in optional:
        downstream = object_keys(value['downstream_observation'], {'contract', 'bot_bindings', 'run_id_source', 'queue_enabled', 'session_binding'}, 'downstream observation')
        if (downstream['contract'] != DOWNSTREAM_CONTRACT or 'execution_observation' not in optional
                or downstream['run_id_source'] not in ('accepted-alias', 'bcn-canonical')
                or downstream['queue_enabled'] is not True or downstream['session_binding'] != 'bcs-session-id'
                or not isinstance(downstream['bot_bindings'], dict)
                or set(downstream['bot_bindings']) != {a['bot_id'] for a in profile['actors'].values()}):
            raise ConfigError('downstream observation requires explicit BaaS mapping for both exact test Bots and delivery permission')
        for bot in downstream['bot_bindings'].values():
            identifier(bot, 'reviewed BaaS Bot ID')
        if len(set(downstream['bot_bindings'].values())) != 2:
            raise ConfigError('downstream test Bot bindings must be distinct')


def review_summary(value: dict, profile: dict | None = None) -> dict:
    declared = value.get('execution_generation')
    generation = declared if declared is not None else (
        execution_generation(profile) if profile is not None else None)
    return {'contract': value['contract'], 'receipt_digest': fingerprint(value),
        'reviewed_at': value['reviewed_at'], 'expires_at': value['expires_at'],
        'verification_mode': 'OPERATOR_ATTESTED', 'live_deployment_verification': 'NOT_PERFORMED',
        'declared_deployment_commit': value['effect_review']['deployment_commit'],
        'execution_termination': 'NOT_CONFIRMED', 'automatic_delete': False,
        'execution_generation': generation}


class RetainedReview:
    def __init__(self, profile: dict, run_id: str, case_id: str, path: Path):
        self.profile = copy.deepcopy(profile)
        self.run_id, self.case_id, self.path = run_id, case_id, path
        self.summary = review_summary(validate_review(read(path), self.profile, run_id, case_id), self.profile)
        self.state_dir: Path | None = None  # Preflight runs before a Run exists.

    def validate(self) -> dict:
        value = validate_review(read(self.path), self.profile, self.run_id, self.case_id)
        if review_summary(value, self.profile) != self.summary:
            raise ConfigError('retained review changed after admission; no automatic renewal')
        if self.state_dir is not None:
            state = read(self.state_dir / 'run.json')
            require_profile_binding(state, self.profile)
            if state.get('run_id') != self.run_id or state.get('retained_review') != self.summary:
                raise ConfigError('retained review no longer matches the prepared Run')
        return value

    def freeze(self, state_dir: Path) -> None:
        value = self.validate()
        state = read(state_dir / 'run.json')
        require_profile_binding(state, self.profile)
        if state.get('run_id') != self.run_id or state.get('status') != 'PREPARED':
            raise ConfigError('retained review can only attach to its prepared Run')
        write(state_dir / FILENAME, value)
        state['retained_review'] = self.summary
        write(state_dir / 'run.json', state)
        self.path, self.state_dir = state_dir / FILENAME, state_dir
        self.validate()


class ProtectedReadClient(ReadClient):
    def __init__(self, profile: dict, review: RetainedReview):
        review.validate()
        super().__init__(profile)
        self._review = review
        self._remaining = 12

    def read(self, operation: str, bot_id: str = ''):
        self._review.validate()
        if self._remaining <= 0:
            raise ObservationError('retained read-only HTTP budget exhausted')
        self._remaining -= 1
        result = super().read(operation, bot_id)
        self._review.validate()
        if operation == 'bot.read':
            # Public-v1 Bot response exposes visibility at the top level.
            # Nested capability metadata is not an authorization substitute.
            if result.data.get('visibility') != 'protected':
                raise ObservationError('retained bootstrap requires owned protected test Bots; no relation repair')
        return result


class RetainedMcpClient:
    """Retain both mapping and effect-review checks; do not add SQL capabilities."""
    def __init__(self, client, review: RetainedReview):
        self._client, self._review = client, review
        self._remaining = 32

    def _call(self, method, *args):
        self._review.validate()
        if self._remaining <= 0:
            raise EvidenceError('retained MCP phase budget exhausted')
        self._remaining -= 1
        result = getattr(self._client, method)(*args)
        self._review.validate()
        return result

    def db_list(self): return self._call('db_list')
    def table_list(self, database): return self._call('table_list', database)
    def table_describe(self, database, table): return self._call('table_describe', database, table)
    def db_query(self, plan): return self._call('db_query', plan)

    def delivery_query(self, plan, *, scope, timeout_seconds: float, deadline_monotonic: float | None = None):
        self._review.validate()
        if self._remaining <= 0:
            raise EvidenceError('retained MCP phase budget exhausted')
        self._remaining -= 1
        return self._client.delivery_query(plan, review=self._review, scope=scope, timeout_seconds=timeout_seconds,
                                           deadline_monotonic=deadline_monotonic)

    def delivery_scan(self, plan, *, scope, deadline_monotonic: float, progress: dict):
        def reserve():
            self._review.validate()
            if self._remaining <= 0:
                raise EvidenceError('retained MCP phase budget exhausted')
            self._remaining -= 1
        return self._client.delivery_scan(plan, review=self._review, scope=scope,
            deadline_monotonic=deadline_monotonic, before_page=reserve, progress=progress)

    def scope_query(self, plan, *, group: bool, scope, deadline_monotonic: float):
        self._review.validate()
        if self._remaining <= 0:
            raise EvidenceError('retained MCP phase budget exhausted')
        self._remaining -= 1
        return self._client.scope_query(plan, group=group, review=self._review, scope=scope,
                                        deadline_monotonic=deadline_monotonic)

    def producer_query(self, plan, *, scope, deadline_monotonic: float):
        self._review.validate()
        if self._remaining <= 0:
            raise EvidenceError('retained MCP phase budget exhausted')
        self._remaining -= 1
        return self._client.producer_query(plan, review=self._review, scope=scope,
                                           deadline_monotonic=deadline_monotonic)

    def downstream_observe(self, plan, *, scope, deadline_monotonic: float):
        self._review.validate()
        if self._remaining < 2:
            raise EvidenceError('retained MCP budget has no room for correlated downstream reads')
        self._remaining -= 2  # Reserve both queries even if the first fails.
        return self._client.downstream_observe(plan, review=self._review, scope=scope, deadline_monotonic=deadline_monotonic)

    def execution_observer(self, profile):
        contract = self._review.validate().get('execution_observation')
        if contract not in DELIVERY_CONTRACTS:
            return None
        return DeliveryObserver(client=self, database=profile['observer']['database'],
            environment=profile['environment']['expected_bcs_env'],
            allowed_bots=frozenset(a['bot_id'] for a in profile['actors'].values()), contract=contract,
            downstream_config=self._review.validate().get('downstream_observation'),
            observation_budget=self._review.validate().get('execution_observation_budget'),
            scope_contract=self._review.validate().get('scope_observation'),
            pagination=self._review.validate().get('delivery_pagination'))


def preflight(profile: dict, review: RetainedReview, receipt: Path, executable: Path) -> dict:
    client, binding = remote_check_dependencies(profile, receipt, executable=executable, allow_read=True)
    result = check(profile, ProtectedReadClient(profile, review))
    result['retained_review'] = review.summary
    result['observer_binding'] = binding.summary
    result['observer_preflight'] = {'status': 'NOT_RUN', 'business_queries': 0}
    if result['status'] != 'DISCOVERY_PASSED':
        return result
    try:
        observer = AlisdDbQueryObserver(client=MetadataOnlyClient(RetainedMcpClient(client, review)),
            database=profile['observer']['database'], environment=profile['environment']['expected_bcs_env'],
            environment_binding_confirmed=True, physical_binding_probe=binding)
        required = REQUIRED_OBSERVATIONS[review.case_id]
        observer.admit(required)
        delivery = RetainedMcpClient(client, review).execution_observer(profile)
        if delivery is not None:
            delivery.admit()  # Describe only, never row queries before Run ownership.
            result['observer_preflight']['execution_schema'] = 'ADMITTED'
        binding.validate(); review.validate()
        result['observer_preflight'].update(status='METADATA_ADMITTED', assertion_ids=list(required))
    except Exception as error:
        cause = primary_error(error)
        if isinstance(cause, KeyboardInterrupt):
            raise cause from None
        result['status'] = 'BLOCKED'
        result['observer_preflight'].update(status='BLOCKED', error_type=type(cause).__name__)

    return result


_OPERATION_LIMITS = {
    'group_create': 1, 'group_read': 2, 'group_update': 1,
    'session_create': 1, 'session_read': 2, 'session_update': 1,
    'session_messages': 1, 'session_token': 1, 'session_ws_connect': 1,
}


class RemoteRetainedAdapter:
    """Finite retained operation surface; deletion/file/stop methods are absent."""
    termination_mode = 'REMOTE_UNSUPPORTED'

    def __init__(self, profile: dict, review: RetainedReview, *, observer_client, binding):
        review.validate()
        self.profile, self.review = copy.deepcopy(profile), review
        self._binding = binding
        self._adapter = RemotePilotAdapter(profile, observer_client=observer_client, physical_binding_probe=binding)
        self._read = ProtectedReadClient(profile, review)
        self._remaining = dict(_OPERATION_LIMITS)
        if review.case_id == 'RETAIN-GROUP-01':
            self._remaining = {k: v for k, v in self._remaining.items() if k.startswith('group_')}
        self._preflight_done = False
        self._preflight_attempted = False

    def _validate(self):
        self.review.validate()
        self._binding.validate()

    def preflight(self):
        self._validate()
        if self._preflight_attempted:
            raise ConfigError('retained adapter preflight already attempted')
        self._preflight_attempted = True
        result = check(self.profile, self._read)
        self._preflight_done = result['status'] == 'DISCOVERY_PASSED'
        return result

    def semantic_observer(self):
        self._validate()
        return self._adapter.semantic_observer()

    def execution_observer(self):
        self._validate()
        return self._adapter.observer_client.execution_observer(self.profile)

    def session_ws_close(self, session_id: str):
        # Base adapter closes only a handle it owns; expiry cannot prevent close.
        return self._adapter.session_ws_close(session_id)

    def __getattr__(self, name):
        if name not in self._remaining:
            raise AttributeError(name)

        def call(*args, **kwargs):
            self._validate()
            if not self._preflight_done or self._remaining[name] <= 0:
                raise ConfigError('retained operation budget or preflight does not admit this call')
            self._remaining[name] -= 1  # Failed/ambiguous requests still consume the budget.
            if name == 'group_create':
                body = args[0]
                actors = self.profile['actors']
                suffix = ' session' if self.review.case_id == 'RETAIN-SESSION-01' else ''
                expected = {'group_kind': 'normal', 'name': f'E2E {self.review.run_id}{suffix}',
                            'context': f'BCS E2E pilot {self.review.case_id}',
                            'driver_bot_uuid': actors['driver']['bot_id'],
                            'collaboration': {'strategy': 'chat'},
                            'participants': [{'actor_id': actors['driver']['bot_id'], 'role': 'driver'},
                                             {'actor_id': actors['member']['bot_id'], 'role': 'consultant'}]}
                if body != expected:
                    raise ConfigError('retained bootstrap payload differs from the reviewed test actor scope')
                # This separate reviewed path deliberately does not invoke the
                # legacy PRE group_create, whose unconditional hold is unchanged.
                return self._adapter.write.call('group.create', body=body, expected=(200, 201))
            if name == 'session_create' and args[1] != {
                    'title': f'E2E {self.review.run_id} session',
                    'acting_bot_id': 'human_' + self.profile['human']['user_id'],
                    'input': {'query': 'Validate the Session connection lifecycle'}}:
                raise ConfigError('retained Session payload differs from the reviewed control story')
            return getattr(self._adapter, name)(*args, **kwargs)
        return call


def adapter_factory(profile: dict, state_dir: Path, run_id: str, case_id: str, *, executable: Path):
    review = RetainedReview(profile, run_id, case_id, state_dir / FILENAME)
    review.state_dir = state_dir
    review.validate()
    client, binding = remote_dependencies(profile, state_dir, run_id, executable=executable, allow_read=True)
    return RemoteRetainedAdapter(profile, review, observer_client=RetainedMcpClient(client, review), binding=binding)
