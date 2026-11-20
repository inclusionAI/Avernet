## Context

Status: local implementation tested / external acceptance pending, NOT_PUBLISHED, NOT_DEPLOYED, 2026-11-19. Source baseline is `8fc2e5f7693fc5caca97e5b5591148885d38ff10`; the implementation refreshed this target before editing; see validation.md for current evidence.

Agent Card management stores the remote document, URL and revision in `bcs_bots`. The V1 A2A delivery adapter has closed request DTOs. Group Cards are store-generated templates, not uploaded AgentCard documents: Group create/PATCH writes Group metadata and maintains `bcs_group_agent_cards` transactionally. Publication reads current control-plane and Card state; Agent labels currently contain `visibility` and `user_visibility`, Group labels contain `visibility`.

Source anchors (repository-relative; inspected at the recorded baseline):
- `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/a2a/management.rs`
- `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/{dto,routes}/group.rs`
- `apps/bcs/crates/application/bcs-app-a2a-bot/src/{management,registration,v1}.rs`
- `apps/bcs/crates/services/bcs-bot-store/src/a2a.rs`
- `apps/bcs/crates/services/bcs-group-store/src/{card_template,card_snapshot,store_writes}.rs`
- `apps/bcs/crates/services/bcs-fusion/src/core/card_sync.rs`
- `apps/bcs/crates/bootstrap/bcs/src/config_loader.rs`

## Goals / Non-Goals

**Goals:** one public policy, strictly typed structure, config-owned key/value dictionaries and multiplicity, durable Bot/Group labels, complete publication, operation-specific omission handling, and a deployable upstream-first migration.

**Non-Goals:** change remote A2A Card schema; infer lifecycle from visibility, status or strategy; add a default lifecycle; allow arbitrary extensions as catalog labels; add Group Triple/Java APIs; implement downstream incremental deletion, label search/filter UI, hot reload or a new rollout flag. Label enumeration never authorizes access.

## Decisions

### D1. Metadata and typed values

The name is `catalog_labels` at DB/REST/service boundaries. The normalized public type is a named `CatalogLabels` backed by `BTreeMap<String, Vec<String>>` (or equivalent deterministic map), not arbitrary JSON. Delivery DTOs accept per-key nullable lists to represent deletion, then normalize to the domain type; domain/persistence never retain null/empty-list entries. A nullable outer object is forbidden. `agent_card` and `card_template` remain separate documents; their unknown fields are not another label source.

Rejecting raw values is preferable to projecting a second copy from the Card: URL refresh cannot then overwrite platform metadata. Group PATCH needs an explicit preserve/replace command; serde `Option` alone must not collapse top-level null into omission. Agent update also records whether the field was explicitly present so a label-only `{}` update is valid while the pre-existing completely empty request remains `UPDATE_REQUIRED`.

### D2. Dictionary file and multiplicity

Load `catalog-labels.toml` from the selected runtime config directory exactly once at startup, without environment suffixes, environment substitutions or overlay merging. Resolve files only in bootstrap/config loading and inject an immutable policy into the application services; core code never discovers files or reads process environment. Package the file in every runnable public distribution and test fixture affected by the new startup dependency.

```toml
[labels.lifecycle]
allowed_values = ["draft", "trial", "running", "offline"]
allow_multiple = false
```

Every key requires both a finite, unique, nonblank `allowed_values` list and an explicit boolean `allow_multiple`. `false` allows zero or one DISTINCT value after duplicate removal; `true` allows any subset of that key's configured values. No schema/default branch special-cases `lifecycle`. Changing the fixture dictionary to a different key and a multi-valued policy must pass without production code changes. Production code contains no initial business key/value literals; fixtures and shipped config may contain examples.

Use exact, case-sensitive strings without trimming/alias correction; reject malformed/blank values and preserve the first occurrence when removing duplicates. Keys must be compatible with downstream label-key syntax (portable ASCII letters/digits/underscore/hyphen is the initial config validation constraint). Reject dictionary entries colliding with system-owned labels `visibility` or `user_visibility`; these protocol identifiers and system visibility enums are not business dictionary entries.

A missing/malformed file fails new-server startup with an actionable error. An explicitly empty `[labels]` dictionary allows only `{}`, never arbitrary data. No environment overlays may override this policy. Dictionary revisions are deployment configuration, not request-selected or tenant-selected policy. Initially all runtime profiles use identical content; product-specific policy separation is deferred without changing wire/storage types.

### D3. Normalization and write semantics

