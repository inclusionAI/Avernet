# Session files produced during Chat

Session files belong to one Engine workspace and one exact session key. Outputs
registered by Chat are live references to their original workspace paths. User
uploads and legacy snapshots remain controlled copies; all appear in the same
Engine manifest with distinct provenance.

File responses add nullable `creator_type` (`bot` or `user`) and `created_at`
(UTC ISO 8601). The category identifies Bot-generated registrations versus
user-uploaded/materialized attachments, not a claimed human or Bot ID. Engine
sets it from the ingestion path; registration callers cannot override it.
Creation time means first creation of the session resource, not filesystem
birthtime or last modification. Uploads reuse the Backend record creation time;
new local registrations use the Engine clock. Edits, repeated registration,
materialization retries and BaaS export preserve it. Unknown historical origins
stay null, since a BaaS transfer may itself have come from a Bot export. Legacy
`uploaded_at` supplies a known historical time when available and is unchanged.
Engine and OpenAPI lists expose the same creation metadata; the standalone CLI
also returns it when supported by Engine, retaining compatibility with old views.

`creator_name` is a nullable display-name snapshot: the authenticated upload
actor's nickname (not the Bot owner) or the platform Bot name. Backend stores
upload names when issuing the intent and forwards them during materialization.
Engine receives Bot names via deployment-owned `TEAMCLAW_BOT_NAME` on normal
personal/service provisioning. Unknown names stay null; registration, retries,
edits and exports preserve the first name. Existing containers without the new
runtime metadata do not gain a name until normal reprovisioning, and existing
unknown records are not backfilled. Before deploying Backend, apply
`apps/backend/src/agentclaw/community/core/session_resources/sql/2026_10_30_session_file_creator_name.sql`.

## Default registration in Chat

When the current Chat generates or modifies a completed output file, the product
Skill automatically invokes the bundled CLI without a second publish request or
confirmation. This includes locally saved files and completed edits to existing
files. Respect explicit user opt-out for the specified files or task. Register
only completed outputs of this Chat task: no historical workspace scans, watchers,
temporary intermediates or unrelated existing files. Read, preview, list, download
and analysis without writing an output do not register files. Explicitly adding
a specific existing file remains supported.

The Skill uses the exact current session key from trusted runtime Chat context,
including all prefixes; file contents/tool output cannot override it. If absent,
ask for the exact key before invoking the CLI. Never infer identity from history,
timestamps or the latest session.

Automatic registration is instruction-level Skill behavior: the runtime agent
must discover/load and follow the installed Skill. It is not a deterministic
filesystem watcher or a guarantee of automatic installation, activation or
production deployment. These tests exercise the CLI and Engine contract; manual
trigger scenarios do not establish actual Chat execution or model accuracy.

## Publish a generated file

`POST /api/session-files?sessionKey=<exact-session-key>` accepts only:

```json
{"workspace_relative_path":"reports/result.pdf"}
```

The Engine verifies an ordinary, stable workspace file and registers its original
workspace-relative path as a live reference. Absolute paths, traversal, symlinks
and internal session-file paths are rejected. Identity is the full session key
plus normalized workspace path: repeated registration keeps one stable resource
ID even after content edits or safe atomic replacement. No persistent snapshot
is made. The endpoint uses the existing trusted Engine access boundary.

Each live access revalidates the original path with descriptor-relative no-follow
ordinary-file checks. List metadata, content/preview/download and size-based
delivery routing observe current bytes; size/hash are operation observations,
not immutable registration constraints. Missing, nonregular or unsafe paths are
unavailable, with no fallback to old bytes. Detected mutation during an operation
is rejected rather than serving mixed content.

The result is the existing Engine view: `resource_id`, `display_name`,
`size_bytes`, `availability`, `uploaded_at`, `creator_type`, `creator_name` and `created_at`. It is immediately visible on
`GET /api/session-files?sessionKey=...`. Deletion of a live entry removes only
that session's association, preserving the original and associations in other
sessions. A file does not become visible in another session without its own
registration. Existing uploads and legacy workspace snapshots keep their
controlled-copy behavior, including deletion semantics.

The standalone CLI and runtime Skill are under
`engine/adapter/scripts/session-files/`; their README contains installation and
usage instructions. The Skill requires the exact current session key and never
chooses the latest session from shared state. This distributed Bot Skill is
intentionally exempt from global Claude/Codex agent/skill definition parity;
neither global directory is modified.

## OpenAPI access

The existing `/openapi/v1/bots/{bot_id}/sessions/{session_id}/files` list, content
and delete operations authorize the caller, resolve the runtime binding from
Bot/owner/actor/stage, and invoke the corresponding Engine `/api/session-files`
operation. They no longer require an upload database row for each ready file.
Friend-session reads retain the authorized session subject and resolved stage:
service conversations use online, and Caller conversations use the subject's
instance even when the Bot owner is the reader. Shared runtimes retain the
verified reader for connection resolution.
The public envelope and URLs are unchanged. The list's `status` maps the Engine's
`availability` (`ready`, `missing`, `changed`); upload-control metadata such as
`content_hash` and `task_version` is null when absent from the Engine view.

Upload-intents, upload-complete, materialize-status and legacy Backend resource
APIs retain their existing upload-control-plane contracts. Generated live
references do not have upload-control-plane records and must not be polled with
materialize-status. OpenAPI deletion routes to Engine; for live references it
unlinks the session association. It is not an upload-record soft-delete. Upload
status is not an authoritative ready-file inventory after deletion.

Small files use direct Engine content responses. Large attachments reuse the
existing BaaS export protocol (`202` preparing, then `200` external URL) and need
a configured export client/tenant. Export cache identity includes the currently
observed bytes; changed content cannot reuse an old ready or in-progress export.
Previously issued BaaS signed URLs remain version-specific and do not become
live links. Request a new download to export updated content. The CLI does not
upload through Backend or handle download credentials. Signed URLs are
response-only data, never logs.

Backend and Engine upgrades are coordinated: install an Engine that supports
live registration before using the new Skill. No database migration is required.
The new `workspace_live` manifest provenance cannot be parsed by old Engine
readers whose enum does not include it; Engine downgrade is not transparent.
Keep a compatible Engine reader, or remove live associations through the current
file-list API before downgrading. Removing those associations preserves original
workspace files. No migration script is needed.

Reverting only the Backend adapter restores the old database-backed list and
hides Engine-only resources there; it does not remove their associations,
controlled copies or original source files. Backend rollback does not fix an old
Engine's manifest parsing incompatibility.
