# ADR-0004: Explicit lifecycle state machines for Bot, Device, and Publish

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

BaaS manages long-running, externally-observable entities whose state must be
unambiguous across API, database, and audit surfaces. Ad-hoc status strings
had already caused subtle bugs (retry-after-reject, restore-on-stop-failure),
so the team encoded each lifecycle as a StrEnum with explicit transition
tables in the enum docstrings, and moved them into `api/` during the refactor
("Extracted from domain/model/publish.py as part of api/domain →
api/{domain}/ refactoring").

## Decision

Manage every core entity lifecycle through a documented state machine:

- **BotStatus** (`api/bot_manage/_enums.py`):
  `PENDING → ACTIVE | FAILED` (CREATE publish outcome),
  `ACTIVE → STOPPING → STOPPED` (STOP publish success) with
  `STOPPING → ACTIVE` restore on STOP failure, and
  `ACTIVE → DESTROYING → RELEASED`; `DESTROYING` is retryable
  (re-approve a rejected DESTROY publish); `RELEASED` terminal.
- **DeviceStatus** (`api/device_manage/_device.py`): container-mapped states —
  `PENDING` (provisioning), `ACTIVE` (running), `UPDATING` (drained,
  excluded from load balancing), `STOPPED` (provider destroyed, record kept),
  `FAILED`, `OFFLINE` (reachable machine, daemon not running), `RELEASED`.
- **PublishStatus** (`api/publish_manage/_enums.py`): the gated pipeline
  machine `PENDING → ACTIVE → APPROVING → …` with terminals
  `REJECTED / FAILED / SUCCESS / REVOKED` (see ADR-0005); plus per-batch
  `BatchStatus` (`PENDING → RUNNING → COMPLETED | FAILED | ROLLED_BACK`) and
  per-record `PublishRecordResult`.
- The enum docstrings — including the ASCII transition tables — are the
  authority for legal transitions; services implement exactly those tables
  (`PublishService.TRANSITIONS` maps `(status, action) → new_status`).

## Consequences

**Positive:** API responses and DB rows share one vocabulary; retry/restore
semantics are discoverable in-code; tests can assert transitions rather than
 incidental flags.

**Negative:** new statuses require updating docstring tables and services in
lockstep; the tables in docstrings can drift from code if edited carelessly.

**Risks:** duplication between the in-docstring transition table and the
`TRANSITIONS` mapping — keep them adjacent and covered by tests.

## Evidence

- community: `src/secbaas/community/api/bot_manage/_enums.py` — `BotStatus` transition table (STOPPING/STOPPED restore, retryable DESTROYING)
- community: `src/secbaas/community/api/device_manage/_device.py` — `DeviceStatus` semantics incl. OFFLINE/STOPPED
- community: `src/secbaas/community/api/publish_manage/_enums.py` — `PublishStatus`, `BatchStatus`, `PublishRecordResult` tables
- community: `src/secbaas/community/core/service/publish_manage/_publish_service.py` — `TRANSITIONS` implementation