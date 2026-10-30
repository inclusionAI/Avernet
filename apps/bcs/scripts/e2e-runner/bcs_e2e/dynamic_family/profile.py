"""Strict profile admission for the dynamic fixed-loop dual families."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..profile import (
    ConfigError,
    identifier,
    object_keys,
    origin,
    unique_keys,
)
from .contract import dynamic_family_contract

_ENV_REFERENCE = re.compile(r'[A-Z][A-Z0-9_]{2,100}')
DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
_REQUIRED_HEADER_ENV = {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}


def _env_reference(value: object, where: str) -> str:
    if not isinstance(value, str) or _ENV_REFERENCE.fullmatch(value) is None:
        raise ConfigError(where + ': expected a fixed environment reference name')
    return value


def _station_routes(value: object, family: str) -> list[str]:
    expected = {
        'fixed_loop': ['legacy-groups', 'legacy-state-machine-runs', 'legacy-sessions'],
        'nested_fixed_loop': ['legacy-groups', 'legacy-state-machine-runs', 'legacy-graph-block'],
        'result_queue': ['legacy-groups', 'legacy-state-machine-runs', 'unregistered-admin-policy'],
        'draft_group': ['legacy-groups', 'legacy-bots-mine', 'legacy-draft-face'],
    }[family]
    if (not isinstance(value, list) or value != expected):
        raise ConfigError('dynamic family station_routes differ from the reviewed station truth')
    return list(value)


def validate_dynamic_family_profile(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError('dynamic family profile must be an object')
    profile = object_keys(
        raw, {'schema_version', 'contract', 'family', 'environment', 'entrypoint',
              'human', 'auth', 'cli', 'policy', 'station_routes'},
        'dynamic family profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ConfigError('dynamic family profile: unsupported schema version')
    family = profile['family']
    if family not in {'fixed_loop', 'nested_fixed_loop', 'result_queue', 'draft_group'}:
        raise ConfigError('dynamic family profile: unsupported family')
    contract = dynamic_family_contract(family)
    if profile['contract'] != contract.profile_contract:
        raise ConfigError('dynamic family profile: contract does not match family')

    environment = object_keys(profile['environment'], {'tier', 'id', 'expected_bcs_env'},
                              'dynamic family environment')
    tier = environment['tier']
    if tier not in ('local', 'preprod'):
        raise ConfigError('dynamic family environment.tier: only local and preprod are admitted')
    identifier(environment['id'], 'dynamic family environment.id')
    expected_env = 'local' if tier == 'local' else 'pre'
    if environment['expected_bcs_env'] != expected_env:
        raise ConfigError('dynamic family environment expected_bcs_env is inconsistent')

    entrypoint = object_keys(
        profile['entrypoint'], {'kind'} if tier == 'local'
        else {'kind', 'origin', 'api_face', 'manifest_face'},
        'dynamic family entrypoint')
    if tier == 'local':
        if entrypoint['kind'] != 'owned-local':
            raise ConfigError('dynamic family local entrypoint must delegate to the owned-local executor')
    else:
        if entrypoint['kind'] != 'bcs-direct':
            raise ConfigError('dynamic family preprod entrypoint must be station-direct')
        if entrypoint['api_face'] != 'legacy' or entrypoint['manifest_face'] != 'legacy':
            raise ConfigError('dynamic family preprod entrypoint must bind the station legacy face')
        origin(entrypoint['origin'], 'preprod')

    human = object_keys(profile['human'], {'user_id'}, 'dynamic family human')
    identifier(human['user_id'], 'dynamic family human.user_id')

    auth = object_keys(profile['auth'], {'kind'} if tier == 'local'
                       else {'kind', 'header_env'}, 'dynamic family auth')
    if tier == 'local':
        if auth['kind'] != 'owned-local':
            raise ConfigError('dynamic family local auth must be owned-local')
    else:
        if auth['kind'] != 'preprod-human-cookie':
            raise ConfigError('dynamic family preprod auth must bind the Human Cookie')
        header_env = object_keys(auth['header_env'], {'Authorization', 'Cookie'},
                                 'dynamic family auth.header_env')
        if {name: _env_reference(value, 'auth.header_env.' + name)
                for name, value in header_env.items()} != _REQUIRED_HEADER_ENV:
            raise ConfigError(
                'dynamic family preprod auth must reference '
                'BCS_E2E_DRIVER_TOKEN and BCS_E2E_HUMAN_COOKIE by name only')

    cli = object_keys(profile['cli'], {'transport', 'command_family', 'api_face'},
                      'dynamic family cli')
    command_families = {
        'fixed_loop': 'fixed-loop', 'nested_fixed_loop': 'nested-fixed-loop',
        'result_queue': 'result-queue', 'draft_group': 'draft-group'}
    if cli['command_family'] != command_families[family]:
        raise ConfigError('dynamic family profile command family does not match family')
    if tier == 'local':
        if cli['transport'] != 'owned-local-story' or cli['api_face'] != 'owned-local':
            raise ConfigError('dynamic family local CLI face must delegate to the local story')
    elif cli['transport'] != 'station-gap-receipt' or cli['api_face'] != 'legacy':
        raise ConfigError('dynamic family preprod offline profile must declare the station gap receipt face')

    policy = object_keys(profile['policy'], {'fixture_pool', 'exclusive_use_confirmed',
                                             'timeout_seconds', 'max_response_bytes',
                                             'failure_retention_hours'},
                         'dynamic family policy')
    identifier(policy['fixture_pool'], 'dynamic family policy.fixture_pool')
    if policy['exclusive_use_confirmed'] is not True:
        raise ConfigError('dynamic family policy requires exclusive story use')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= 30:
        raise ConfigError('dynamic family policy.timeout_seconds is outside 1..30')
    if type(policy['max_response_bytes']) is not int or not 1 <= policy['max_response_bytes'] <= 65536:
        raise ConfigError('dynamic family policy.max_response_bytes is outside 1..65536')
    if type(policy['failure_retention_hours']) is not int or not 1 <= policy['failure_retention_hours'] <= 168:
        raise ConfigError('dynamic family policy.failure_retention_hours is outside 1..168')

    profile['station_routes'] = _station_routes(profile['station_routes'], family)
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)


def load_dynamic_family_profile(path: Path) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ConfigError('dynamic family profile must be a regular file')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError('dynamic family profile is unreadable JSON') from None
    return validate_dynamic_family_profile(raw)
