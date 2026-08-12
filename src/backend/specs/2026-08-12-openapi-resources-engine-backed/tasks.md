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
- [ ] **C3.** Convert each listing entry into one `Resource`. `list_dir` returns
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
- [x] **E4.** Follow-up issue for the deferred engine-side work: **#1002**
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
