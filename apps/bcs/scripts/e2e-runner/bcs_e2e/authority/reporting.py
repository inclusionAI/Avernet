"""Canonical reports for the independent Bot-authority owned-local receipt.

The dual preprod leg publishes its machine-readable PASS or fail-closed
BLOCKED evidence leaf under its own root outside this local receipt tree;
this module stays the owned-local summary projection. The summary never
carries a credential, never grants remote release credit, and always keeps
the preprod leg outside this local receipt's boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from ..inventory import executor_digest
from ..profile import fingerprint
from ..run_state import RunStateError, read
from .contract import (CASE_ID, DISPOSITION, PARENT, REQUIREMENTS, STATUS, STORY_CHECKS,
                       SUITE)

BINDING_NAME = 'authority-binding.json'
FLOW_NAME = 'authority-flow-report.json'
CASE_NAME = 'authority.json'
COMPLETION_NAME = 'authority-dual-completion.json'
COMPLETION = 'owned-local-authority-dual-completion-v1'
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')


def _require(value: object, message: str) -> None:
    if not value:
        raise RunStateError(message)


def _sha(path: Path) -> str:
    _require(not path.parent.is_symlink(), 'Bot authority report directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_size <= 8 * 1024 * 1024,
                 'Bot authority report evidence must be a bounded regular file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024, 'Bot authority report evidence exceeded its limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def build_authority_summary(binding: dict, result: dict) -> dict:
    observed = result.get('child_receipt', {}).get('observed_checks', 0)
    passed = (result.get('status') == STATUS
              and result.get('case', {}).get('status') == STATUS
              and result.get('checks', {}).get('status') == 'PASS'
              and observed == STORY_CHECKS)
    status = 'PASS' if passed else ('CANCELLED' if result.get('status') == 'CANCELLED' else 'BLOCKED')
    requirements = [{'id': name, 'status': 'PASS' if passed else 'NOT_RUN'}
                    for name in REQUIREMENTS]
    return {
        'schema_version': 1,
        'run_id': binding['run_id'],
        'status': 'LOCAL_SUITE_PASS' if passed else status,
        'target': {'tier': 'local', 'environment_id': binding['run_id']},
        'scope': {
            'suite': SUITE,
            'parent': PARENT,
            'selected_cases': [CASE_ID],
            'legacy_inventory_count': len(binding.get('legacy_inventory', [])),
            'evidence_granularity': 'OWNED_CHILD_RECEIPT',
            'independent_slice': True,
            'release_parent_denominator': False,
            'environment': binding.get('environment', {}),
            'full_release_status': 'NOT_QUALIFIED',
            'full_story_status': 'LOCAL_INTEGRATED' if passed else 'NOT_QUALIFIED',
            'manifest_digest': fingerprint(binding),
        },
        'runner': {
            'executor_digest': binding['executor_digest'],
            'binary_kind': binding['binary_kind'],
            'bcs_binary_sha256': binding['bcs_binary_sha256'],
            'cli_binary_sha256': binding['cli_binary_sha256'],
            'auto_build': False,
            'strict_auth': True,
            'required_arguments': ['--binary', '--binary-sha256', '--cli-binary',
                                   '--cli-binary-sha256'],
        },
        'cases': [{
            'id': CASE_ID,
            'version': 1,
            'status': status,
            'functional': 'PASS' if passed else 'INCOMPLETE',
            'evidence_status': 'PASS' if passed else 'INCOMPLETE',
            'cleanup': result.get('resource_disposition', 'UNVERIFIED'),
            'resource_disposition': result.get('resource_disposition', DISPOSITION),
        }],
        'requirements': requirements,
        'reports': {
            'expected_assertion_count': len(REQUIREMENTS),
            'observed_assertion_count': sum(row['status'] == 'PASS' for row in requirements),
            'story_checks': observed,
        },
        'integrity': {'status': 'PASS' if passed else 'BLOCKED',
                      'issues': [] if passed else [result.get('error_type', 'BLOCKED')]},
        'redaction_gate': result.get('redaction', {}).get('status', 'BLOCKED'),
        'routes': [],
        'cleanup': {
            'status': 'OWNED_RUNTIME_STOPPED',
            'verification_scope': 'OWNED_RUNTIME_AND_AUTHORITY_RESOURCES_ONLY',
            'physical_data_erasure': 'NOT_VERIFIED',
            'associated_history_purge': 'NOT_VERIFIED',
        },
        'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
        'observer_binding': {'verification_mode': 'NOT_ATTACHED',
                             'live_verification': 'NOT_PERFORMED'},
        'verification_boundary': {
            'remote_validation': 'NOT_RUN',
            'preprod': 'DUAL_PREPROD_LEG_OUTSIDE_THIS_LOCAL_RECEIPT',
            'grants_release_credit': False,
            'release_parent_denominator': False,
            'not_proven': [
                'remote authentication and authorization',
                'preprod Bot-authority reachability (admission-gated read-only probes only)',
                'mutating preprod Bot-authority journey (frozen REMOTE_ADMISSION_GATED_READ_ONLY column)',
                'full release qualification',
            ],
        },
        'ready_to_execute': False,
    }


def publish_authority_report(root: Path, binding: dict, result: dict) -> dict:
    root = Path(root).resolve()
    _require(read(root / BINDING_NAME) == binding
             and executor_digest() == binding['executor_digest'],
             'Bot authority source changed before report publication')
    _require(read(root / FLOW_NAME) == result,
             'Bot authority publication must follow the actual flow result')
    summary = build_authority_summary(binding, result)
    from ..reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {
        'schema_version': 1,
        'contract': COMPLETION,
        'binding_digest': fingerprint(binding),
        'summary_sha256': output_hashes['reports/summary.json'],
        'outputs': output_hashes,
    }
    completion_digest = _exclusive(root / COMPLETION_NAME, completion)
    _require(all(_sha(root / name) == digest for name, digest in output_hashes.items())
             and _sha(root / COMPLETION_NAME) == completion_digest,
             'Bot authority report output changed during publication')
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_authority_report(root: Path) -> dict:
    root = Path(root).resolve()
    _require(not root.is_symlink(), 'Bot authority report requires a non-symlink root')
    binding = read(root / BINDING_NAME)
    result = read(root / FLOW_NAME)
    completion = read(root / COMPLETION_NAME)
    _require(completion.get('contract') == COMPLETION
             and completion.get('binding_digest') == fingerprint(binding)
             and completion.get('outputs', {}).get('reports/summary.json')
             == _sha(root / 'reports' / 'summary.json'),
             'Bot authority report completion is missing or changed')
    return build_authority_summary(binding, result)
