"""Fresh read-only authorization for immutable artifacts of a retained Run.

Old expiry is historical, never renewed. A new observation cannot execute a
Story, adopt unknown resources, mutate the original result or release the pool.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re
import time
import stat

from .inventory import frozen_manifest
from .ledger import Ledger
from .observation_budget import BUDGETED_WAIT_CONTRACT
from .profile import ConfigError, fingerprint, identifier, object_keys
from .retained_remote import require_remote_profile, validate_observation_permission
from .run_state import read, require_profile_binding

CONTRACT = 'retained-execution-recheck-v1'
PLAN_CONTRACT = 'retained-source-read-plan-v2'
CASES = {'RETAIN-GROUP-01': 1, 'RETAIN-SESSION-01': 2}
RESOURCE_FIELDS = ('handle', 'case_id', 'attempt', 'kind', 'parent', 'resource_id', 'origin', 'state', 'request_id')


def source_snapshot(profile: dict, state_dir: Path, run_id: str, case_id: str) -> dict:
    require_remote_profile(profile)
    identifier(run_id, 'recheck Run ID')
    if case_id not in CASES or not state_dir.is_dir() or state_dir.is_symlink():
        raise ConfigError('recheck requires an existing retained Run and reviewed Case')
    state = read(state_dir / 'run.json')
    digest = require_profile_binding(state, profile)
    if (state.get('run_id') != run_id or state.get('status') != 'QUARANTINED'
            or state.get('active_case') != case_id or state.get('attempted_cases') != [case_id]
            or state.get('adapter_kind') not in {'gateway', 'bcs-direct'}
            or state.get('last_report') != f'report-{case_id}.json'):
        raise ConfigError('recheck requires one completed, quarantined retained attempt')
    manifest = frozen_manifest(state_dir, state)
    definition = next((c for c in manifest['cases'] if c['id'] == case_id), None)
    if manifest['suite'] != 'preprod-retained-v1' or definition is None or definition.get('finalization') != 'retain' or definition.get('version') != 1:
        raise ConfigError('recheck cannot substitute another suite or Case contract')
    report = read(state_dir / f'report-{case_id}.json')
    retention = read(state_dir / 'retention.json')
    for value in (report, retention):
        if (value.get('run_id') != run_id or value.get('case_id') != case_id
                or value.get('profile_digest') != digest):
            raise ConfigError('recheck artifacts differ from their Run/Profile/Case')
    if (report.get('manifest_digest') != state['manifest_digest']
            or report.get('executor_digest') != state['executor_digest']
            or report.get('case_version') != definition['version']
            or report.get('status') not in {'BLOCKED', 'FAIL', 'CANCELLED'}
            or report.get('pool_disposition') != 'QUARANTINED'
            or retention.get('inventory_status') != 'COMPLETE' or retention.get('automatic_expiry') is not False):
        raise ConfigError('recheck requires retained original evidence, not an unresolved inventory')
    ledger_path = state_dir / 'run.sqlite'
    if ledger_path.is_symlink() or Path(state.get('ledger_path', '')).resolve() != ledger_path.resolve():
        raise ConfigError('recheck journal must be the existing Run-local ledger')
    with Ledger(ledger_path, run_id, digest, read_only=True) as ledger:
        resources = ledger.resource_snapshot()
    groups = [r for r in resources if r['kind'] == 'group']
    sessions = [r for r in resources if r['kind'] == 'session']
    if (len(groups) != 1 or len(sessions) != CASES[case_id] or len(resources) != len(sessions) + 1
            or groups[0]['parent'] is not None
            or any(s['parent'] != groups[0]['handle'] for s in sessions)
            or any(r['origin'] != 'created' or r['state'] != 'live' or not r['resource_id']
                   or r['case_id'] != case_id or r['attempt'] != 1 for r in resources)):
        raise ConfigError('recheck resources must be exact known-owned live parents and Sessions')
    projected = [{k:r[k] for k in RESOURCE_FIELDS} for r in resources]
    if retention.get('resources') != projected:
        raise ConfigError('retained inventory differs from the durable resource journal')
    original_review = read(state_dir / 'retained-review.json')
    original_binding = read(state_dir / 'observer-binding.json')
    if (fingerprint(original_review) != state.get('retained_review', {}).get('receipt_digest')
            or fingerprint(original_binding) != state.get('observer_binding', {}).get('receipt_digest')):
        raise ConfigError('historical frozen reviews differ from the original Run')
    # Fingerprints pin semantic JSON, not sqlite file bytes that can change due
    # to storage layout. This snapshot does not claim malicious-tamper resistance.
    return {'run': fingerprint(state), 'manifest': fingerprint(manifest), 'report': fingerprint(report),
            'retention': fingerprint(retention), 'resources': fingerprint(resources),
            'original_review': fingerprint(original_review), 'original_binding': fingerprint(original_binding)}


class RetainedRecheckReview:
    def __init__(self, profile: dict, state_dir: Path, run_id: str, case_id: str, path: Path):
        self.profile = copy.deepcopy(profile)
        self.state_dir, self.run_id, self.case_id, self.path = state_dir, run_id, case_id, path
        self._authority_check = lambda: None
        self._identities = self._file_identities()
        value = self._validated()
        self.digest = fingerprint(value)
        self.observation_id = value['observation_id']

    def _file_identities(self) -> dict:
        paths = [self.state_dir, self.state_dir / 'run.sqlite', self.state_dir / 'run.json',
                 self.state_dir / 'manifest.json', self.state_dir / 'retention.json',
                 self.state_dir / f'report-{self.case_id}.json', self.state_dir / 'retained-review.json',
                 self.state_dir / 'observer-binding.json']
        result = {}
        for path in paths:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or path == self.state_dir and stat.S_ISDIR(info.st_mode)):
                raise ConfigError('recheck source must remain regular files in the original directory')
            result[str(path)] = (info.st_dev, info.st_ino)
        return result

    def bind_authority(self, check) -> None:
        self._authority_check = check

    def _validated(self) -> dict:
        value = read(self.path)
        optional = set(value) & {'downstream_observation', 'scope_observation', 'delivery_pagination'}
        planned = value.get('contract') == PLAN_CONTRACT
        fields = {'mapping_confirmation_digest'} if planned else {
            'reviewed_at', 'expires_at', 'reviewer', 'observer_binding_digest'}
        object_keys(value, {'schema_version', 'contract', 'profile_digest', 'run_id', 'case_id',
                           'observation_id', 'source', 'execution_observation',
                           'execution_observation_budget'} | fields | optional, 'recheck review or read plan')
        if (value['contract'] not in (CONTRACT, PLAN_CONTRACT)
                or value['run_id'] != self.run_id or value['case_id'] != self.case_id
                or value['profile_digest'] != fingerprint(self.profile)
                or not planned and value['reviewer'] != self.profile['human']['user_id']
                or value['execution_observation'] != BUDGETED_WAIT_CONTRACT):
            raise ConfigError('recheck must bind this Run/Case/Profile and wait-v2 contract')
        if (not isinstance(value['observation_id'], str)
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', value['observation_id']) is None):
            raise ConfigError('recheck observation ID must be one safe directory component')
        binding_key = 'mapping_confirmation_digest' if planned else 'observer_binding_digest'
        if not isinstance(value[binding_key], str) or re.fullmatch('[0-9a-f]{64}', value[binding_key]) is None:
            raise ConfigError('recheck requires the exact environment binding digest')
        if not planned:
            start, end = value['reviewed_at'], value['expires_at']
            if (type(start) is not int or type(end) is not int or not 0 < end-start <= 3600
                    or not start <= int(time.time()) < end):
                raise ConfigError('recheck review is expired, not yet valid or exceeds one hour')
        if 'scope_observation' in optional:
            from .scope_observation import CONTRACTS as SCOPE_CONTRACTS
            if not isinstance(value['scope_observation'], str) or value['scope_observation'] not in SCOPE_CONTRACTS:
                raise ConfigError('recheck scope observation requires the fixed Group/Session facts contract')
        validate_observation_permission(value, self.profile)
        if value['source'] != source_snapshot(self.profile, self.state_dir, self.run_id, self.case_id):
            raise ConfigError('recheck source artifacts differ from the explicitly reviewed snapshot')
        return value

    def validate(self) -> dict:
        self._authority_check()
        if self._file_identities() != self._identities:
            raise ConfigError('recheck source file identity changed')
        value = self._validated()
        if fingerprint(value) != self.digest:
            raise ConfigError('recheck review changed; authority is never renewed in flight')
        return value


def recheck_plan(profile: dict, state_dir: Path, run_id: str, case_id: str, *, mapping_confirmation: Path | None = None) -> dict:
    source = source_snapshot(profile, state_dir, run_id, case_id)
    if mapping_confirmation is not None:
        from .mapping_confirmation import EnvironmentMapping
        mapping = EnvironmentMapping(profile, mapping_confirmation)
        return {'schema_version': 1, 'status': 'PLANNED', 'ready_to_execute': False,
                'run_id': run_id, 'case_id': case_id, 'environment_mapping': mapping.summary,
                'original_result_unchanged': True, 'pool_release_authorized': False,
                'read_plan': {'schema_version': 1, 'contract': PLAN_CONTRACT,
                    'profile_digest': fingerprint(profile), 'run_id': run_id, 'case_id': case_id,
                    'observation_id': 'choose-unique-observation-id', 'source': source,
                    'mapping_confirmation_digest': mapping.digest,
                    'execution_observation': BUDGETED_WAIT_CONTRACT,
                    'execution_observation_budget': {'timeout_seconds': 120, 'max_rounds': 6, 'poll_interval_seconds': 10}},
                'authorization_required': ['--allow-observer-read', '--confirm-run-id'],
                'note': 'Environment facts grant no reads. Explicit command flags authorize one bounded owned-source observation; no writes or release.'}
    return {'schema_version': 1, 'status': 'PLANNED', 'ready_to_execute': False,
            'scope': 'RETAINED_EXECUTION_READ_ONLY', 'run_id': run_id, 'case_id': case_id,
            'original_result_unchanged': True, 'pool_release_authorized': False,
            'review_template': {'schema_version': 1, 'contract': CONTRACT,
                'profile_digest': fingerprint(profile), 'run_id': run_id, 'case_id': case_id,
                'reviewer': profile['human']['user_id'], 'reviewed_at': 0, 'expires_at': 0,
                'observation_id': 'choose-unique-observation-id', 'source': source,
                'observer_binding_digest': 'replace-with-reviewed-binding-sha256',
                'execution_observation': BUDGETED_WAIT_CONTRACT,
                'execution_observation_budget': {'timeout_seconds': 120, 'max_rounds': 6, 'poll_interval_seconds': 10}},
            'note': 'Offline proposal only. Zero timestamps grant no authority; obtain fresh scoped read approval.'}
