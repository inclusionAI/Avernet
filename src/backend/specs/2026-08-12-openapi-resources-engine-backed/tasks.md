# Tasks — Engine-backed file resources for the OpenAPI bot resources API

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md) · Issue: [#1000](https://github.com/inclusionAI/Avernet/issues/1000)

Branch: `claude/file-upload-endpoint-flow-5rsj6m`

A is the foundation; B and C depend on it; D and E depend on B. No engine
change, no OCB dependency in this scope.

---

## Group A — Wiring and schema

- [ ] **A1.** Add a local helper in `openapi_v1/resources/router.py` resolving
  `(entity_type, entity_id, engine_type)`, modelled on the console router's
  `_resolve_params` (`adapters/http/resources/file_router.py:71`): inject
  `BotRepository`, default `engine_type` via `resolve_engine_for_bot` to the
  bot's `active_engine`, default `entity_type` to `"staff"`.
- [ ] **A2.** Inject `ResourceFileService` into the router. Confirm it and
  `BotRepository` stay out of the served OpenAPI schema — the existing invariant
  guarded by `test_public_namespace.py`.
- [ ] **A3.** Schema (`openapi_v1/resources/schemas.py`): add optional
  `path: str | None` to `Resource` — the full workspace-relative path
  (`a/b/c.txt`). **No `parent_path` field** — it is a `dirname` off `path`.
  `name` stays: a link has a name and no path, and the schema is shared. Do
  **not** add an uploader field; the console surfaces it, this API does not.
- [ ] **A4.** Relax `gmt_create` / `gmt_modified` to `str | None` — the engine's
  listing carries no timestamps (`FileEntry`, `core/file/models.py:34`), so a
  bot-created file has none.
- [ ] **A5.** Add a path-sanitization helper used by every file handler: reject
  any `..` segment with `ValueError`; strip leading slashes and empty segments.
  **Do not change `ResourceFileService`'s own filter** (`resource_file_service.py:409`)
  — the console needs its leniency for whole-folder upload.
- [ ] **A6.** Confirm `@envelope_errors` maps a bare `ValueError` to 400, not
  500 — check `adapters/http/openapi_v1/responses.py`. If not, raise a mapped
  error type.

## Group B — File endpoints on the engine

- [ ] **B1.** `POST /upload`: rename the `name` query parameter to `path` — the
  workspace-relative path, directories included — and delegate to
  `ResourceFileService.upload_file` (which composes `workspace/<rel>` and
  dispatches addressed) after the A5 sanitization. **One parameter.** No
  `parent_path`, and no separate name: the directory is part of the path, and
  `path` is the same spelling every other file endpoint uses.
- [ ] **B2.** After a successful upload, write the enrichment record
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
- [ ] **B3.** Replace `GET /{resource_id}/download` with
  `GET /download?path=` → `ResourceFileService.read_file`. Declare it **before**
  `/{resource_id}` (`:293`), as `/check-name` and `/upload` already are.
- [ ] **B4.** Replace `GET /{resource_id}/preview` with `GET /preview?path=` →
  `read_file` + the existing text-ification and 1 MB cap
  (`FileTooLargeError` → 413).
- [ ] **B5.** Add `DELETE ""?path=` → `ResourceFileService.delete` (which routes
  file vs. directory), then remove any matching record. Missing record is not an
  error; missing file is 404.
- [ ] **B6.** Narrow `GET /{resource_id}` and `DELETE /{resource_id}` to links
  only; a file id no longer resolves.
- [ ] **B7.** `GET /check-name`: for files, take `path` and resolve via
  `device_fs.exists` instead of the repo; links keep `name` and the repo check.
  (Two parameters on one endpoint reflects the two resource types; splitting the
  file and link surfaces entirely is the cleaner end state but a larger contract
  change than this one.)
- [ ] **B8.** Add `POST /mkdir?path=` → `ResourceFileService.create_directory`
  (writes a `.keep`). `POST ""` with `type=FOLDER` keeps returning 501 — folder
  *records* remain unsupported.

## Group C — Listing

- [ ] **C1.** Add a `path` query parameter to `list_resources` — the directory to
  list, relative to the workspace root, empty for the root. Named `path` to match
  the console (`adapters/http/resources/file_router.py:202`); it is a listing
  input and has nothing to do with the DB `parent_path` attribute.
- [ ] **C2.** List via `ResourceFileService.list_dir(path=path)`, non-recursive.
- [ ] **C3.** Map entries: directories → `ResourceType.FOLDER`, files → `FILE`.
  Use `relative_path`, never the engine-view absolute `path`
  (`resource_file_service._rel_path:200`).
- [ ] **C4.** Enrich from records, matched on workspace-relative path. In-memory
  join — `path`/`parent_path` live in the `attributes` JSON column
  (`core/repository/implementations/platform/resource.py:123,79`), and the repo
  already filters `parent_path` in Python (`:112`).
- [ ] **C5.** Entries with no record: empty `resource_id`, null `source` and
  timestamps.
- [ ] **C6.** Append LINK resources from the repo.
- [ ] **C7.** Paginate the merged list in the handler; `total` is the merged
  length. The service-level `limit`/`offset` push-down no longer covers files.
- [ ] **C8.** `FOLDER` must stop short-circuiting to an empty page (`:141`) and
  return directories.

## Group D — Tests

- [ ] **D1.** Endpoint cases replacing `coverage_baseline.txt:346`: happy upload
  asserting the logical path reaching `device_fs`, nested-name upload, `..`
  rejection, disallowed extension.
- [ ] **D2.** Round trip: upload → list → download → delete → list, asserting the
  file is gone.
- [ ] **D3.** Bot-created file: present on the device with no record — listed,
  downloaded, and deleted, with empty `resource_id` and null timestamps.
- [ ] **D4.** Links unaffected: create / update / get / delete by record id.
- [ ] **D5.** Provider coverage at the `DeviceFileSystem` boundary: baas/arca →
  OSS-view absolute; teclaw → `/workspace/<rel>` (raises today, should start
  passing); local → absolute host path in pathlib and baas modes.
- [ ] **D6.** Enrichment failure: a repo error during record creation leaves the
  upload successful.

## Group E — Close-out

- [ ] **E1.** Remove line 346 of
  `src/backend/tests/community/framework/coverage_baseline.txt` **by hand**.
  Do not regenerate — `--regen` drops the hand-written header notes.
- [ ] **E2.** Run the module gates: `OCB_PRE_PUSH_RUN_CI=1` per the pre-push
  contract in `AGENTS.md`, against `origin/dev`'s merge base.
- [ ] **E3.** Update the PR body to the final scope, including the contract
  change (four operations re-shaped, file record ids stop resolving) and the
  "NOT PUBLIC-READY" justification for making it now. Mark ready for review.
- [ ] **E4.** Open the follow-up issue for the deferred engine-side work (see
  `plan.md` → Follow-up) and link it from #1000.

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
