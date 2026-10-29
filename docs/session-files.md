# Session files produced during Chat

Session files are ready, controlled snapshots owned by one Engine workspace and
one exact session key. User uploads and files published by Chat tools appear in
the same Engine manifest.

## Publish a generated file

`POST /api/session-files?sessionKey=<exact-session-key>` accepts only:

```json
{"workspace_relative_path":"reports/result.pdf"}
```

The Engine verifies an ordinary, stable workspace file, copies it into the
controlled session-file directory, and atomically registers its metadata.
Absolute paths, traversal, symlinks and internal session-file paths are rejected.
The original is retained. Repeating registration for the same session, source
path and content returns the same resource; a changed source produces a new
snapshot. The endpoint uses the existing trusted Engine access boundary.

The result is the existing Engine view: `resource_id`, `display_name`,
`size_bytes`, `availability` and `uploaded_at`. It is immediately visible on
`GET /api/session-files?sessionKey=...`. Deletion removes the registered snapshot,
not the source. A file does not become visible in another session.

The standalone CLI and runtime Skill are under
`engine/adapter/scripts/session-files/`; their README contains installation and
usage instructions. The Skill requires the exact current session key and never
chooses the latest session from shared state.

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
APIs retain their existing upload-control-plane contracts. Generated local
snapshots do not have upload-control-plane records and must not be polled with
materialize-status. OpenAPI deletion now removes the Engine snapshot; it is not
an upload-record soft-delete. Upload status is not an authoritative ready-file
inventory after deletion.

Small files use direct Engine content responses. Large attachments reuse the
existing BaaS export protocol (`202` preparing, then `200` external URL) and need
a configured export client/tenant. The CLI does not upload through Backend or
handle download credentials. Signed URLs are response-only data, never logs.

Backend and Engine upgrades are coordinated: install an Engine with registration
support before using the new Skill. No database migration is required. Reverting
the Backend adapter restores the old database-backed list and hides Engine-only
resources there; it does not remove their snapshots or original source files.
