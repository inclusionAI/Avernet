# Workflow Pack scripts read API (v1)

Status: read-only prototype; both host integrations fail closed and cannot currently return script contents. This is a ClawWeb **Service API**: the authenticated editor calls the Workflow service. It does not define a Plugin API and it does not implement script editing, uploading, committing or deployment.

## Endpoint

`GET /api/workflows/{workflowId}/pack-scripts`

The HTTP host must inject a **server-verified** principal resolver. The public runner disables this route because its development identity can be derived from an unverified cookie. An internal host integration was prototyped separately but is not included in this repository change; it requires independent review and an authoritative Pack authorization provider before enabling reads. Host integration still requires independent environment verification; an injected resolver must never accept an unverified caller identity. No resolver or no verified principal means 401; arbitrary identity headers, unsigned tokens or `req.isAdmin` are not sufficient. The endpoint requires the principal to hold **edit** permission on the workflow; `can_view` or `can_execute` alone is insufficient. A missing or failed workflow permission provider fails closed. A separate authoritative Pack-wide access policy is also required after resolving the Pack: an individual workflow ACL or a single-entry manifest cannot prove exclusive ownership or authorize residual files. The public host currently lacks that Pack policy; its endpoint is disabled, and absence of a Pack policy returns 503 before reading Git. `packId` is obtained from the authorized workflow row (falling back to the row's workflow ID), not from client parameters. All responses use `Cache-Control: no-store`.

Successful response:

```json
{
  "workflowId": "example-flow",
  "packId": "example-pack",
  "baseRef": "origin/wf/example-flow",
  "baseCommit": "<full-git-commit-id>",
  "files": [
    { "path": "scripts/example.py", "content": "print(1)\n", "mode": "100644" }
  ]
}
```

`baseCommit` pins the entire result to one immutable Git commit; it is not a deploy tag and does not establish that the candidate is deployed. `baseRef` is the selected source ref, not a client-selected write target. The only returned paths are Pack-relative `scripts/**` UTF-8 text files with supported extensions `.py`, `.js`, `.ts`, `.sh`, `.bash`; empty text is valid. No script is executed. The server rejects symlinks, gitlinks, invalid path/UTF-8, NUL, unsupported script-directory files, more than 500 files, a file over 1 MiB or combined script contents over 8 MiB. A rejected snapshot is not a successful empty list. Manifest and workflow IDs and the referenced workflow file must match the authorized DB-derived identity. A Pack containing more than one workflow is rejected: edit access to one workflow does not authorize reading scripts shared with another. Repository-root `workflow.pack.yaml`, `<packId>/workflow.pack.yaml`, and historical `packs/<packId>/workflow.pack.yaml` layouts are supported. Multiple candidate manifests across these locations are rejected.

The snapshot resolver considers `origin/wf/<workflowId>` and `origin/saved/<workflowId>`: a sole or identical head is selected; for an ancestor pair the descendant is selected; a divergence is rejected. This conservative read policy is **not** the full ClawMind compatibility algorithm and must not be reused as a write or publish authorization rule. The host performs a configured, isolated temporary clone; request input cannot choose the repository, URL or credentials.

## Statuses

- `400` invalid workflow ID.
- `401` no verified identity.
- `403` missing edit permission or permission service.
- `404` workflow, candidate ref or Pack manifest not found.
- `409` conflicting refs, duplicate Pack manifests, or a Pack shared by multiple workflows (separate Pack-wide authorization is not yet available).
- `503` authorization dependency, Git, malformed Pack, unsupported file, limit or other unavailable snapshot. Response body never includes the raw Git error, remote URL, credentials or file contents.

Error responses retain the existing `error` string and add a stable `code`: `INVALID_WORKFLOW_ID` (400), `AUTHENTICATION_REQUIRED` (401), `FORBIDDEN` (403), `WORKFLOW_NOT_FOUND` or `SNAPSHOT_NOT_FOUND` (404), `SNAPSHOT_CONFLICT` (409), and `SERVICE_UNAVAILABLE` (503). Unknown or non-JSON 404 responses may come from an older host or intermediary and do not establish that a Git snapshot is missing. Clients must not display raw error bodies.

Successful responses contain script contents and must not be cached or logged. The caller should display an error when the API is unavailable, and must not treat errors as an empty snapshot. Existing workflow editor save/deploy APIs are unchanged. To use writes, a separate API and durable Git/DB reconciliation contract is required; callers must not send file modifications to the existing `/auto-heal/save` or assume `/auto-heal/deploy` publishes them.

## Tests / compatibility

`server/routes/__tests__/pack-scripts.test.ts` exercises authentication and authorization before DB/Git, request tampering, sanitized failures and no-store. `server/services/__tests__/pack-script-snapshot.test.ts` uses synthetic local Git commits to cover ref reconciliation, Pack identity and layout, modes, safe paths, UTF-8 and limits. `server/services/__tests__/pack-script-read-service.test.ts` covers clone/cleanup and a local bare Git integration. No production repository or data is used. `server/routes/__tests__/pack-scripts-integration.test.ts` additionally covers a real loopback HTTP server and local bare Git, including rejection of a shared Pack. Only the disabled public host wiring is included here; the separate internal host prototype is not part of this change. Host-specific identity integration and deployment still require separate verification; a mounted route is not proof of production readiness.
