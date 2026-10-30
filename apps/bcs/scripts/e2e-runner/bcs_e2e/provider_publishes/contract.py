"""Frozen S04 source and binary admission; no automatic map refresh."""
import hashlib
import json
import os
from pathlib import Path

from ..owned_bcs import file_sha256
from ..profile import unique_keys
from ..run_state import RunStateError

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_FILE = ROOT / 'provider-publishes-migration-contract.json'
PARENT = 'story_provider_operator_publishes_agent'
CASE_ID = 'DUAL-PROVIDER-PUBLISHES-01'
SUITE = 'dual-provider-publishes-v1'
CONTRACT_SHA256 = 'b045a02b8149cf0389c250736fb103a1a5b6592318a83acc4843b28410636326'
SERVER_SHA256 = '899092e39ca461f04a59dce989d1a13bc85e61791099c532b98d7b3882cb745c'
CLI_SHA256 = '5a0f3fc595f97d71587af13b2ab5d0267c74c99f7540333b3249554276e97f77'


def source_binding():
    try:
        raw_contract = CONTRACT_FILE.read_text()
        if hashlib.sha256(raw_contract.encode()).hexdigest() != CONTRACT_SHA256:
            raise ValueError
        value = json.loads(raw_contract, object_pairs_hook=unique_keys)
        source = ROOT.parent / 'e2e-test/stories.sh'
        raw = source.read_bytes()
        lines = raw.decode().splitlines()
        rows = value['requirements']
        if value['parent'] != PARENT or value['dual_case_id'] != CASE_ID:
            raise ValueError
        if not value['sources'] == {'stories.sh': hashlib.sha256(raw).hexdigest()}:
            raise ValueError
        if not len(rows) == value['story_checks'] == value['parsed_upstream_steps'] == 98:
            raise ValueError
        if not len({r['id'] for r in rows}) == 98:
            raise ValueError
        if not sum(r['kind'] == 'status' for r in rows) == 42:
            raise ValueError
        if not len(value['operations']) == 42:
            raise ValueError
        if not value['runtime_binary']['sha256'] == SERVER_SHA256:
            raise ValueError
        if not value['cli_runtime_binary']['sha256'] == CLI_SHA256:
            raise ValueError
        for row in rows + value['admission_preconditions']:
            if not row['id'].startswith('PROVIDER-PUBLISHES.'):
                raise ValueError
            if not row['quote'] == '\n'.join(s.strip() for s in lines[row['line'] - 1:row['end_line']]):
                raise ValueError
        return value
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        raise RunStateError('Provider publishes frozen source/contract drift') from None


def validate_binary(path, digest, expected, label):
    """Reject missing, symlink, changed or unfrozen pairs before any state write."""
    if path is None or digest != expected:
        raise RunStateError(label + ' frozen binary pair unavailable')
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
        raise RunStateError(label + ' binary must be an absolute regular executable')
    if file_sha256(path) != expected:
        raise RunStateError(label + ' binary differs from sheltered SHA256')
    return path


REQUIREMENTS = tuple(row['id'] for row in source_binding()['requirements'])