For a supplied object: validate every key against the policy, including keys whose value is null/[]; require each non-null value to be an array containing only allowed strings; deduplicate; remove null/[] entries; enforce configured multiplicity. Invalid input rejects the entire operation before mutation/catalog notification. Reuse existing request-size bounds; do not introduce unbounded policy scans or per-key DB lookups.

| Operation | Omitted field | Explicit `{}` | Non-null object |
| --- | --- | --- | --- |
| Agent registration | empty | empty | normalize and store |
| Agent explicit management update with source/visibility | preserve | replace with empty | replace entire collection |
| Agent label-only management update | no operation requested if no other field exists | valid clear | valid replacement |
| Group creation | empty | empty | normalize and store |
| Group PATCH | preserve | clear | replace entire collection |
| Agent URL/manual/background refresh | preserve | no label parameter | no label parameter |
| Internal template refresh, unrelated Group mutation, catalog retry | preserve | no implicit clearing | use persisted state |

For Group PATCH, omitted keys inside a supplied object are removed, not preserved. For Agent updates, old callers changing only source or visibility preserve current labels. On every CAS attempt, omission uses the latest record, never an earlier snapshot. Explicit objects still replace the entire collection. Auth, Provider scoping, owner/manager checks and existing error envelopes remain authoritative. Return field-specific invalid-request details (`catalog_labels`, `catalog_labels.<key>`, or list index) for null object, unknown key/value, wrong type and disallowed multiplicity without introducing hardcoded business errors.

Provider authentication/name resolution is read-only. Public and named registration,
updates and refreshes validate both admission and persistence projection before
slug reservation. Validate owner/ref/target and local prerequisites before pinning;
successful get/find views validate their target first and empty finds do not pin.
A valid update reserves once before its first CAS write, retaining rename fencing.
Later DB/CAS failures do not imply rollback of the separate reservation.
Legacy label type errors in Group create/PATCH, Provider Card configure/register
and named registration are mapped at their parsing boundaries to field-specific
structured 400 (`INVALID_TYPE`), without globally changing unrelated rejections.
The Gateway must expose the complete generated register body and label property
plus parent required semantics in every response expansion; input labels stay
optional. Compare generated contracts semantically and retain unrelated historical
schema content during selective publication.

### D4. Persistence and Group eligibility

Add `catalog_labels` independently to `bcs_bots` and `bcs_group_agent_cards`; MySQL/OceanBase JSON, SQLite TEXT containing JSON. Use nullable/default NULL additive columns so old rows/inserts remain valid; new writes serialize normalized empty state as `{}`. Read SQL NULL as empty; malformed non-null stored JSON is a storage error, not silent empty fallback. Do not duplicate labels in `bot_info`, `bcs_groups`, raw Card documents or A2A task/run/context tables.

Ship one new uniquely numbered migration per existing dialect chain containing both column additions. Determine numbers at implementation time after re-fetching; never edit migration 039/041 or the committed baseline. Confirm real MySQL/OceanBase JSON driver decoding and SQLite upgrades. Old migration runners may reject unknown newer history; verify that explicitly rather than assuming additive columns alone prove rollback safety.

Eligible Group Cards remain normal manager-worker/state-machine Groups, including private Groups that may later become public. Do not expand Chat/DM Card support: omitted/empty labels remain compatible there; a non-empty label write fails explicitly as unsupported, not silently discarded. A legacy eligible Group lacking a Card row returns empty on read; an explicit label write creates a valid template and labels atomically. Ordinary reads do not backfill. Existing template upserts must preserve labels unless a replace was explicitly requested; labels survive name/context changes, eventful writes and idempotent create recovery. Creation omission initializes a new resource only and must not clear labels when an existing Group is returned.

Legacy normal-Group creation, including explicit IDs, is create-only at the Core/
repo boundary. SQL unique constraints and the memory write lock reject collisions
atomically; do not authorize a replacement using only the incoming caller/driver.
Existing Group changes use authorized PATCH. DM reuse and trusted internal upsert
remain distinct contracts. Versioned mutations return the already fenced committed
candidate without a fallible post-COMMIT read before catalog notification. This
removes a lost-notification error window, not process-crash durability; no outbox
or external delivery guarantee is added.

Agent creation writes identity/Card/labels in the existing transaction. A combined Card/visibility/label update commits atomically and uses existing revision/identity fences. Refresh updates only the fields it owns, must not write a stale copied label map, and honors superseded-refresh fencing. Label-only Agent updates participate in the management revision fence so older refreshes cannot restore old metadata. Group writes use the existing Group transaction and authorization path, not a second post-commit write. Cache invalidation and catalog notification follow successful commit only.

### D5. Complete labels to catalog

