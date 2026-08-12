# Tasks — Engine-backed file resources for the OpenAPI bot resources API

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md) · Issue: [#1000](https://github.com/inclusionAI/Avernet/issues/1000)

Branch: `claude/file-upload-endpoint-flow-5rsj6m`

A is the foundation; B and C depend on it; D and E depend on B. No engine
change, no OCB dependency in this scope.

---

## Group A — Wiring and schema

- [x] **A1.** Add a local helper in `openapi_v1/resources/router.py` resolving
  `(entity_type, entity_id, engine_type)`, modelled on the console router's
  `_resolve_params` (`adapters/http/resources/file_router.py:71`): inject
  `BotRepository`, default `engine_type` via `resolve_engine_for_bot` to the
  bot's `active_engine`, default `entity_type` to `"staff"`.
- [x] **A2.** Inject `ResourceFileService` into the router. Confirm it and
  `BotRepository` stay out of the served OpenAPI schema — the existing invariant
  guarded by `test_public_namespace.py`.
- [x] **A3.** `Resource` **response** schema (`openapi_v1/resources/schemas.py`) —
  the model returned by list, get, and the upload response; not a request
  parameter. Add optional `path: str | None`, the **full** workspace-relative
  path (`a/b/c.txt`).
  It is the addressing key: a client takes it from a listing and passes it
  verbatim to `?path=` on download / preview / delete. It is deliberately *not*
  the directory — making `path` mean `a/b` would be `parent_path` under another
  name, and would force every client to rejoin it with `name` before it could
  address anything.
  So for a file: `path` = `a/b/c.txt`, `name` = `c.txt`. For a link: `name` is
  the label, `path` is null.
  **No `parent_path` field** — a `dirname` off `path`. `name` stays because a
  link has one and no path, and the schema is shared. Do **not** add an uploader
  field; the console surfaces it, this API does not.
- [x] **A4.** Relax `gmt_create` / `gmt_modified` to `str | None` — the engine's
  listing carries no timestamps (`FileEntry`, `core/file/models.py:34`), so a
  bot-created file has none.
  **Keep the names.** They are DB-flavoured, but they are the prevailing
  convention across `openapi_v1` (`engine_runtime/sessions/schemas.py:54,105`,
  `routines/schemas.py:28`); only `skills/schemas.py:20` uses
  `created_at`/`updated_at`. Renaming resources alone moves it from the majority
  to the minority and deepens an existing split — an API-wide rename is worth
  doing, in its own issue.
- [x] **A5.** Add a path-sanitization helper used by every file handler: reject
  any `..` segment with `ValueError`; strip leading slashes and empty segments.
  **Do not change `ResourceFileService`'s own filter** (`resource_file_service.py:409`)
  — the console needs its leniency for whole-folder upload.
- [x] **A6.** Confirm `@envelope_errors` maps a bare `ValueError` to 400, not
  500 — check `adapters/http/openapi_v1/responses.py`. If not, raise a mapped
  error type.

## Group B — File endpoints on the engine

- [x] **B1.** `POST /upload`: rename the `name` query parameter to `path` — the
  workspace-relative path, directories included — and delegate to
  `ResourceFileService.upload_file` (which composes `workspace/<rel>` and
  dispatches addressed) after the A5 sanitization. **One parameter.** No
  `parent_path`, and no separate name: the directory is part of the path, and
  `path` is the same spelling every other file endpoint uses.
- [x] **B2.** After a successful upload, write the enrichment record
  best-effort — **including `user_id` and `created_by` from the caller**.
  `ResourceFileService` writes no records, so the router owns this; omitting the
  uploader would make OpenAPI uploads appear in the console's resource list with
  a blank owner (`ResourceListItem.user_id`,
  `adapters/http/resources/schemas.py:99`, off the same shared table).
  `path` (workspace-relative) is the key our flow needs; `name` is
  non-optional on the model (`core/resources/models.py:36`); `parent_path` is
  written only for consistency with the console, whose legacy listing filters
  rows by it (`core/repository/implementations/platform/resource.py:112`) — our
  own duplicate check moves to the filesystem in B7, so nothing here reads it.
  All three derive from the one path. A repo failure must **not** fail the
  upload; log and continue.
  **Do not remove this write.** The publish pipeline reads `ac_resource` file
  rows to build a published bot's manifest (`config_composer.py:105` via
  `collector.resources()`), so an upload with no row yields a published bot
  missing that file. teclaw is exempt because its files reach the next version
  through the engine gather at promotion; extending that to the other engines is
  the prerequisite for dropping these rows, and is publish-pipeline work outside
  this scope.
- [x] **B3.** Replace `GET /{resource_id}/download` with
  `GET /download?path=` → `ResourceFileService.read_file`. Declare it **before**
  `/{resource_id}` (`:293`), as `/check-name` and `/upload` already are.
- [x] **B4.** Replace `GET /{resource_id}/preview` with `GET /preview?path=` →
  `read_file` + the existing text-ification and 1 MB cap
  (`FileTooLargeError` → 413).
- [x] **B5.** Add `DELETE ""?path=` → `ResourceFileService.delete` (which routes
  file vs. directory), then remove any matching record. Missing record is not an
  error; missing file is 404.
- [x] **B6.** Narrow `GET /{resource_id}` and `DELETE /{resource_id}` to links
  only; a file id no longer resolves.
- [x] **B7.** `GET /check-name`: for files, take `path` and resolve via
  `device_fs.exists` instead of the repo; links keep `name` and the repo check.
  (Two parameters on one endpoint reflects the two resource types; splitting the
  file and link surfaces entirely is the cleaner end state but a larger contract
  change than this one.)
