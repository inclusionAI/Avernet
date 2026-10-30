"""Canonical reports for the dual Retired service policy slice receipt.

Both legs project their machine-readable dual run report into the runner
report triple (``summary.json`` / ``junit.xml`` / ``summary.md``) beside the
state directory; the preprod bearer BLOCKED leaf stays under its own dated
root outside this tree. The summary is a migration receipt only: it never
grants release credit and never claims remote validation.
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
from .contract import (BEARER_STORY_CHECKS, DUAL_CASE_ID, DUAL_SUITE, NOT_APPLICABLE_REQUIREMENT,
                       REPORT_CONTRACT)
from ..local_service_policy_contract import (PARENT, REQUIREMENTS, STORY_CHECKS)

BINDING_NAME = 'service-policy-binding.json'
FLOW_NAME = 'service-policy-dual-run.json'
CASE_NAME = 'service-policy.json'
COMPLETION_NAME = 'service-policy-dual-completion.json'
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'dual-service-policy-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path: Path) -> str:
    _require(not path.parent.is_symlink(), 'service policy report directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_size <= 8 * 1024 * 1024,
                 'service policy report evidence must be a bounded regular file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024, 'service policy report evidence exceeded its limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def build_service_policy_dual_summary(binding: dict, report: dict) -> dict:
    passed = report.get('status') == 'PASS'
    tier = report.get('environment', {}).get('tier', 'unknown')
    if tier == 'preprod':
        redaction = (report.get('preprod_story') or {}).get('redaction_status', 'NOT_RUN')
        cleanup = 'DISPOSABLE_PREPROD_GROUPS_AND_SESSIONS_DELETED_DRIVER_BOT_RETAINED'
    else:
        gate = ((report.get('local_owned_run') or {}).get('child_receipt') or {}).get(
            'redaction_gate')
        redaction = 'PASS' if gate is True else 'BLOCKED'
        cleanup = 'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED'
    requirements = [{'id': name, 'status': 'PASS' if passed else 'NOT_RUN'}
                    for name in REQUIREMENTS]
    return {
        'schema_version': 1,
        'run_id': binding['run_id'],
        'status': 'PASS' if passed else 'BLOCKED',
        'target': {'tier': tier, 'environment_id': binding['run_id']},
        'scope': {
            'suite': DUAL_SUITE,
            'parent': PARENT,
            'selected_cases': [DUAL_CASE_ID],
            'source_case_id': binding.get('source_case_id'),
            'evidence_granularity': 'OWNED_CHILD_RECEIPT',
            'independent_slice': True,
            'release_parent_denominator': False,
            'environment': report.get('environment', {}),
            'full_release_status': 'NOT_QUALIFIED',
            'dual_story_status': 'DUAL_INTEGRATED' if passed else 'NOT_QUALIFIED',
            'manifest_digest': fingerprint(binding),
        },
        'runner': {
            'executor_digest': binding['executor_digest'],
            'binary_kind': 'bcs',
            'bcs_binary_sha256': binding.get('bcs_binary_sha256'),
            'auto_build': False,
            'required_arguments': ['--binary', '--binary-sha256'],
        },
        'obligation_summary': report.get('obligation_summary', {}),
        'cases': [{
            'id': DUAL_CASE_ID,
            'version': 1,
            'status': 'PASS' if passed else 'BLOCKED',
            'functional': 'PASS' if passed else 'INCOMPLETE',
            'evidence_status': 'PASS' if passed else 'INCOMPLETE',
            'cleanup': report.get('resource_disposition', 'UNVERIFIED'),
            'resource_disposition': report.get('resource_disposition', 'UNVERIFIED'),
        }],
        'requirements': requirements,
        'reports': {
            'expected_assertion_count': len(REQUIREMENTS),
            'observed_assertion_count': sum(row['status'] == 'PASS' for row in requirements),
            'applicable_assertion_count': len(REQUIREMENTS) - 1,
            'not_applicable_requirement': NOT_APPLICABLE_REQUIREMENT,
            'story_checks': {'local': STORY_CHECKS, 'bearer': BEARER_STORY_CHECKS},
            'observed_story_checks': (
                (report.get('local_owned_run') or {}).get('child_receipt') or {}
            ).get('observed_checks', 0) if tier != 'preprod'
            else (report.get('preprod_story') or {}).get('observed_checks', 0),
        },
        'integrity': {'status': 'PASS' if passed else 'BLOCKED',
                      'issues': [] if passed else [report.get('error_type', 'BLOCKED')]},
        'redaction_gate': redaction,
        'routes': [],
        'cleanup': {
            'status': cleanup,
            'verification_scope': ('OWNED_RUNTIME_AND_SERVICE_RESOURCES_ONLY' if tier != 'preprod'
                                   else 'DISPOSABLE_PREPROD_GROUPS_AND_SESSIONS_ONLY'),
            'physical_data_erasure': 'NOT_VERIFIED',
            'associated_history_purge': 'NOT_VERIFIED',
        },
        'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
        'observer_binding': {'verification_mode': 'NOT_ATTACHED',
                             'live_verification': 'NOT_PERFORMED'},
        'verification_boundary': {
            'remote_validation': 'NOT_RUN',
            'preprod': ('DUAL_PREPROD_LEG_LIVE_BEARER_RECEIPT' if tier == 'preprod' and passed
                        else 'DUAL_PREPROD_LEG_OUTSIDE_THIS_LOCAL_RECEIPT'),
            'grants_release_credit': False,
            'release_parent_denominator': False,
            'not_proven': [
                'remote authentication and authorization',
                'preprod service policy reachability (proven only by a live bearer receipt)',
                'full release qualification',
            ],
        },
        'ready_to_execute': False,
    }


def publish_service_policy_dual_report(root: Path, binding: dict, report: dict) -> dict:
    root = Path(root).resolve()
    _require(read(root / BINDING_NAME) == binding
             and executor_digest() == binding['executor_digest'],
             'service policy source changed before report publication')
    _require(read(root / FLOW_NAME) == report,
             'service policy publication must follow the actual dual run report')
    summary = build_service_policy_dual_summary(binding, report)
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
             'service policy report output changed during publication')
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_service_policy_dual_report(root: Path) -> dict:
    root = Path(root).resolve()
    _require(not root.is_symlink(), 'service policy report requires a non-symlink root')
    binding = read(root / BINDING_NAME)
    report = read(root / FLOW_NAME)
    completion = read(root / COMPLETION_NAME)
    _require(completion.get('contract') == COMPLETION
             and completion.get('binding_digest') == fingerprint(binding)
             and completion.get('outputs', {}).get('reports/summary.json')
             == _sha(root / 'reports/summary.json'),
             'service policy dual report completion is missing or changed')
    return build_service_policy_dual_summary(binding, report)
