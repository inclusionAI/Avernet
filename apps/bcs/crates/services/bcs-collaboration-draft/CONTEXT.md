# Collaboration Draft context

## Provides

Immutable collaboration authoring revisions, Bot management ownership, CAS,
idempotent creation, and revocable public fixed-revision reads.
The separate `bcs-app-collaboration-draft` orchestrates exact stored-source
validation. This core does not create groups, deliver messages or execute runs.

## Consumes

`CollaborationDraftRepoPort` injected by the bootstrap. Draft IDs are unique
within the configured database, independent of chats.

## Allowed dependencies

Service API, structured serialization, UUID, clock and source hashing libraries.
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
`DRAFT_SERVICE_UNAVAILABLE` after their normal request checks.
Disabling does not delete Drafts or their availability. The checked-in
`configs/bcs-config-local.toml` explicitly enables Drafts for local previews;
Singlebox inherits that setting without an additional override.

Each fixed revision is publicly readable by ID and revision, with no dedicated
credential or request header. Revocation makes that version unavailable to all
readers; deletion removes its source. Existing Bot management authentication
and Human group-creation authentication are unchanged.

## Schema and compatibility

SQLite 36 / MySQL 35 already store revisions in `payload_json`. New records no
longer contain `read_token`; retained JSON records with that former field are
readable because unknown stored fields are ignored. Historical migration names,
checksums and table columns are unchanged; no schema migration is required.
The persisted `revoked` column continues to control version availability.

New schema 2 references contain only `schemaVersion`, `draftId`, and `revision`.
Panel 1.7.1 and the frontend host normalize historical cards to those fields.
The native HTTP `/reference` response additionally retains deprecated `readToken`
as the constant base64url encoding of 32 zero bytes. Deployed CLI versions require
that field and validate its encoding before emitting a card. This placeholder
exists only in the HTTP adapter: it is neither stored nor authenticated. Old CLI
cards retain it; new CLI cards discard it. All reads remain credential-free.
Deploy the BCS API and Panel/host update together; the deployed CLI can remain
unchanged and be upgraded independently. No replacement auth header, query
credential or signing key is introduced.

## Persistence and access cost

The SQL implementation belongs to `bcs-collaboration-store::draft` and uses the
existing DB plugin. Fresh creation: one point query and one two-write transaction.
Idempotent replay: three point queries. Update: one owner point query and one
two-write CAS transaction. Shared read: one joined primary-key query. Validation
adds no DB work beyond its public read. Delete removes only the selected
draft's revisions and leaves an ID/owner tombstone. No network or YAML
validation occurs inside a transaction; there are no background scans or caches.

## Tests

`cargo test -p bcs-collaboration-draft` exercises SQLite transactions, incident
bytes, CAS, owner checks, public reads, legacy JSON, revocation, deletion and restart.
HTTP/CLI/Panel conformance tests cover delivery boundaries independently.
