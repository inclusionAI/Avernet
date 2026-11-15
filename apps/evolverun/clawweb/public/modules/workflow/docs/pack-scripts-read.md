# Workflow Pack scripts read API (v1)

Status: read-only implementation; the public host is disabled and the separate internal host requires its corresponding version-capable build and deployment verification. This is a ClawWeb **Service API**: the authenticated editor calls the Workflow service. It does not define a Plugin API and it does not implement script editing, uploading, committing or deployment.

## Endpoint

`GET /api/workflows/{workflowId}/pack-scripts?version=current|draft` (the `version` query parameter is optional)

The HTTP host must inject a **server-verified** principal resolver. The public runner disables this route because its development identity can be derived from an unverified cookie. An internal host integration was prototyped separately but is not included in this repository change; its identity and deployment must be verified separately before enabling reads. Host integration still requires independent environment verification; an injected resolver must never accept an unverified caller identity. No resolver or no verified principal means 401; arbitrary identity headers, unsigned tokens or `req.isAdmin` are not sufficient. The endpoint requires the principal to hold **edit** permission on the workflow; `can_view` or `can_execute` alone is insufficient. A missing or failed workflow permission provider fails closed. The product policy grants an editor access to all content in that workflow version, including shared and residual Pack scripts; it does not require a second Git credential or an independent Pack ACL. The scope is the workflow’s own `wf/<workflowId>` snapshot and active deployment tag, not an arbitrary branch, ref or repository. The editor may change the Pack ID within that version. This policy explicitly covers all contents of that version snapshot, even if the Git tree includes other or residual Pack contents; it depends on the integrity of the configured Git repository and its workflow refs. The public host is disabled. The router still requires the host to inject `authorizePackRead`: under this policy it checks the same actor’s workflow edit permission; missing callback returns 503 and denial returns 403. The internal host implementation and its production deployment must be validated separately. For unversioned reads `packId` comes from the workflow row (falling back to its workflow ID). For explicit versions the row value is only a legacy callback argument: the selected commit determines the actual Pack ID, which is then separately authorized before the response. Client parameters never select a Pack. All responses use `Cache-Control: no-store`.

Successful response:

```json
{
  "workflowId": "example-flow",
  "packId": "example-pack",
  "baseRef": "origin/wf/example-flow",
  "baseCommit": "<full-git-commit-id>",
  "skippedFiles": { "count": 0, "paths": [], "truncated": false },
  "files": [
    { "path": "scripts/example.py", "content": "print(1)\n", "mode": "100644" }
  ]
}
```

`baseCommit` pins the entire result to one immutable Git commit; it is not a deploy tag and does not establish that the candidate is deployed. `baseRef` is the selected source ref, not a client-selected write target. The only returned paths are Pack-relative `scripts/**` UTF-8 text files with supported extensions `.py`, `.js`, `.ts`, `.sh`, `.bash`; empty text is valid. No script is executed. The server rejects symlinks, gitlinks, invalid path/UTF-8, NUL, more than 500 non-directory entries (including ignored files), a supported script over 1 MiB or combined script contents over 8 MiB. For explicit-version reads, regular files with other extensions, such as `.so` and `.bak`, are ignored without reading their content; unversioned requests fail closed on any unsupported extension, preserving the legacy behavior; mode and path validation still applies to all entries, including unsupported files. A rejected snapshot is not a successful empty list. The selected manifest and referenced workflow file must match the requested workflow identity. For explicit versions, the Pack ID comes from the matching manifest in the selected commit, not the current database row. A Pack may contain multiple workflows; after the host has authorized the entire Pack, all its scripts are included. The specified workflow must occur exactly once in the manifest and its referenced file must match its ID. These consistency checks are not a separate Pack ownership or authorization mechanism. Repository-root `workflow.pack.yaml`, `<packId>/workflow.pack.yaml`, and historical `packs/<packId>/workflow.pack.yaml` layouts are supported. For explicit versions, manifests for unrelated workflows are ignored; multiple matching Pack manifests are rejected. The legacy unversioned path rejects multiple manifests in the DB-derived candidate locations. Malformed candidate manifests fail closed because their applicability cannot be determined safely. Discovery uses non-recursive Git tree queries with literal, bounded pathspec batches, and is bounded by 4,096 candidate directories, 50,000 tree records, 512 Git tree calls, 16 MiB cumulative tree output and 8 MiB cumulative manifest/workflow YAML. Candidate paths and batches also have byte limits. Git links and symlinks are never followed as Pack directories.

