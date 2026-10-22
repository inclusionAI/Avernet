# Collaboration Draft context

## Provides

Immutable collaboration authoring revisions, Bot management ownership, CAS,
idempotent creation, and revocable fixed-revision read capabilities.
The separate `bcs-app-collaboration-draft` orchestrates exact stored-source
validation. This core does not create groups, deliver messages or execute runs.

## Consumes

`CollaborationDraftRepoPort` injected by the bootstrap. Draft IDs are unique
within the configured database, independent of chats.

## Allowed dependencies

Service API, structured serialization, UUID, clock and cryptographic libraries.
Core uses only the repo port.

## Forbidden dependencies

HTTP adapters, database plugins, configuration/environment reads, Backend,
Gateway, Engine Runtime and frontend.

## Configuration

Draft is disabled by default. SQLite and MySQL both need only this setting:

```toml
[collaboration_drafts]
enabled = true
```

Omitting the section or `enabled`, or setting it to `false`, skips Draft service
initialization. Disabled Draft endpoints report HTTP 503
`DRAFT_SERVICE_UNAVAILABLE` after their normal request/authentication checks.
Disabling does not delete Drafts or their read grants. The checked-in
`configs/bcs-config-local.toml` explicitly enables Drafts for local previews;
Singlebox inherits that setting without an additional override.

Each revision has an independently generated 32-byte random read token stored
with its source. Replicas and restarts use the same database; no key files,
deployment ID or extra secrets are required. The read token is still scoped to
one fixed revision and remains revocable. Owner Bot authentication is unchanged.

## Schema and compatibility

SQLite 36 / MySQL 35 create Draft tables without a deployment namespace. The
persisted revision payload contains `read_token`; it has no signing key ID or
separate signing nonce. No additional migration version is required.

Before initial enablement, the signing-key schema was replaced directly in
these migrations by explicit project-owner instruction. There is no automatic
upgrade for databases that already applied the earlier Draft schema. Retained
local test databases need separate schema/data adjustment before reuse; this
change neither deletes their data nor rewrites migration records or bypasses
checksum checks. Back up any such database before adjusting it.

Remove `deployment_id`, `active_key_id` and `key_files` from configuration: these
removed fields are rejected. Old signing files are no longer read or generated.
Draft persistence and backup use only the database. Old signed cards are not
supported.

## Persistence and access cost

The SQL implementation belongs to `bcs-collaboration-store::draft` and uses the
existing DB plugin. Fresh creation: one point query and one two-write transaction.
Idempotent replay: three point queries. Update: one owner point query and one
two-write CAS transaction. Shared read: one joined primary-key query. Validation
adds no DB work beyond its authorized read. Delete removes only the selected
draft's revisions and leaves an ID/owner tombstone. No network or YAML
validation occurs inside a transaction; there are no background scans or caches.

## Tests

`cargo test -p bcs-collaboration-draft` exercises SQLite transactions, incident
bytes, CAS, owner checks, grant scope, revocation, deletion and restart.
HTTP/CLI/Panel conformance tests cover delivery boundaries independently.
