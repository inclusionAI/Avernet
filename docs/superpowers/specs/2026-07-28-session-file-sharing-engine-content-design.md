# Session File Sharing Engine Content Design

## Scope

This change migrates TeamClaw session resources from the Bot Device File
Transfer API to the Session File Sharing API. It changes Backend and Engine
only. Frontend direct OSS upload and the BaaS Session File Sharing service are
separate owners and are not changed here.

## Contracts

- Backend creates upload credentials through `POST
  /api/v1/sessions/{tenant}/{session_id}/files/upload-url`, forwarding
  `filename`, `file_size`, and the authenticated operator. It preserves the
  BaaS `SINGLE` and `MULTIPART` response shapes for Frontend.
- `upload-complete` calls `POST .../upload-url/{transfer_id}/complete` and
  dispatches materialization only when BaaS returns `DONE`.
- New records have `transfer_api_version=session_v2`. The raw `session_id`
  is AES-GCM encrypted before persistence. It is absent from manifests, task
  payloads, and logs. Historical rows without a version are interpreted as
  `bot_device_v1` for materialization only.
- A materialization task contains only resource and task identity. Its worker
  reloads the resource, decrypts the session id only for the authenticated
  Backend-to-Engine request, and never logs it.
- For `session_v2`, Engine asks BaaS for `POST
  /api/v1/sessions/{tenant}/{session_id}/files/transfers/{transfer_id}/share-link`
  and consumes its short-lived URL solely to materialize the controlled Bot
  workspace file. The URL is neither persisted nor returned to Frontend.

## Content Delivery

After a resource becomes `ready`, Frontend reads the Engine user data plane
through the existing Bot proxypass:

- `GET /api/session-files?sessionKey=...`
- `GET /api/session-files/{resource_id}/content?sessionKey=...&disposition=inline|attachment`
- `DELETE /api/session-files/{resource_id}?sessionKey=...`

The proxypass is the authentication boundary: it validates the browser login
Cookie, proxypass connection token, and target Bot before forwarding to Engine.
The Engine Router does not require or inspect `x-iam-token`. It resolves only
manifest-controlled entries for the supplied session hash, rejects missing or
changed files and workspace escapes, and streams without buffering the file.
It accepts no caller path.

`/api/resource-materializations/{resource_id}/content` remains an internal
Backend-to-Engine route and is not a browser fallback. Neither data path falls
back to BaaS or OSS download URLs. Any new ingress that bypasses proxypass must
provide equivalent authentication before it can reach `/api/session-files`.

## Compatibility And Safety

- Existing ready `bot_device_v1` records use the same Engine content endpoint.
- Existing incomplete `bot_device_v1` records keep their legacy completion and
  materialization source path. New records only use `session_v2`.
- Custom path checks use resolved-path containment and are marked `COSEC`.
- Production profile bindings for the BaaS pull and device HTTP stream remain
  outside the public community package; generic contracts and fail-closed
  implementations are updated here.

## Release Prerequisites

- Apply
  `src/backend/src/agentclaw/community/core/session_resources/sql/2026_07_28_session_file_sharing.sql`
  before deploying Backend. The community production database deliberately has
  no automatic schema migration runner; local SQLite creates a fresh schema.
- For a greenfield MySQL-compatible deployment, create the complete resource
  table using
  `src/backend/src/agentclaw/community/core/session_resources/sql/ac_session_resource.sql`.
  Do not apply that create script to an existing deployment.
- The sensitive Engine profile must dispatch `session_v2` requests to
  `SessionFileBaasMaterializationClient`, providing the BaaS base URL, internal
  control-plane headers, and the exact allowlist of Session File OSS hosts. It
  must retain the existing legacy client for `bot_device_v1` rows.
- The sensitive Backend profile's `HttpDeviceAdapterTransport` must implement
  `stream()` with its existing Engine internal authentication, return an
  unbuffered response, and never expose request credentials as response
  headers. The public community and local transports intentionally fail closed
  or simulate this boundary.
- The Backend production `TokenVault` binding must use its deployment-managed
  encryption key. The encrypted `session_key_ciphertext` is the only persisted
  form of the TeamClaw session key for newly created resources.

## Verification

- Cover BaaS SINGLE/MULTIPART parsing, completion gating, encrypted persistence,
  queue redaction, stale callback and re-materialization CAS behavior.
- Cover Engine manifest content resolution, traversal/symlink rejection, missing
  files, response headers, streaming cleanup, and no BaaS access after ready.
- Cover Backend safe-header proxying, non-buffered chunks, upstream cleanup,
  `resource_materializing`, and cross-session denial.
- Preserve Claude Code reference rewriting tests for single, multiple, absent,
  tampered, and cross-session references.
