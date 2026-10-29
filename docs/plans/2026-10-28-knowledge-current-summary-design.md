# Bounded current knowledge publication summary

Approved on 2026-10-28: reuse the existing `ac_bot_common_config` for one current
knowledge result per immutable source Bot. No new table/column/index, worker,
native status rule or legacy HTTP transition change.

- Keep immutable version snapshots/results in publication.ext and per-KB flags
  and editor revisions in ac_bot_knowledge_binding.
- Store current result, actual observed workflow watermark, native source scope,
  current Owner/tenant/space and <=100 selected/materialized IDs in one reserved
  common-config entry. Its unique key uses the source Bot PK plus an immutable-entity digest, business Bot ID,
  environment and a reserved knowledge key; global immutable source PK isolates
  tenants even when a legacy callback cannot resolve the guarded Bot. Validate
  tenant/space/current Owner on reads. Unresolved callbacks may only record UNKNOWN
  against that native identity, never mutate bindings or borrow another scope.
- Read the entry by its existing unique key; no publication-history JSON scan.
  Read flags only by the <=100 selected unique KB keys. Fence the flags statement
  against the exact summary value so a concurrent replacement cannot mix states.
- Under the existing Bot lock (and config-row lock), compare actual workflow IDs,
  reject stale completions and replace flags by the previous/new bounded ID sets.
  Summary, native CAS and flags share the caller's transaction and rollback.
- Missing/corrupt summaries fail closed on reads. No history/binding scan fallback.
  Legacy online flags without a summary are not effective; successful trusted
  republication initializes the summary. Old untracked physical flags are never
  read as grants and are not scanned for opportunistic cleanup.
- Reserve the key from generic operator configuration writes/deletes; native
  snapshots remain protected. Use the current Session, not a repository method
  which commits its own Session. Actual SQL failures propagate.
- The existing table must be provisioned before enabling this path; no startup
  DDL or production operation is added. Historical local query-plan evidence is
  superseded by growing H/B probes and native lifecycle/failure regressions.
