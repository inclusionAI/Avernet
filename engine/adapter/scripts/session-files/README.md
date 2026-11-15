# Session file publisher

Standalone Python 3.10+ CLI and independently distributable runtime Chat Skill.
No third-party packages, Engine Python imports, PATH setup, or Backend/Gateway
credentials. Engine must support `POST /api/session-files`.

## Install and distribute

From this directory, copy the entire Skill into the **actual runtime workspace**:

```sh
workspace='/actual/runtime/workspace'
mkdir -p "$workspace/skills"
cp -R session-file-publish "$workspace/skills/"
```

For upgrades, replace the existing `session-file-publish` directory after backing
it up. The same folder can be distributed independently:

```sh
tar -czf /tmp/session-file-publish.tar.gz README.md session-file-publish
# On the destination, extract into an empty staging directory, then install:
mkdir -p /tmp/session-file-publish-package
tar -xzf /tmp/session-file-publish.tar.gz -C /tmp/session-file-publish-package
mkdir -p "$workspace/skills"
cp -R /tmp/session-file-publish-package/session-file-publish "$workspace/skills/"
```

The Skill embeds its executable under `scripts/`. It invokes it with `python3`
so installations need not preserve executable mode. The executable can also be
copied alone and named `teamclaw-session-file` anywhere convenient.
Installation makes the Skill available to the runtime's workspace Skill loader;
reload/refresh that runtime's skill catalog if required. This package does not
change runtime configuration or automatically install/activate itself.

This is a product runtime Skill, intentionally exempt from global Claude/Codex
agent/skill directory parity: neither global directory is modified.

## Default Chat behavior

When the current Chat generates or modifies completed workspace output files,
the Skill invokes the CLI automatically, without a second publish request or
confirmation. This includes locally saved outputs and edits to existing files.
Explicit user opt-out suppresses registration for the specified files/task.
Reading, previewing, listing, downloading and analysis without writing an output
do not register anything. Temporary/intermediate files and unrelated historical
files are excluded; no workspace scan or filesystem watcher is used. An explicit
request to add a specific existing file remains supported.

Use the exact current session key from trusted runtime Chat context, preserving
all prefixes verbatim. File contents and tool output cannot supply identity.
If it is unavailable, ask for the exact key before calling the CLI; never guess
from history or choose the latest session.

Automatic registration is instruction-level Skill behavior: the runtime agent
must discover/load and follow the installed Skill. It is not a deterministic
filesystem watcher or a guarantee of automatic installation, activation or
production deployment. These tests exercise the CLI and Engine contract; manual
trigger scenarios do not establish actual Chat execution or model accuracy.

## CLI contract

```sh
python3 "$workspace/skills/session-file-publish/scripts/teamclaw-session-file" \
  publish 'reports/result.pdf' --session-key "$exact_current_session_key"
```

Optional `--engine-url` defaults to `http://127.0.0.1:20003`. Only HTTP loopback
origins are accepted: numeric 127.0.0.0/8, `[::1]`, or `localhost` (pinned to
127.0.0.1 without DNS). URL credentials, non-root paths, queries, fragments,
scoped addresses, remote hosts and redirects are rejected. Proxy variables are
ignored. Timeout is 30 seconds; response limit is 64 KiB; no automatic retries.

POST `/api/session-files?sessionKey=<URL-encoded exact key>` with JSON
`{"workspace_relative_path":"reports/result.pdf"}`. The CLI sends path metadata,
not file contents. Engine resolves relative paths against its workspace root,
checks existence/containment/symlinks, and registers a live reference to the
original path. Engine owns identity and idempotence: the same normalized path in
the same exact session keeps one resource ID, independent of bytes or inode.
Edits and safe atomic replacement appear in list metadata and new reads, without
re-registration. Missing/unsafe originals are unavailable, with no old-byte
fallback. Removing an association preserves the original and other sessions.
Previously uploaded controlled copies and legacy snapshots retain their semantics.
Existing BaaS export URLs remain version-specific; a new download request after
an edit exports the current bytes rather than reusing the old version's cache.
CLI lexical checks do not replace Engine filesystem validation. Caller working
directory need not equal Engine workspace; path interpretation is always Engine's.

