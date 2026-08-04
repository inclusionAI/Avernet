# Session File Engine Direct Data Plane

## Goal

After a session resource reaches `ready`, the Engine workspace manifest is the
only user-facing source for its list, content, deletion, and chat reference.
Backend remains the upload/materialization control plane and permanently keeps
the audit/task record in `ac_session_resource`.

## Wire Contract

The Frontend obtains its usual Bot proxypass connection and sends its IAM token
on these Engine APIs. It never sends an Engine filesystem path:

- `GET /api/session-files?sessionKey={sessionKey}` returns manifest entries for
  that session with `ready`, `missing`, or `changed` availability.
- `GET /api/session-files/{resourceId}/content?sessionKey={sessionKey}&disposition=inline|attachment`
  streams only a verified manifest file.
- `DELETE /api/session-files/{resourceId}?sessionKey={sessionKey}` removes only
  the local file and manifest entry.
- `GET /api/session-resources/pending?bot_id={botId}&session_key={sessionKey}`
  remains a Backend control-plane endpoint and returns only non-ready,
  non-deleted resources for page reload recovery.

The existing Backend list, reference, content, and delete routes remain
compatible during one release window, but new callers must not use them after
`ready`.

## Invariants

- The Engine requires `x-iam-token` and verifies it for the requested session
  and operation. Missing, denied, or failed verification is rejected.
  Proxypass remains the container boundary.
- Manifest access uses `resource_id`, never caller-supplied paths. Relative
  paths, symlinks, stat metadata, and SHA-256 are validated before streaming or
  supplying a Chat absolute path.
- Materialization records `observed_size`, `observed_mtime_ns`, and
  `observed_inode`. Listing avoids rehashing unchanged files. Content and Chat
  always rehash before exposing bytes or paths.
- Missing or modified files are visible as `missing` or `changed` and require
  upload again. No automatic re-materialization occurs.
- Explicit Engine deletion removes manifest then the local file and empty
  controlled directories. It does not delete the Backend audit record or BaaS
  source object.

## Acceptance

Local tests cover manifest atomic deletion, session isolation, safe streaming,
missing/changed states, deletion, and Chat reference validation. The final
gate deploys the Engine to Bot `20260728_lkn1m6x3`, then performs an actual
upload -> materialize -> ready -> Engine list/preview/download -> Chat reference
flow through frontend-equivalent proxypass calls. A dedicated harmless fixture
is used for changed/deleted behavior; the primary test resource and Backend
record are retained.
