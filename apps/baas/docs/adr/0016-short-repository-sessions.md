# ADR-0016: Short repository sessions and service-owned orchestration

## Status

accepted (transactional repository boundary clarified by ADR-0017)

## Date

2026-10-29

## Applies to

all

## Context

Repository decorators currently span ticket transition policy, diagnostic
queries and nested repository calls. A checked-out connection remains held
until the surrounding session exits, even while no SQL executes. Diagnostic
logging must not itself force a checkout. The existing explicit transaction
scope also uses a process-global session stack and does not exit the plugin's
context manager correctly.

## Decision

- For independent short calls, services own transition policy, orchestration
  and interpretation of failed conditional writes. Repositories own persistence,
  environment and tenant filters. Ticket CAS receives allowed source states and
  executes one conditional UPDATE; it does not read to explain failure.
  This is not a blanket ban on repository logic: see
  [ADR-0017](0017-transactional-repository-boundary.md) for atomic workflows.
- Ticket services perform any diagnostic read in a separate short repository
  call after the UPDATE has committed and released its connection. This read
  observes the latest state, not necessarily the state at the failed UPDATE.
  Never retry with an unconditional write. Same-state success requires the
  requested error message, if supplied, to have been persisted too.
- Ordinary `orm_session()` calls remain independent. Only an explicit
  synchronous `scoped_orm_session()` joins repository calls into a transaction.
  Its plugin context owns commit, rollback and close exactly once. Nested
  explicit scopes join; an escaping inner error marks the scope rollback-only,
  even if the caller catches that error.
- Explicit transactions are local to the creating thread and asyncio task.
  Copying a context to a worker thread/task must not share a SQLAlchemy Session;
  fail clearly instead. Do not wrap async/external work in these scopes.
- Atomic multi-row persistence (e.g. slot replacement or JSON read/modify/write)
  is not mechanically split into independently committed calls. It may stay in
  one decorated repository method, including transaction-dependent logic;
  no service transaction decorator is introduced in this change.
- Performance diagnostics identify sessions without calling
  `session.connection()`. Method duration is not labelled SQL execution time.

## Consequences

**Positive:** business decisions run without idle checked-out connections;
CAS concurrency and explicit atomic work retain their guarantees; plugin
cleanup is deterministic and concurrent requests cannot share a transaction.

**Negative:** failed CAS diagnostics may require a second database round-trip;
repository ticket contracts and callers must migrate together.

**Risks:** copying a transaction context to `asyncio.to_thread` now fails
instead of accidentally sharing a Session. Existing multi-row operations
must be migrated individually, not placed under a blanket service transaction.

## Evidence

- community: `src/secbaas/community/core/database/_manager.py`
- community: `src/secbaas/community/core/repository/__init__.py`
- community: `src/secbaas/community/core/repository/file_transfer_ticket/_orm_repository.py`
- community: `src/secbaas/community/core/repository/session_file_ticket/_orm_repository.py`
- community: `src/secbaas/community/core/service/bot_runtime/dispatcher/_file_transfer_dispatcher.py`
- community: `src/secbaas/community/core/service/session_file_sharing/_dispatcher.py`
- community: `tests/unit/core/repository/test_with_orm_session_isolation.py`