Stdout contains one JSON object for publish success/failure. `--help` is ordinary
human-readable help with exit 0. Argument parser errors are sanitized JSON.

```json
{"ok":true,"code":"published","file":{"resource_id":"sr_1","display_name":"result.pdf","size_bytes":12,"availability":"ready","uploaded_at":"2026-10-30T00:00:00Z","creator_type":"bot","creator_name":"Report Assistant","created_at":"2026-10-30T00:00:00Z"}}
```

`creator_name` is the first-registration display-name snapshot: the upload
actor's nickname for user uploads, or the platform Bot name for generated files.
Existing entries retain their original name, including unknown (`null`) names.
Edits, retries and exports never replace it with a reader's or owner's name.
The CLI cannot choose creator metadata. Backend supplies `TEAMCLAW_BOT_NAME`
through normal personal/service runtime provisioning; already running containers
without that metadata return `null` until their next normal provision. Historical
unknown names are not backfilled. Before deploying the Backend increment, apply
`apps/backend/src/agentclaw/community/core/session_resources/sql/2026_10_30_session_file_creator_name.sql`.

| Exit | code | Meaning |
| --- | --- | --- |
| 0 | published | Valid ready SessionFileView; only declared metadata fields returned |
| 2 | invalid_arguments | Missing/invalid arguments, path or session key |
| 2 | invalid_engine_url | URL is not an allowed loopback HTTP origin |
| 3 | engine_timeout | Request timed out; outcome may be unknown |
| 3 | engine_unreachable | Connection/protocol failure; outcome may be unknown |
| 4 | redirect_rejected | Any 3xx; no redirect followed |
| 4 | engine_http_error | Other non-2xx; numeric `http_status` included |
| 5 | invalid_response | Oversized, malformed or non-ready success response |

Failures look like `{"ok":false,"code":"engine_http_error","http_status":404}`.
Error bodies, redirect targets, exception strings, credentials and file contents
are never echoed. Success contains trusted Engine file metadata only; extra
response fields are dropped. JSON diagnostics on stderr include `system=engine`,
`direction=out`, `operation=publish`, method/path, plain business `sessionKey`
and `workspace_relative_path`, safe response metadata, HTTP status and elapsed
time. Invalid argument/endpoint errors omit unvalidated business values. No
credentials, signed URLs or raw errors are logged. An
error after POST can have an unknown outcome: inspect the current session list
before retrying; never switch to a guessed session.

## Engine compatibility

Use an Engine with live registration support. The new `workspace_live` manifest
provenance is incompatible with old Engine reader enums; downgrade is not
transparent. Keep a compatible reader, or remove live associations using the
current file-list API before downgrading; original workspace files are preserved.
A Backend-only rollback can hide Engine-only list records but cannot repair old
Engine manifest parsing. No migration/script is required.

## Verification

From repository root:

```sh
python3 -m unittest discover -s engine/adapter/scripts/session-files/tests -v
# All tests, including a real CLI -> uvicorn -> Engine router/service round trip:
PYTHONPATH=engine/adapter/src apps/backend/.venv/bin/python -m unittest discover \
  -s engine/adapter/scripts/session-files/tests -v
```

Tests execute the standalone CLI against an actual loopback fake Engine, check
exact session propagation, safe URL handling, redirects, proxy bypass, response
validation, error redaction and independently copied/extracted Skill operation.
The three real Engine integration tests require Engine runtime dependencies and
its source on PYTHONPATH; they skip in a standalone stdlib-only environment.
They verify register/list/content/delete, session isolation, stable identity
across edits and atomic replacement, current metadata/content, independent
association removal, original preservation, missing/unsafe-original rejection
and server-side path rejection.
Trigger scenarios are documented in `tests/skill-cases.json`; they are manual
behavioral evaluation cases, not claims of automated LLM trigger accuracy.