Build the outgoing map from persisted custom labels plus current system labels; system values cannot be shadowed. Agent publication has the two current system dimensions; Group publication has its current visibility dimension (do not invent Group user_visibility). Keep labels outside `card`. Always send the full current collection, including all system dimensions when custom labels are empty. Do not send `[]` tombstones or query the catalog for old keys.

Continue to queue identities and re-read latest state on each attempt. Label-only Group/Agent changes notify publication. Preserve current Group publication eligibility and delete/unpublish behavior. Downstream full-replacement support is an external acceptance dependency, not a requested implementation: payload assertions can pass while downstream stale-key removal remains NOT_VERIFIED.

### D6. Public APIs and cross-repository source compatibility

Expose typed register/update/view/record fields and a structured update command rather than growing transport-shaped tuples or leaking Proto types. Preserve old service entry points where needed for downstream compilation, delegating to the explicitly defined legacy omission semantics; never provide a default implementation that accepts non-empty labels and drops them. New public fields can break Rust struct literals: enumerate downstream consumers and record that risk. Independent old hosts retain their old gitlink; a host must update its source and gitlink together in phase 2.

Agent canonical endpoints remain `/api/v1/collaboration/a2a/agents` and `/{bot_id}/agent-card` (GET/PUT), collection Find and `:refresh`, with existing envelopes/auth. Named/provider-specific Agent registration paths must carry the same field or explicitly route to the shared command, not bypass policy. Group endpoints remain `/openapi/v1/collaboration/groups` (POST) and `/{group_id}` (PATCH/GET); the browser create alias reuses the same validation. No independent full-Group-Card upload endpoint is added. Successful management views return normalized `catalog_labels: {}` rather than null/omitting the field.

## Risks / Trade-offs

- [Legacy Agent omission preserves populated labels] → test canonical and legacy entries, visibility/source updates and concurrent label-only writes. Old server binaries and publication still require separate compatibility validation.
- [New field sent to old REST DTO] → old closed DTO rejects; keep callers label-disabled until every backend replica is upgraded. An ordinary successful request is not a capability probe.
- [New policy configuration breaks startup] → distribute the standalone file first and test startup with old/new packages; no new inline config section is sent to old closed config schemas.
- [Cross-product dictionary drift] → record policy checksum per application rollout and compare intended content; no shared-file coupling is required.
- [Dictionary shrink invalidates retained data] → no automatic deletion or read-time filtering. Existing labels read/publish unchanged, new replacements are strictly validated; explicitly plan migration before shrinking the dictionary.
- [New optional columns are not enough for old-binary rollback] → test schema-history handling, SQL queries/upserts and generated-client decoders against expanded schema.
- [Unrelated Group writes erase labels] → preserve/replace is explicit and covered through memory, persistent and eventful paths.

## Migration Plan

1. Complete public code/contracts/config/migrations and focused suites in the Avernet worktree; keep downstream-host integration and deployment unchecked.
2. Expand each target database using the owning new migrations, before any new binary selects columns. Exercise existing binary on expanded schema and runner history. Never issue DDL from delivery adapters or request handlers.
3. Distribute the independent policy file to every instance/config bundle. Verify hash and startup validation; loading policy does not enable label-writing clients.
4. Roll new Avernet runtimes across all replicas with clients still label-disabled. Verify old payloads, old-row `{}` readback, Group omission preservation, and auth/envelope compatibility.
5. Run create/read/update/clear/refresh/concurrent-update/publication payload smoke tests. Wait for complete backend rollout before enabling new REST writers. For any strict old response decoder, upgrade its read compatibility before enabling the new server.
6. Publish/merge the tested public revision. Then downstream hosts adapt source, pin that exact available revision, run host integration suites and perform their own migration/config/server rollout. Public library publication alone does not upgrade embedded runtimes or their databases.
7. The downstream typed-client release is last. Record actual DB migration versions, runtime revisions, dictionary checksum, client versions and verification gaps. Full-label downstream application is verified separately by its owner.

Rollback: stop new label writes/roll back clients first, stop incompatible legacy writers for label-bearing Agents, drain affected operations, then restore a previously verified binary/config bundle. Keep both new columns, values and migration records; no destructive down migration. Old publication may omit custom labels and old writers may lose them: do not claim label-preserving downgrade without explicit tests. Reconcile by republishing persisted current state after re-upgrade. Configuration shrink and data cleanup require separate approved work.

## Open Questions

No unresolved user-facing field semantics. Whether product deployments intentionally use different dictionaries is deferred; initial policy is identical and files remain independent. Future release SHAs, actual migration numbers and downstream full-replacement acceptance are implementation/release evidence to collect, not values to invent in this proposal.