- [x] **B8.** Add `POST /mkdir?path=` → `ResourceFileService.create_directory`
  (writes a `.keep`). `POST ""` with `type=FOLDER` keeps returning 501 — folder
  *records* remain unsupported.

## Group C — Listing

- [x] **C1.** Add a `path` query parameter to `list_resources` — the directory to
  list, relative to the workspace root, empty for the root. Named `path` to match
  the console (`adapters/http/resources/file_router.py:202`); it is a listing
  input and has nothing to do with the DB `parent_path` attribute.
- [x] **C2.** List via `ResourceFileService.list_dir(path=path)`, non-recursive.
- [x] **C3.** Convert each listing entry into one `Resource`. `list_dir` returns
  a dict per directory entry — `name`, `path`, `relative_path`, `is_dir`, `size`
  — which maps to: `type` = `FOLDER` when `is_dir` else `FILE`, `name` = the
  entry's `name`, `size` = the entry's `size`, and `path` = **the listed
  directory joined with the entry's `relative_path`**.
  The join is needed because the engine reports `relative_path` relative to the
  *listed* directory, not to the workspace root: listing `a/b` yields
  `relative_path="c.txt"`, and the response `path` must be `a/b/c.txt`.
  `resource_file_service._rel_path:193` already implements exactly this,
  including its fallback to `name` for teclaw, which returns no `relative_path`.
  **Never use the entry's own `path`** — that is the engine-view absolute
  container path (`/home/admin/.aicoding/workspace/...`) and must not be exposed
  through a public API.
- [x] **C4.** ~~Enrich from records~~ — **dropped by decision.** No runtime read
  touches a file record: every field of a workspace entry comes from the device,
  and the record is written solely as the publish pipeline's input. Files
  therefore report an empty `resource_id` and null `source`/timestamps
  uniformly, rather than only when a record happens to be missing. Links are the
  one repo read that remains, because a link has no file.
- [x] **C5.** Entries with no record: empty `resource_id`, null `source` and
  timestamps.
- [x] **C6.** Append LINK resources from the repo.
- [x] **C7.** Paginate the merged list in the handler; `total` is the merged
  length. The service-level `limit`/`offset` push-down no longer covers files.
- [x] **C8.** `FOLDER` must stop short-circuiting to an empty page (`:141`) and
  return directories.

## Group D — Tests

- [x] **D1.** Handler cases replacing `coverage_baseline.txt:346`: happy upload
  asserting the logical path reaching the engine seam, nested-path upload, `..`
  rejection, service rejection (allow-list / size) → 400.
  *Handler tests, not endpoint cases* — see the success-criteria note: the
  endpoint-case runner has no gateway-principal minter, so a case on
  `/openapi/v1` could assert nothing but a 401 (#651).
- [x] **D2.** Round trip: upload → list → download → delete → list, asserting the
  file is gone (`test_real_factory_service_supports_all_handler_methods_e2e`).
- [x] **D3.** Bot-created file: present on the device with no record — listed,
  downloaded, and deleted, with empty `resource_id` and null timestamps.
- [x] **D4.** Links unaffected: create / update / get / delete by record id, and
  a **file** id rejected on both record-addressed routes.
- [ ] **D5.** ~~Provider coverage at the `DeviceFileSystem` boundary~~ —
  **dropped as out of scope.** This PR changes no path mapper: it reuses
  `ResourceFileService`, which composes the same address the console already
  composes and dispatches through the same `build_workspace_mapper`. Per-provider
  boundary assertions (teclaw's `/workspace/<rel>`, the local host path) belong
  with the wire-format change in #1002, which is what actually moves them.
- [x] **D6.** ~~Enrichment failure leaves the upload successful~~ — **inverted by
  review.** The record is the publish pipeline's input, not enrichment, so the
  upload now rolls the file back and fails (502): `record_uploaded_file` failing,
  and that *plus* the rollback failing.

## Group E — Close-out

- [x] **E1.** Edit `src/backend/tests/community/framework/coverage_baseline.txt`
  **by hand** — the removed routes out, the new ones in with the #651 note. Do
  not regenerate; `--regen` drops the hand-written header notes.
- [x] **E2.** Module gates. Run on CI rather than locally: the pinned mirror in
  `uv.lock` is unreachable from this environment, so the pre-push hook's `uv`
  path cannot resolve. The equivalent suites were run against a from-PyPI venv
  (`openapi_v1`, `framework`, `core/resources`, `core/services` — 916 passing)
  and CI runs the real gates on the PR.
- [x] **E3.** Publish the reshaped contract to the gateway's catalog
  (`src/gateway/configs/schemas/bots.openapi.json`) — it is a build output, so
  regenerate with `dump_openapi.py` and republish with
  `gate_and_publish_openapi.py --allow-breaking`. Without this the gateway keeps
  serving the removed `/{resource_id}/download` and `/{resource_id}/preview`.
- [x] **E4.** Update the PR body to the final scope, including the contract
  change (four operations re-shaped, file record ids stop resolving) and the
  "NOT PUBLIC-READY" justification for making it now. Mark ready for review.
- [x] **E5.** Follow-up issue for the deferred engine-side work: **#1002**
  (opened up front; its body cross-links #1000).

---

## Out of scope (deferred to the follow-up issue)

- Namespace-relative wire format (`workspace/<rel>` on the wire).
- Engine-side containment assertions and rejection of unanchored paths.
- The `target_path` absoluteness guard in `BaasDeviceFileSystem.write_file`.
- Any change to `corp/engines/*/file.py` or `plugins/openclaw/_file.py`.

## Out of scope (not planned)

- Changing the console path, `ResourceFileService`'s own sanitization, or
  `build_workspace_mapper`.
- Folder-type resource *records* — `POST ""` with `type=FOLDER` stays 501.
- Migrating historical records (issue owner handles separately).
