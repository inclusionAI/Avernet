"""Capture source identity before importing runner implementation modules."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Reviewed flat focused-module allowance; the history of increments is
# documented below disk_digest.
FLAT_FILE_BUDGET = 340


def _artifact_paths() -> list[Path]:
    return [ROOT / 'runner.py', *sorted((ROOT / 'bcs_e2e').glob('*.py'))]


def disk_digest() -> str | None:
    paths = _artifact_paths()
    # Keep the executor inventory small, but leave room for focused modules;
    # the old hard cap blocked splitting oversized adapters while still
    # enforcing the stricter per-file 1,000-line CI limit. The dual Workspace
    # round-trip contract added one focused Case-executor module; its proposal
    # counterpart was retired upstream with the group proposal surface
    # (dev 72f9dff36, Avernet #3114), returning the allowance two files; the
    # structured-collaboration
    # core slice adds its Case contract and remote adapter, and the
    # register-onboard register core slice adds its Case contract and
    # remote adapter, so the focused-module allowance grows by exactly
    # those six files. The dual Channel product-CLI slice adds its Case
    # Profile, product-CLI runner and remote adapter, so the allowance
    # grows by exactly those three files. The fixed-Loop per-Run sandbox
    # isolation slice adds one focused module, and the fixed-Loop case
    # executor adds its owned-local seam implementation, so the
    # allowance grows by exactly those two files. The fixed-Loop owned
    # executor adds its run lifecycle and offline report projection, so
    # the allowance grows by exactly those two files. The edge
    # friend-mutation executor adds its case contract, owned adapter,
    # run lifecycle and offline report projection, so the allowance
    # grows by exactly those four files. The human delivery-policy
    # CAS executor adds its source contract, owned adapter, run
    # lifecycle and offline report projection, so the allowance grows
    # by exactly those four files. The operator CLI parent executor
    # adds its source contract, owned adapter, run lifecycle and
    # offline report projection, so the allowance grows by exactly
    # those four files. The provider-free owned runtime holds one
    # fixture Bot streaming connection per chat delivery window, so
    # the operator CLI slice adds one focused Bot WebSocket seam,
    # and the allowance grows by exactly that one file. The retained
    # Session/WS offline closure projection adds one focused review module, so
    # the allowance grows by exactly that one file. Splitting the crowded
    # Workspace command mapping into its focused command contract adds
    # exactly one more file. The Session Service lifecycle slice is also
    # split into its own focused Case module, and the completion plus
    # service-roundtrip slice adds one further focused module.
    # The collaboration-team cross-environment product-CLI slice adds its
    # Profile, bounded Runner, remote Adapter and Case contract, and its
    # owned-local run/report lifecycle, so the focused-module allowance grows
    # by exactly those six files. Its focused transport
    # reader adds one further file. Joining the three custom-collaboration
    # obligations into one owned-local parent case adds one focused module, so
    # the allowance grows by exactly that one file. The complete Session
    # Service parent case similarly combines its four focused slices in one
    # focused composition module, so the allowance grows by exactly one file.
    # The custom-collaboration and Session Service owned-local lifecycles add
    # four focused execution/report modules, and the offline 6.6 entry-switch
    # review adds one focused module, so the allowance grows by exactly
    # those five files. The reviewed remote-reconcile lifecycle is also a
    # single focused module, so the allowance grows by exactly one file.
    # The 7.2 Aliyun target binding adds one focused offline contract module.
    # The dual Workspace status/termination and Group case command lifecycles each add
    # one further focused module, and the remaining product-CLI case lifecycle adds
    # one more, so the focused-module allowance grows by those three files.
    # The dual Session, Channel, Session-file and friendship product-CLI
    # command lifecycles each add one focused module, so the focused-module
    # allowance grows by exactly those four files. The remaining Workspace
    # incident/roundtrip (proposal slice later retired upstream) and
    # collaboration-team command lifecycles add two further focused modules.
    # The Channel-binding, Edge read, actor-status, register,
    # structured-core, structured-templates, friend-decision,
    # friend-public-accept and network-read command lifecycles add nine
    # further focused modules. Splitting the crowded cross-environment command
    # registration out of the CLI adds one more focused module. The dual Event
    # Webhook subscription command lifecycle adds one further focused module.
    # The dual public Session JWT/WS command lifecycle adds one further focused
    # module while reusing its existing admission and owned-local executor.
    # The dual Group Context product-CLI story adds one focused Case contract,
    # one remote Adapter and one command Run lifecycle, so the allowance grows
    # by exactly those three files.
    # The owned-local caller-execution black-box slice adds its focused source
    # binding contract, run lifecycle and offline report projection modules, so
    # the focused-module allowance grows by exactly those three files.
    # The owned-local Draft group black-box slice adds its focused source
    # binding contract, run lifecycle and offline report projection modules,
    # so the focused-module allowance grows by exactly those three files.
    # The owned-local Result-queue black-box slice adds its focused source
    # binding contract and the later run/report projection modules, so the
    # focused-module allowance grows by exactly those three files in advance.
    # The owned-local Nested fixed-loop black-box slice adds its focused source
    # binding contract and its run/report projection modules, so the allowance
    # grew by exactly those three files in advance.
    # The Edge friend-mutation backlog split moves the fixture service-key
    # adapter into its own focused module and adds one independent extras
    # mixin, so the allowance grows by two more focused files.
    # The Edge friend-mutation backlog slice adds its tc-friend-auth-sync and
    # dept-scope friend-ext owned-local contracts, runs and report projections,
    # so the allowance grows by those five flat files. The Provider owner
    # backlog promotion adds its source binding contract, run lifecycle and
    # offline report projection as three flat modules, and the shared backlog
    # command router adds one flat module. The Session-owner and Draft-delivery
    # slices remain subpackages outside this flat digest. The resulting flat
    # count was 332 under a 340-file allowance before the dual Provider-subbots
    # route-probe command added one flat focused module, leaving seven slots.
    # The enforced count keeps runner.py alongside the focused modules.
    # The dual-environment Edge friend-mutation remote slice adds its focused
    # remote Adapter and command Run lifecycle modules, so the focused-module
    # allowance grows by exactly those two files. The dual-environment Session
    # Service product-CLI command Run lifecycle adds one focused module that
    # reuses the owned-local Session Service adapters, so the allowance grows
    # by exactly that one file. The dual-environment operator product-CLI command
    # lifecycle adds its focused remote Runner and Adapter modules, so the
    # allowance grows by exactly those two files in advance.
    # This session's dual Operator, Direct-chat, Custom-collaboration and
    # Delivery-policy product-CLI command entries add four focused case-run
    # modules, so the allowance grows by exactly those four files plus one
    # slot of headroom for a future adapter split.
    # This session's Direct-chat and Custom-collaboration leaf splits consume
    # the staged headroom and add two further focused leaf modules, so the
    # allowance grows by exactly those two leaf files.
    # The owned-local Retired service policy black-box slice adds its focused
    # source binding contract, run lifecycle and offline report projection
    # modules, so the allowance grew by exactly those three files plus one
    # slot of headroom for a future adapter split.
    # The owned-local Bot-authority user journey parent slice adds its
    # focused source binding contract, owned adapter, run lifecycle and
    # offline report projection modules, so the allowance grows by exactly
    # those four files.
    # The Session assertion-density backfill splits two reviewed 1,000-line
    # modules instead of growing them: the product-CLI Session slices move to
    # session_cli_contract and the public Session storage/deletion helpers to
    # local_session_public_storage, so the flat count grows by exactly those
    # two files (335 of 340).
    # The dual Provider-owner promotion adds one flat focused dual case-run module
    # (336 of 340); the dual Session-owner run lands in the session_owner subpackage
    # and adds no flat file.
    # R1 tolerant aperture (2026-10-31): a budget breach must not poison
    # package import (pytest collection, --help, read-only probes). The
    # budget gate closes fail-closed at Run time in process_digest().
    if len(paths) > FLAT_FILE_BUDGET:
        return None
    digest = hashlib.sha256()
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError('executor artifact must contain bounded regular source files')
        data = path.read_bytes()
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0')
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


# Imported by package __init__ before cli, adapters, pilot or other leaf
# modules. None when the flat executor artifact breaches its reviewed
# module budget; the breach fails closed at Run time instead of breaking
# every import consumer.
PROCESS_EXECUTOR_DIGEST = disk_digest()

if PROCESS_EXECUTOR_DIGEST is None:
    print(
        'bcs_e2e: executor artifact breaches its flat source-file budget '
        f'(>{FLAT_FILE_BUDGET} focused modules); receipts gate closed at '
        'process_digest() until the allowance is reviewed or split',
        file=sys.stderr,
    )


def process_digest() -> str:
    if PROCESS_EXECUTOR_DIGEST is None:
        raise ValueError(
            f'executor artifact breaches its flat source-file budget '
            f'(>{FLAT_FILE_BUDGET} focused bcs_e2e/*.py modules plus '
            'runner.py); split oversized modules or land a reviewed '
            'allowance increment in bcs_e2e/artifact.py before starting a Run'
        )
    current = disk_digest()
    if current is None or current != PROCESS_EXECUTOR_DIGEST:
        raise ValueError('runner source changed after process import; start a fresh process and Run')
    return PROCESS_EXECUTOR_DIGEST
