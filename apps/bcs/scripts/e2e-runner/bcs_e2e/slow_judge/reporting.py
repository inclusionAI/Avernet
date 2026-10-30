"""Canonical reports for the dual slow-Judge callback slice receipt.

The dual entry projects its machine-readable run report into the runner report
triple (``summary.json`` / ``junit.xml`` / ``summary.md``) beside the state
directory; the preprod BLOCKED leaf stays under its own dated root outside this
tree.  The summary is a migration receipt only: it inherits the ledger MOCK gate
honestly, never grants release credit, and never claims remote validation.
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
from .contract import (COMPLETION_CONTRACT, DUAL_CASE_ID, DUAL_SUITE, MOCK_GATE_INHERITANCE,
                       OBLIGATION_COUNT, PARENT, PREPROD_MODES, REDACTION_CANARIES,
                       REQUIREMENTS, REPORT_CONTRACT, SOURCE_SUITE, preprod_obligation_modes)

BINDING_NAME = 'slow-judge-dual-binding.json'
FLOW_NAME = 'slow-judge-dual-run.json'
COMPLETION_NAME = 'slow-judge-dual-completion.json'
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')


def _require(value: object, message: str) -> None:
    if not value:
        raise RunStateError(message)


def _sha(path: Path) -> str:
    _require(not path.parent.is_symlink(), 'slow judge report directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_size <= 8 * 1024 * 1024,
                 'slow judge report evidence must be a bounded regular file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024, 'slow judge report evidence exceeded its limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def scan_redaction(name: str, payload) -> dict:
    """Self-contained credential-canary scan for one evidence stream."""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    if not isinstance(payload, (bytes, bytearray)):
        raise RunStateError('slow judge redaction scan requires bytes')
    captured = sorted(canary for canary in REDACTION_CANARIES
                      if canary.encode('utf-8') in payload)
    return {'stream': name, 'clean': not captured, 'canaries': captured,
            'bytes': len(payload)}


def build_slow_judge_dual_summary(binding: dict, report: dict) -> dict:
    """Project the dual run report into the frozen triple-report summary."""
    passed = report.get('status') == 'PASS'
    tier = report.get('environment', {}).get('tier', 'unknown')
    modes = preprod_obligation_modes()
    mode_counts = {mode: len([name for name in REQUIREMENTS if modes[name] == mode])
                   for mode in PREPROD_MODES}
    closure = MOCK_GATE_INHERITANCE
    requirements = [{'id': name, 'status': 'PASS' if passed else 'NOT_RUN'}
                    for name in REQUIREMENTS]
    observed = sum(1 for row in requirements if row['status'] == 'PASS')
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
            'source_suite': SOURCE_SUITE,
            'evidence_granularity': 'OWNED_CHILD_RECEIPT',
            'independent_slice': True,
            'release_parent_denominator': False,
            'environment': report.get('environment', {}),
            'full_release_status': 'NOT_QUALIFIED',
            'dual_story_status': 'DUAL_INTEGRATED' if passed else 'NOT_QUALIFIED',
            'manifest_digest': fingerprint(binding),
            'legacy_inventory_count': binding.get('legacy_inventory_count', 0),
            'inherited_ledger_gate': closure['ledger_gate'],
        },
        'runner': {
            'executor_digest': binding['executor_digest'],
            'binary_kind': 'bcs',
            'bcs_binary_sha256': binding.get('bcs_binary_sha256'),
            'cli_binary_sha256': binding.get('cli_binary_sha256'),
            'cli_pair_status': binding.get('cli_pair_status'),
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
        }],
        'requirements': requirements,
        'reports': {
            'expected_assertion_count': OBLIGATION_COUNT,
            'observed_assertion_count': observed,
            'applicable_assertion_count': OBLIGATION_COUNT,
            'local_suite_id': binding.get('local_suite_id', SOURCE_SUITE),
            'inherited_ledger_gate': closure['ledger_gate'],
            'preprod_obligation_mode_counts': mode_counts,
            'observed_local_assertion_count': observed if passed else 0,
        },
        'integrity': {'status': 'PASS' if passed else 'BLOCKED',
                      'issues': [] if passed else [report.get('error_type')
                                                   or 'SLOW_JUDGE_DUAL_BLOCKED']},
        'mock_gate_inheritance': {
            'ledger_gate': closure['ledger_gate'],
            'ledger_row': closure['ledger_row'],
            'ledger_file': closure['ledger_file'],
            'gate_owner': closure['gate_owner'],
            'local_leg': closure['local_leg'],
            'receiver_module': closure['receiver_module'],
            'receiver_class': closure['receiver_class'],
            'receiver_reuse': closure['receiver_reuse'],
            'receiver_surfaces': list(closure['receiver_surfaces']),
            'preprod_leg': closure['preprod_leg'],
            'not_proven': list(closure['not_proven']),
            'grants_release_credit': False,
        },
        'preprod_obligation_mode_counts': mode_counts,
        'routes': [],
        'cleanup': {
            'status': report.get('resource_disposition', 'UNVERIFIED'),
            'verification_scope': ('OWNED_RUNTIME_AND_RECEIVER_ONLY' if tier == 'local'
                                    else 'NO_REMOTE_RESOURCES_CREATED'),
            'physical_data_erasure': 'NOT_VERIFIED',
            'associated_history_purge': 'NOT_PERFORMED',
        },
        'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
        'observer_binding': {'verification_mode': 'NOT_ATTACHED',
                             'live_verification': 'NOT_PERFORMED'},
        'verification_boundary': {
            'remote_validation': 'NOT_RUN',
            'preprod': ('DUAL_PREPROD_LEG_LIVE_BEARER_RECEIPT' if tier == 'preprod' and passed
                        else 'PREPROD_JOURNEY_SEAM_PENDING_MAINLINE'),
            'grants_release_credit': False,
            'release_parent_denominator': False,
            'not_proven': list(closure['not_proven']),
        },
        'mock_boundary': {'not_proven': list(closure['not_proven'])},
        'ready_to_execute': False,
    }


def publish_slow_judge_dual_report(root: Path, binding: dict, report: dict) -> dict:
    """Publish the triple report plus its exclusive completion receipt."""
    root = Path(root).resolve()
    _require(read(root / BINDING_NAME) == binding
             and executor_digest() == binding['executor_digest'],
             'slow judge source changed before report publication')
    _require(read(root / FLOW_NAME) == report,
             'slow judge publication must follow the actual dual run report')
    summary = build_slow_judge_dual_summary(binding, report)
    from ..reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {
        'schema_version': 1,
        'contract': COMPLETION_CONTRACT,
        'binding_digest': fingerprint(binding),
        'summary_sha256': output_hashes['reports/summary.json'],
        'outputs': output_hashes,
    }
    completion_digest = _exclusive(root / COMPLETION_NAME, completion)
    _require(all(_sha(root / name) == digest for name, digest in output_hashes.items())
             and _sha(root / COMPLETION_NAME) == completion_digest,
             'slow judge report output changed during publication')
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_slow_judge_dual_report(root: Path) -> dict:
    """Offline verifier over a published dual report directory."""
    root = Path(root).resolve()
    _require(not root.is_symlink(), 'slow judge report requires a non-symlink root')
    binding = read(root / BINDING_NAME)
    report = read(root / FLOW_NAME)
    completion = read(root / COMPLETION_NAME)
    _require(completion.get('contract') == COMPLETION_CONTRACT
             and completion.get('binding_digest') == fingerprint(binding)
             and completion.get('outputs', {}).get('reports/summary.json')
             == _sha(root / 'reports' / 'summary.json'),
             'slow judge dual report completion is missing or changed')
    return build_slow_judge_dual_summary(binding, report)
