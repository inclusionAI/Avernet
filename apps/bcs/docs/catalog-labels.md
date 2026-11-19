# Configured catalog labels

`catalog_labels` is platform management metadata, separate from raw `agent_card`
and Group `card_template`. It is a flat object of string keys to string lists.
Labels do not confer authorization, infer status/strategy, or alter discovery
eligibility. No lifecycle is assigned implicitly.

## Policy and bootstrap

Distribute an independent regular `catalog-labels.toml` beside the selected BCS
config file(s), **before** starting the new binary:

```toml
[labels.lifecycle]
allowed_values = ["draft", "trial", "running", "offline"]
allow_multiple = false
```

The policy is loaded once, without environment overlays or `${VAR}` expansion,
and injected into Agent, scoped registration and Group application assembly.
Every key requires a finite unique nonblank `allowed_values` list and an explicit
`allow_multiple` boolean. Keys use ASCII letters/digits/underscore/hyphen;
`visibility` and `user_visibility` are reserved. Missing/malformed files fail
configuration loading. `[labels]` is a valid empty dictionary accepting only empty
objects. Configuration changes require a controlled restart. Do not shrink a
dictionary without planning for retained data: reads/publication do not silently
filter persisted labels through a newly smaller dictionary.

## Request contract

Canonical Agent registration, named/Provider/scoped registration and explicit
Agent update accept a top-level field; Group create/PATCH and legacy/browser
aliases use the same normalization. For example:

```json
{"catalog_labels":{"lifecycle":["running"]}}
```

Reject outer null, non-object values, unknown keys (including deletion keys),
unknown/case-modified values, non-array/non-null values and non-string array
entries. Preserve exact strings; deduplicate equal values in first-occurrence
order, then enforce distinct multiplicity. Known-key null/[] deletes that key.
The supplied object replaces the **whole** collection, not individual keys.
Invalid combined writes persist neither metadata nor Card and emit no committed
catalog-change notification. Existing authorization and response envelopes apply.

| Operation | Omitted | Explicit `{}` | Nonempty normalized object |
| --- | --- | --- | --- |
| Agent registration | initialize empty | empty | replace |
| Agent otherwise-valid explicit update | **clear** | clear | replace |
| Agent label-only update | no operation; existing UPDATE_REQUIRED | valid clear | replace |
| Group creation | initialize new resource empty; preserve on reuse | empty | replace |
| Group PATCH | **preserve** | clear | replace |
| URL/manual/background refresh | **preserve** | no parameter | no parameter |
| Internal Group/template maintenance | **preserve** | no implicit clear | no implicit replace |

Normal manager-worker/state-machine Groups may carry labels while private and
later publish them on a public transition. Chat/DM accept omitted/normalized-empty
labels but reject nonempty labels; this does not add Card support or Triple APIs.
Management views return `{}`, not null/omission, for empty/legacy labels. Group
list summaries do not gain a label field or per-result label lookup.

## Persistence, concurrency and publication

Owning columns: `bcs_bots.catalog_labels` and
`bcs_group_agent_cards.catalog_labels`; not `bot_info`, `bcs_groups`, Card JSON or
A2A runtime tables. New migrations: MySQL/OceanBase **048**, SQLite **046**.
Both columns are nullable JSON/TEXT for old rows and old inserts. New writes encode
normalized `{}` deterministically. SQL NULL/missing historical Group Card rows
read empty; malformed non-null JSON is an error. Explicit eligible Group label
writes materialize a missing template in the Group transaction; reads do not
backfill.

Agent writes use the existing identity/revision CAS and transaction; label-only
updates advance the same revision. Superseded refreshes fail rather than replay
stale metadata. Group writes explicitly preserve or replace labels in the
existing Group/event transaction; template display upserts never overwrite them.
Invalidation/catalog notification occurs only after commit. The joined Group
snapshot still reads Group and Card in one query. Labels are read/written as one
serialized value, not per-key queries. Detail projections can request one fixed
snapshot read; list summaries are unchanged. No database lock covers outbound
Card fetch or Fuse delivery.

Each Fuse V2 write sends all persisted custom labels plus current system labels:
Agents retain `visibility` and `user_visibility`; Groups retain only their
existing `visibility`. System labels cannot be overridden. Clearing emits a full
system-only map, not tombstones/empty-list patches. The queue stores identities
and re-reads current state on every retry. Downstream full-replacement/stale-key
removal is externally owned and is **not** proven by BCS payload tests.

## Compatibility and rollout

Service API **7.0.0** adds required Rust struct fields; external literals and
consumers rebuild together. `UpdateAgentCommand`/`A2aBotManagementService::update_agent`
carry source/visibility/label presence. Legacy update methods remain and clear
labels by omission. New unsupported trait methods refuse explicit label data;
there is no default that drops nonempty data. `upsert_with_catalog_labels` is the
Group Core/repo preserve/replace boundary. No Java or Group Triple API is added.

Old requests are not universally label-preserving: old Agent updates can clear
labels. New writers are rejected by old closed REST DTOs; strict old response
readers can reject the new field. Expand schema and provision the file, roll all
server replicas, verify nonempty readback, then enable label-aware clients.
Standalone public source availability is not an embedded host upgrade. OCB must
adapt source/assembly, pin an independently published verified public revision,
and pass its own integration gates.

Rollback disables new writers first and retains columns, values and migration
history. Old binary reads/upserts/runner compatibility and old publication loss
must be assessed separately; do not run destructive down migrations. Real
MySQL/OceanBase and deployed rollout remain separate acceptance gates. See the
[implementation validation record](../specs/2026-11-19-configured-catalog-labels/validation.md)
for actual commands/results and remaining NOT_RUN/NOT_VERIFIED items.
