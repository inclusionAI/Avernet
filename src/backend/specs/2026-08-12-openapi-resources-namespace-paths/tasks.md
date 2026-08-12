# Tasks — Fix file addressing for OpenAPI bot resources

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md) · Issue: [#1000](https://github.com/inclusionAI/Avernet/issues/1000)

Branch: `claude/file-upload-endpoint-flow-5rsj6m`

A is independent and shippable on its own; B depends on A; C depends on B; D
closes out. No engine or OCB change in this scope.

---

## Group A — Validate and compose the upload path

- [ ] **A1.** In `core/resources/service.py::upload_file` (line 354), import
  `ALLOWED_EXTENSIONS` / `MAX_FILE_SIZE` from
  `core/resources/services/file_service.py:21` rather than redefining them.
- [ ] **A2.** Sanitize `filename`, mirroring the console's filter
  (`core/services/resource_file_service.py:409`): drop leading slashes, drop
  empty and `.` segments. **Reject** with `ValueError` if any `..` segment was
  present — do not silently drop it.
- [ ] **A3.** Enforce the extension allow-list (on the leaf) and the size cap.
- [ ] **A4.** Derive the three record fields from the single `name` input:
  `name` = leaf, `path` = full workspace-relative path, `parent_path` = directory
  (empty for a flat name). Matches what the console writes to the same table
  (`core/resources/services/resource_service.py:359`). **No new API parameter.**
- [ ] **A5.** Confirm `@envelope_errors` maps a bare `ValueError` to 400, not
  500 — check `adapters/http/openapi_v1/responses.py`. If it does not, raise a
  mapped error type instead.
- [ ] **A6.** Verify the read/delete sites (`349` delete, `426` download, `462`
  preview) compose the logical path identically to the write. Write-relative +
  read-absolute is the failure this change exists to remove.
- [ ] **A7.** Unit tests: sanitization matrix (`..` at various depths, leading
  `/`, disallowed extension, oversize) and the name/path/parent_path split for a
  flat and a nested name.

## Group B — Addressed dispatch

- [ ] **B1.** Add a local helper in `openapi_v1/resources/router.py` resolving
  `(entity_type, entity_id, engine_type)`, modelled on the console router's
  `_resolve_params` (`adapters/http/resources/file_router.py:71`): inject
  `BotRepository`, default `engine_type` via `resolve_engine_for_bot` to the
  bot's `active_engine`, default `entity_type` to `"staff"`.
- [ ] **B2.** Switch all four handlers — `upload_resource:271`, `delete_resource`,
  `download_resource`, `preview_resource` — from `dispatch(ctx)` to
  `dispatch_addressed(ctx, namespace=WORKSPACE_NS, ...)`. All four together;
  splitting them breaks read/write symmetry. **No dispatcher change** — existing
  method, existing mappers.
- [ ] **B3.** Confirm the injected deps stay out of the served OpenAPI schema
  (the existing invariant guarded by `test_public_namespace.py`).
- [ ] **B4.** Provider-coverage tests asserting the composed address at the
  `DeviceFileSystem` boundary: baas/arca → OSS-view absolute; teclaw →
  `/workspace/<rel>` (raises today, should start passing); local → absolute host
  path in both pathlib and baas modes.

## Group C — Filesystem-backed listing

- [ ] **C1.** Add optional `path` and `parent_path` to the `Resource` schema
  (`openapi_v1/resources/schemas.py`). Additive only.
- [ ] **C2.** Add a `parent_path` query parameter to `list_resources` for
  browsing a subdirectory. (Listing input only — the upload input carries its own
  path in `name`.)
- [ ] **C3.** List the workspace through the addressed `device_fs`
  (non-recursive, `parent_path`-scoped), mirroring
  `resource_file_service.list_dir:235`.
- [ ] **C4.** Map entries: directories → `ResourceType.FOLDER`, files → `FILE`.
  Use `relative_path`, never the engine-view absolute `path` — see
  `resource_file_service._rel_path:200`.
- [ ] **C5.** Join to DB rows by workspace-relative path to attach `resource_id`;
  entries with no row get an empty `resource_id`.
- [ ] **C6.** Append LINK resources from the repo (no filesystem presence).
- [ ] **C7.** Paginate the merged list in the handler; `total` is the merged
  length. The service-level `limit`/`offset` push-down no longer covers the file
  half.
- [ ] **C8.** Update type filtering: `FOLDER` currently short-circuits to an
  empty page (`router.py:141`) and must now return directories.
- [ ] **C9.** Tests: one page containing a DB-backed file, a bot-created file
  with no row, a directory, and a link.

## Group D — Coverage and close-out

- [ ] **D1.** Endpoint cases for `POST /openapi/v1/bots/resources/upload` in
  `tests/community/endpoints/` — a happy upload asserting the logical path handed
  to `device_fs`, a nested-name upload, and a rejection case.
- [ ] **D2.** Remove line 346 of
  `src/backend/tests/community/framework/coverage_baseline.txt`
  (`POST /openapi/v1/bots/resources/upload: missing [happy, error]`) **by hand**.
  Do not regenerate — `--regen` drops the hand-written notes in the header.
- [ ] **D3.** Run the module gates: `OCB_PRE_PUSH_RUN_CI=1` per the pre-push
  contract in `AGENTS.md`, against `origin/dev`'s merge base.
- [ ] **D4.** Update the PR body to the reduced scope and mark it ready for
  review. State that no engine or OCB change is required.
- [ ] **D5.** Open the follow-up issue for the deferred namespace-relative work
  (see `plan.md` → Follow-up) and link it from #1000.

---

## Out of scope (deferred to the follow-up issue)

- The namespace-relative wire format (`workspace/<rel>` on the wire).
- Engine-side containment assertions and rejection of unanchored paths.
- The `target_path` absoluteness guard in `BaasDeviceFileSystem.write_file`.
- Any change to `corp/engines/*/file.py` or `plugins/openclaw/_file.py`.

## Out of scope (not planned)

- Changing the console path or `build_workspace_mapper`'s output.
- Folder-type resource records — `create_resource` for `FOLDER` stays 501.
- Correcting historical DB rows (issue owner handles separately).