On successful reads the current reader always returns `skippedFiles: { count, paths, truncated }`, including `{ "count": 0, "paths": [], "truncated": false }` when no files are ignored. Clients should treat the field as optional for older hosts; its absence means completeness is unknown, not that no files were skipped. `count` is the total number of ignored regular files; `paths` contains up to 100 Pack-relative names, each at most 256 UTF-8 bytes and without control/format characters, and `truncated` indicates that names were omitted. If every name is unsafe to display, `paths` can be empty while `count` is positive. Render these paths as text, not HTML. No ignored file contents are read or returned.

With `version=current`, the resolver uses the unique active deployment record's
tag and returns `refs/tags/deploy/<workflowId>/#N`. With `version=draft`, it
uses only wf and reports its observed commit. Missing production history/tag does not fall back to wf.
The production tag may legitimately differ from a newer saved wf commit.
When version is omitted, the scripts-only reader also uses wf. It never
selects or compares saved/. All paths use exact wf/tag discovery and fetch, never a full-repository clone. A read is not a write authorization.
The configured host still owns repository selection and authentication.

## Host integration contract

The router dependency `readSnapshot(workflowId: string, packId: string, version?: 'current' | 'draft')` receives the validated version as its third argument when it was explicitly requested; omitted versions retain the legacy two-argument call. A host using `readPackScriptsFromRemote` must pass that value as `version` in its input object. A host must set `supportsVersionedSnapshots: true` **only** when its callback honors this argument, resolves the requested draft or active deployment, returns its actual `baseRef`, and reports a missing ref as `Workflow snapshot not found`. The host must also declare `supportsVersionedPackResolution: true` only if the callback discovers the matching Pack inside the selected commit and returns its actual ID. Explicit-version reads resolve and load the snapshot after workflow edit permission, then call `authorizePackRead` with the resolved historical Pack ID before returning any content; the callback and Git reader are trusted server-side components and may temporarily hold the snapshot. Unversioned reads retain authorization before the callback. Old hosts that do not declare both capabilities receive `503 SERVICE_UNAVAILABLE` for explicit-version requests without invoking their callback. The router also checks the returned `baseRef` is a permitted ref for the requested workflow and version and returns a sanitized 503 on a mismatch. This check depends on the trusted host reporting the actual ref; it cannot detect a dishonest or incorrect callback that fabricates the expected `baseRef` for different contents. Requests without `version` do not require either capability declaration, but their returned `baseRef` must still be `origin/wf/<workflowId>`. An old or incorrect callback may omit `skippedFiles`; do not infer completeness from its absence.

## Statuses

- `400` invalid workflow ID or snapshot version.
- `401` no verified identity.
- `403` missing edit permission or permission service.
- `404` workflow, candidate ref or Pack manifest not found.
- `409` conflicting refs or duplicate Pack manifests.
- `503` authorization dependency, host without version capability, mismatched returned ref, Git, malformed Pack, unsupported extension on a legacy unversioned read, unsafe file, limit or other unavailable snapshot. Response body never includes the raw Git error, remote URL, credentials or file contents.

Error responses retain the existing `error` string and add a stable `code`: `INVALID_WORKFLOW_ID` or `INVALID_SNAPSHOT_VERSION` (400), `AUTHENTICATION_REQUIRED` (401), `FORBIDDEN` (403), `WORKFLOW_NOT_FOUND` or `SNAPSHOT_NOT_FOUND` (404), `SNAPSHOT_CONFLICT` (409), and `SERVICE_UNAVAILABLE` (503). Unknown or non-JSON 404 responses may come from an older host or intermediary and do not establish that a Git snapshot is missing. Clients must not display raw error bodies.

Successful responses contain script contents and must not be cached or logged. The caller should display an error when the API is unavailable, and must not treat errors as an empty snapshot. The existing scripts-only endpoint remains compatible. Complete-version writes use the companion `pack-version-write.md` contract; `/auto-heal/save` saves the DB spec and complete wf Pack and `/auto-heal/deploy` publishes a confirmed saved candidate. File changes use the complete-version PUT endpoint, not the scripts-only reader.

## Tests / compatibility

`server/routes/__tests__/pack-scripts.test.ts` exercises authentication and authorization before DB/Git, request tampering, sanitized failures and no-store. `server/services/__tests__/pack-script-snapshot.test.ts` uses synthetic local Git commits to cover ref reconciliation, Pack identity and layout, modes, safe paths, UTF-8 and limits. `server/services/__tests__/pack-script-read-service.test.ts` covers exact fetch/verification/cleanup and a local bare Git integration that rejects importing legacy refs. No production repository or data is used. `server/routes/__tests__/pack-scripts-integration.test.ts` additionally covers a real loopback HTTP server and local bare Git, including a shared Pack and the workflow-ref boundary under an injected authorization policy. Only the disabled public host wiring is included here; the separate internal host prototype is not part of this change. Host-specific identity integration and deployment still require separate verification; a mounted route is not proof of production readiness.
