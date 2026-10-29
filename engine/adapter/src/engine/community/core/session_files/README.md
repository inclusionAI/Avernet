# Session File registration

`POST /api/session-files?sessionKey=<exact-session-key>` accepts exactly
`{"workspace_relative_path": "reports/result.txt"}` and returns the existing
five-field SessionFileView: `resource_id`, `display_name`, `size_bytes`,
`availability`, `uploaded_at`. It uses the existing trusted Engine/proxypass
boundary, like list/content/delete; it does not introduce another authentication
transport. Never expose that trusted listener as a public unauthenticated API.

`SessionFileService.register_file` copies a completed ordinary workspace file
into the controlled `.teamclaw/session-files` layout before publishing a ready
manifest entry. Absolute/traversing/internal paths, symlinks at any component,
non-regular sources and source changes during copying are rejected. POSIX
descriptor-relative opens pin directory inodes; the manifest merge uses the
existing thread lock and a registration process lock. Same session, normalized
path and content reuse an ID; changed content or another session gets a new ID.
Failed writes do not register a ready file. Deleting a resource deletes only its
snapshot, retaining the original workspace source.

Local entries use `source_kind=workspace_file`, empty `transfer_id` and empty
`task_id`: registration never pretends a BaaS transfer occurred. Small attachments
stream directly without a BaaS tenant. Attachments over 30 MiB require a configured
export capability and tenant; the existing exporter uploads the snapshot, completes
the real transfer and then promotes the manifest to `baas_session_file`. The
deployment owns the default tenant; community defaults fail closed for external
export. Original source edits do not change already registered bytes.

Request/success/failure events use existing loggers, fixed event templates and
structured `extra` fields. They include the business `sessionKey`, relative path,
resource ID, status, byte count and duration where available. Only explicit safe
metadata is logged; headers, request dictionaries, credentials, exception text,
signed URLs and file contents are never logged.
