# Tasks — Namespace-relative addressing for OpenAPI bot resources

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md) · Issue: [#1000](https://github.com/inclusionAI/Avernet/issues/1000)

Branch: `claude/file-upload-endpoint-flow-5rsj6m`

Groups are ordered by dependency. A/B/D are independent of each other; C depends
on B; E depends on C; F closes out.

---

## Group A — Engine: namespace-relative branch (community openclaw)

- [ ] **A1.** In `src/engine/src/engine/community/plugins/openclaw/_file.py`, add
  `_NAMESPACES = ("workspace", "identity", "config")` and a
  `_namespace_root(ns)` helper. `workspace` → `workspace_root_strict()` when
  `OPENCLAW_WORKSPACE_DIR` is set (singlebox), else
  `/home/admin/.openclaw/workspace`. `identity` / `config` raise `ValueError`
  ("namespace not yet supported").
- [ ] **A2.** Add the discriminator at the top of `_convert_path` (line 34):
  split on `/`, drop empty segments and `.`, and take the namespace branch only
  when the first segment is in `_NAMESPACES`. **Leave all three existing
  branches byte-identical** — PROD_OSS regex, SINGLEBOX regex, passthrough.
- [ ] **A3.** Add the containment assertion: `final.resolve()` must be relative
  to `root.resolve()`, else `ValueError` naming the namespace. An empty
  remainder (`"workspace"`) returns the root itself.
- [ ] **A4.** Reject bare relative paths — a target that is neither
  namespace-prefixed nor absolute raises `ValueError` instead of reaching
  passthrough. **First** grep backend callers of `/api/file/*` to confirm no
  dependant on CWD-relative writes; if one exists, downgrade to a WARNING with
  old behavior preserved and note it in the PR.
- [ ] **A5.** Use the declared-but-unused logger in
  `src/engine/src/engine/community/api/file/router.py:28`: one INFO line on
  upload recording the requested `target_path` and the resolved
  `result.target_path`.
- [ ] **A6.** Tests in `src/engine/src/engine/community/plugins/openclaw/tests/`
  (or the existing `_file` test module): `workspace/foo.txt`,
  `/workspace/foo.txt` (equivalent), `workspace` (root), `workspace/../../etc/passwd`
  (raises), `identity/x` and `config/x` (raise), `111.txt` bare (raises), plus
  regression on all three existing branches with unchanged assertions.

## Group B — Service: sanitize and compose

- [ ] **B1.** In `core/resources/service.py::upload_file` (line 354), import
  `ALLOWED_EXTENSIONS` / `MAX_FILE_SIZE` from
  `core/resources/services/file_service.py:21` rather than redefining them.
- [ ] **B2.** Validate `filename` as a leaf: any `/` → `ValueError`. Do **not**
  silently `basename` it.
- [ ] **B3.** Sanitize `parent_path`: drop empty segments and `.`; any `..` →
  `ValueError`; leading `/` → `ValueError`.
- [ ] **B4.** Enforce the extension allow-list and size cap, matching the console
  path's rules.
- [ ] **B5.** Compose `file_path = f"{parent_path}/{name}"` when a directory is
  given, else `name`. Workspace-relative — **the DB column semantics do not
  change**, so no migration.
- [ ] **B6.** Confirm `@envelope_errors` maps a bare `ValueError` to 400 (not
  500). If it does not, raise a mapped error type instead — check
  `adapters/http/openapi_v1/responses.py`.
- [ ] **B7.** Verify the read/delete sites (`349` delete, `426` download, `462`
  preview) compose the logical path identically to the write. Write-relative +
  read-absolute is the failure this whole change exists to remove.
- [ ] **B8.** Unit tests for the sanitization matrix and composition with/without
  a directory.

## Group C — Addressing: namespace-relative mapper and addressed dispatch

- [ ] **C1.** Add a namespace-relative workspace mapper alongside
  `build_workspace_mapper` (`core/services/resource_addressing.py:38`).
  **Do not modify `build_workspace_mapper`** — the console path calls it and must
  keep emitting OSS-view paths.
- [ ] **C2.** Wire it into `DeviceFilesystemDispatcher._namespaced_mapper`
  (`device_filesystem_dispatcher.py:191`) so the OpenAPI flow can select it
  without disturbing the console flow. Record the chosen mechanism (distinct
  namespace constant vs. explicit flag on `dispatch_addressed`) in the PR body.
- [ ] **C2a.** Select the mapper **by provider**: the new namespace mapper for
  `baas`/`arca` only; `teclaw` keeps `to_engine_relative`; **`local` keeps
  `build_workspace_mapper`**. `LocalDeviceFileSystem` falls back to
  `_pathlib_write_file` — a bare `Path(...).write_bytes()` on the *backend* host —
  whenever there is no BaaS binding, so a namespace-relative path there lands in
  the backend's CWD and reproduces this exact bug one host over.
- [ ] **C2b.** Regression test: a `local`-provider bot's OpenAPI upload still
  receives an absolute host path at the `DeviceFileSystem` boundary, in both
  pathlib and baas modes.
- [ ] **C3.** Add a local helper in `openapi_v1/resources/router.py` resolving
  `(entity_type, entity_id, engine_type)`, modelled on the console router's
  `_resolve_params` (`adapters/http/resources/file_router.py:71`): inject
  `BotRepository`, default `engine_type` via `resolve_engine_for_bot` to the
  bot's `active_engine`, default `entity_type` to `"staff"`.
- [ ] **C4.** Switch all four handlers — `upload_resource:271`, `delete_resource`,
  `download_resource`, `preview_resource` — from `dispatch(ctx)` to
  `dispatch_addressed(ctx, namespace=WORKSPACE_NS, ...)`. All four together;
  splitting them breaks read/write symmetry.
- [ ] **C5.** Add the `parent_path` query parameter to `upload_resource` and pass
  it through to the service.
- [ ] **C6.** Confirm the injected deps stay out of the served OpenAPI schema
  (the existing invariant guarded by `test_public_namespace.py`).
- [ ] **C7.** Confirm `_namespaced_mapper` still branches on **provider before
  namespace**, so teclaw keeps getting `to_engine_relative` and is not displaced
  by the new mapper. Add a regression test asserting a teclaw bot's OpenAPI
  upload maps `workspace/a.txt` → `/workspace/a.txt` — this path raises today
  (`to_engine_relative` rejects the bare name) and should start passing.

## Group D — Rollout guard

- [ ] **D1.** In `core/devices/services/baas_device_filesystem.py::write_file`
  (line 82), when the path sent is namespace-relative (does not start with `/`),
  parse the engine response and assert `data.target_path` is absolute; raise
  otherwise. Inert for the console path, which sends absolute paths.
- [ ] **D2.** Confirm the raise surfaces as 502 with **no** resource record
  created — `upload_file` writes before `repo.create` (`service.py:387`), which
  is the ordering this depends on.
- [ ] **D3.** Tests in `tests/community/plugins/prod/test_baas_device_filesystem.py`:
  relative sent + relative echo → raises; relative sent + absolute echo → passes;
  absolute sent → guard inert.

## Group E — Filesystem-backed listing

- [ ] **E1.** Add optional `path` and `parent_path` to the `Resource` schema
  (`openapi_v1/resources/schemas.py`). Additive only.
- [ ] **E2.** Add a `parent_path` query parameter to `list_resources` for
  browsing a subdirectory.
- [ ] **E3.** List the workspace through the addressed `device_fs`
  (non-recursive, `parent_path`-scoped), mirroring
  `resource_file_service.list_dir:235`.
- [ ] **E4.** Map entries: directories → `ResourceType.FOLDER`, files → `FILE`.
  Use `relative_path`, never the engine-view absolute `path` — see
  `resource_file_service._rel_path:200` for why.
- [ ] **E5.** Join to DB rows by workspace-relative path to attach `resource_id`;
  entries with no row get an empty `resource_id`.
- [ ] **E6.** Append LINK resources from the repo (no filesystem presence).
- [ ] **E7.** Paginate the merged list in the handler; `total` is the merged
  length. The service-level `limit`/`offset` push-down no longer covers the file
  half.
- [ ] **E8.** Update type filtering: `FOLDER` currently short-circuits to an
  empty page (`router.py:141`) and must now return directories.
- [ ] **E9.** Tests: one page containing a DB-backed file, a bot-created file
  with no row, a directory, and a link.

## Group F — Coverage and close-out

- [ ] **F1.** Endpoint cases for `POST /openapi/v1/bots/resources/upload` in
  `tests/community/endpoints/` — a happy upload asserting the logical path handed
  to `device_fs`, and a rejection case.
- [ ] **F2.** Remove line 346 of
  `src/backend/tests/community/framework/coverage_baseline.txt`
  (`POST /openapi/v1/bots/resources/upload: missing [happy, error]`) **by hand**.
  Do not regenerate — `--regen` drops the hand-written notes in the header.
- [ ] **F3.** Run the module gates: `OCB_PRE_PUSH_RUN_CI=1` per the pre-push
  contract in `AGENTS.md`, against `origin/dev`'s merge base.
- [ ] **F4.** Open the PR as a draft against `dev`, titled
  `refactor(resources): address engine files by namespace-relative path`, with
  the `Problem` / `Solution` / `Validation` sections from
  `.github/pull_request_template.md`. Link it to issue #1000.
- [ ] **F5.** In the PR body, state explicitly that the corp engines
  (`aicoding`, `claude_code`, `hermes`) must land the same contract in OCB
  **before** this ships, and that the Group D guard is what makes the reverse
  order fail loudly instead of silently.

---

## Out of scope (do not do here)

- Changing the console path or `build_workspace_mapper`'s output.
- Retiring the OSS-view format.
- `identity/` and `config/` namespace support (reserved, raising).
- Folder-type resource records — `create_resource` for `FOLDER` stays 501.
- Correcting historical DB rows (issue owner handles separately).
