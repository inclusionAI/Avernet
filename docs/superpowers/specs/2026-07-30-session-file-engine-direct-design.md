# Session File Engine Direct Design

## Interaction

The upload drawer keeps using Backend for upload intent, upload completion, and
polling until a resource becomes `ready`. On `ready`, it switches to the Bot
proxypass Engine connection:

1. The file panel calls `GET /api/session-files` for the active session.
2. Preview and download call the Engine content route with the requested
   disposition.
3. The chat composer submits only `resourceId` and `insertId`; the existing
   Engine reference service resolves the Bot absolute path from its manifest.
4. A `missing` or `changed` entry is visibly unavailable and offers re-upload.
   An explicit delete removes the entry from the Engine list immediately.

Backend's `pending` response is merged only with in-flight local uploads after
page reload. It must not be used to reconstruct ready files.

## Compatibility

Existing Backend ready-file APIs stay operational for one release cycle for
older clients and are marked deprecated in the HTTP contract. The migration
does not alter `ac_session_resource` retention or the BaaS source lifecycle.
