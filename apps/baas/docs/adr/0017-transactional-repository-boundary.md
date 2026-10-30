# ADR-0017: Keep atomic database workflows in repositories

## Status

accepted (clarifies ADR-0016)

## Date

2026-10-29

## Applies to

all

## Context

ADR-0016 shortened ticket and clone sessions. Moving every conditional or
multi-statement method to a service would also split operations that need a
single commit. There is no service transaction decorator in the current API;
introducing a generic transaction framework is not part of this change.

## Decision

- A bounded, database-only operation whose writes/read-modify-write must commit
  together may remain in one decorated repository method, including the policy
  needed for that atomic operation. Examples are bot version replacement,
  administrator recovery, bot/relation cleanup, JSON read-modify-write, locks
  and conditional queue dequeue. An ordinary transaction is not itself proof
  that concurrent JSON updates cannot be lost.
- Do not use the existence of a session as a reason to group unrelated work.
  Admission policy, query branching, output formatting and failed-write
  diagnostics that do not require one atomic snapshot belong in services or
  workers. Finish each repository call before making the next decision/read.
- In particular, queue admission counts are advisory, not strict cross-worker
  quotas. Moving them outside dequeue does not remove an existing guarantee.
- Services supply renewal cutoffs and creation defaults. Repositories retain
  tenant/env isolation, SQL filtering, JSON decoding and record projection;
  do not replace database filtering with unbounded in-memory scans.
- Do not add a service transaction decorator or wrap remote I/O/async work in
  database transactions in this change.

## Consequences

**Positive:** transaction-dependent workflows keep their atomicity; unrelated
policy does not extend a checked-out connection's lifetime.

**Negative:** atomic repository workflows can contain domain-specific logic.
They need explicit transactional contracts rather than a blanket no-logic rule.

**Risks:** a separated diagnostic sees later state, not the failed write's
snapshot. Bot cleanup batching/state revalidation and JSON concurrency control
remain separate correctness work, not reasons to split their commits.

## Evidence

- community: `src/secbaas/community/core/repository/bot/_orm_repository.py`
- community: `src/secbaas/community/core/repository/bot_session/_orm_repository.py`
- community: `src/secbaas/community/core/repository/bot_run_queue/_orm_repository.py`
- community: `src/secbaas/community/core/service/bot_run/_worker.py`
- community: `docs/adr/0016-short-repository-sessions.md`
