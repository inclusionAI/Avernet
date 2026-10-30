# Session File registration

`POST /api/session-files?sessionKey=<exact-session-key>` accepts exactly
`{"workspace_relative_path": "reports/result.txt"}` and returns the existing
SessionFileView: `resource_id`, `display_name`, `size_bytes`,
`availability`, `uploaded_at`, `creator_type`, `creator_name`, `created_at`. It uses the existing trusted Engine/proxypass
boundary, like list/content/delete; it does not introduce another authentication
transport. Never expose that trusted listener as a public unauthenticated API.

`creator_name` preserves the initial user nickname or platform Bot name. Missing
historical names stay null. Bot names come from deployment-owned
`TEAMCLAW_BOT_NAME`, read through the default runtime identity provider; the
registration HTTP body and CLI cannot choose them. User names arrive with the
trusted Backend materialization request. Names are display metadata, not an
additional authorization mechanism.

Creation metadata is server-owned: Bot registrations use `creator_type=bot`,
user uploads/attachments use `user`. `created_at` is the first resource creation
time in UTC, preserved through edits, repeated registration, retry and export.
It does not mean filesystem birthtime. Historical metadata is nullable; never
infer human origin from BaaS transfer provenance. Keep `uploaded_at` compatible.

`SessionFileService.register_file` associates a completed ordinary workspace file
with the exact session using `source_kind=workspace_live`. It persists metadata,
not a registration byte snapshot. Identity uses the full session key and normalized
workspace-relative path, independent of bytes/inode: edits, atomic replacements
between operations and repeated registration keep one ID/list entry. Another
session gets an independent association. Deleting a live entry removes only that
association, preserving the source and every other session's entry.

Every live list/content/export observation reads the current ordinary file using
POSIX descriptor-relative no-follow opens for every path component. Absolute,
traversing/internal paths, ancestor/final symlinks and nonregular sources are
rejected. Missing or unsafe sources remain unavailable without old-byte fallback.
Before/after descriptor and original-path observations reject detected concurrent
mutation/replacement. Current size/hash observations are operation versions, not
immutable registration constraints. Manifest writes retain thread/process locking;
failed persistence never returns successful registration.

Direct content uses an anonymous operation-local temporary stream: verified bytes
and Content-Length remain coherent through HTTP consumption even if the source is
subsequently replaced. The transport consumes/closes that stream and never reopens
the descriptive source Path. This temporary delivery representation is not a
persistent registration copy. Live binary responses use `Cache-Control: no-store`
because the stable content URL can return edited bytes. The core rejects direct
content over 30 MiB from descriptor metadata before copying; growth during copying
fails safely. Attachments inspect current size: small files stream without BaaS
identity, large files use export. Changes between size selection and delivery
observation fail safely.

A new live entry has empty transfer/task IDs. Large attachments require a configured
export capability and tenant. The exporter securely copies an observed version into
an operation-local private transfer file, uploads/completes the actual transfer,
and removes the temporary file. Promotion retains `workspace_live` provenance and
original relative path; the real transfer is paired with its uploaded hash/size.
Repeat registration cannot relabel an old transfer with new bytes. Cache identity
includes observed content hash; revalidation rejects stale ready/pending results
with `resource_changed`. New requests after edits export the new version. Already
issued signed links remain specific to the previously exported bytes. The deployment
owns the default tenant; community defaults fail closed for external export.

Existing `workspace_file` entries are legacy controlled snapshots. They keep their
original immutable-byte checks and snapshot-file deletion behavior. Uploaded
`baas_session_file` and `temporary_url` controlled copies retain existing behavior,
including promotion to `baas_session_file` on export. The legacy unsessioned
materialization-content endpoint explicitly refuses `workspace_live` entries;
only the session-scoped reader may consume live sources.

Downgrade requires removing live associations from the manifest before starting an
older Engine that cannot parse the new provenance. Originals remain intact, but an
older runtime cannot preserve live-reference semantics. Do not relabel live entries
as legacy snapshots: that would restore immutable checks and source-file deletion.
Existing upload/snapshot entries need no conversion.

Request/success/failure events retain existing loggers and historical access logs.
Content diagnostics add method, constant route, business `sessionKey`, resource ID,
disposition, status, size and duration when known. Direct success is logged after
stream completion; HTTP/stream failures record controlled status and exception type.
External preparing/ready responses also log safe outcomes. Only explicit safe
metadata is logged; headers, credentials, raw exceptions, signed URLs and file bytes
are never logged.
