# Collaboration Draft context

## Provides

Immutable collaboration authoring revisions, Bot management ownership, CAS,
idempotent creation, and revocable fixed-revision read capabilities.
The separate `bcs-app-collaboration-draft` orchestrates exact stored-source
validation. This core does not create groups, deliver messages or execute runs.

## Consumes

`CollaborationDraftRepoPort` and signing keys injected
by the bootstrap. Drafts are scoped by a trusted deployment, independent of chats.

## Allowed dependencies

Service API, structured serialization, UUID, clock and cryptographic libraries.
Core uses only the repo port.

## Forbidden dependencies

HTTP adapters, database plugins, configuration/environment reads, Backend,
Gateway, Engine Runtime and frontend.

## Configuration

Draft is always assembled by the durable BCS bootstrap. SQLite deployments
atomically initialize a private `*.draft-keys.json` file beside the database and
reuse it across restarts. Missing keys for existing Drafts, invalid files or
storage failures prevent startup; they never silently replace signing material.

Apply SQLite 36 / MySQL 35 before use. The optional `collaboration_drafts`
section overrides the default signing material, and is required for shared or
non-SQLite deployments. Provision a private file containing
at least 32 random bytes as hex, mode 0600, separately from source control.
Use the same deployment ID and key files across replicas/restarts/rollback:

```toml
[collaboration_drafts]
deployment_id = "your-stable-deployment"
active_key_id = "key-1"
[collaboration_drafts.key_files]
key-1 = "/protected/config/collaboration-draft-key.hex"
```

Keep old key IDs/files on rotation; `active_key_id` only selects the key for new
revisions. Removing an old key makes its references unavailable and is not a
routine rotation. Back up keys together with BCS data. Do not regenerate keys
on each deployment. Rollback that predates the Draft endpoint cannot serve
Draft cards; rollback between Draft-capable builds must retain migrations, data,
and the signing files/configuration. A restored database without its original
keys fails startup. Deploy BCS first, then CLI/Panel, then enable the new Skill.

The 2026-10-21 rebase preserved upstream `group_context_owners` migrations and,
with explicit user approval, renumbered this branch's Draft migrations from
SQLite 35 / MySQL 34. The retained local SQLite database was backed up with its
signing material before transactionally moving only its Draft history entry to
36 and applying upstream 35. Draft contents, revisions and revocations were
unchanged. No automatic history rewrite or checksum bypass is provided.

## Persistence and access cost

The SQL implementation belongs to `bcs-collaboration-store::draft` and uses the
existing DB plugin. Fresh creation: one point query and one two-write transaction.
Idempotent replay: three point queries. Update: one owner point query and one
two-write CAS transaction. Shared read: one joined primary-key query. Validation
adds no DB work beyond its authorized read. Delete removes only the selected
draft's revisions and leaves an ID/owner/deployment tombstone. No network or YAML
validation occurs inside a transaction; there are no background scans or caches.

## Tests

`cargo test -p bcs-collaboration-draft` exercises SQLite transactions, incident
bytes, CAS, owner checks, grant scope, revocation, deletion, restart and rotation.
HTTP/CLI/Panel conformance tests cover delivery boundaries independently.
