# Plan — Fix file addressing for OpenAPI bot resources

Spec: [`spec.md`](./spec.md) · Issue: [inclusionAI/Avernet#1000](https://github.com/inclusionAI/Avernet/issues/1000)

## Root cause

Backend → engine file access goes through `/api/file/*` (`/api/v1/file/*` for
teclaw). The console path composes an OSS-view absolute path:

```
/aidesktop/aidesktop_{env}/bolt_data/{entity_type}_{entity_id}/{bot_id}/{engine_type}/workspace/<rel>
```

via `build_workspace_mapper` (`core/services/resource_addressing.py:38`) →
`get_bot_engine_dir` (`core/workspace/path_factory.py:117`). That tree does not
exist in the container; each engine's `_convert_path` folds the prefix onto its
own root (community openclaw: `plugins/openclaw/_file.py:34`).

The OpenAPI path **uses the same device-filesystem seam** — the backend log shows
`[DEVICE-PLUGIN-DEBUG] → BaasDeviceFileSystem(...)` followed by
`[BaasDeviceFileSystem.write_file] ... file=111.txt` — but bypasses the
addressing, in two independent places:

1. `openapi_v1/resources/router.py:271` calls `dispatch(ctx)` (generic flow),
   whose mapper for a non-teclaw provider is `_passthrough_mapper`
   (`device_filesystem_dispatcher.py:174`) — the identity function.
2. `core/resources/service.py:381` sets `file_path = filename`, so a bare
   `111.txt` enters the seam with no namespace on it.

Both are needed for the failure. A real mapper with a bare-name input raises —
which is exactly what happens on teclaw, where `to_engine_relative` rejects it,
so the endpoint 502s there instead of misplacing the file. An anchored input
through the identity mapper would still go out unchanged.

The fix is therefore to compose the address the console already composes:
`dispatch_addressed(namespace=WORKSPACE_NS, …)` plus a `workspace/`-prefixed
logical path. **No new mapper, no engine change, no cross-repo coordination.**

## Why the mapper is not the whole fix

The `path_mapper` is *injected into* the device filesystem, not baked into it —
`BaasDeviceFileSystem` holds whatever callable it was constructed with. Selecting
the right one gets the file to the workspace on all three providers:

| provider | mapper | wire form |
|---|---|---|
| baas / arca | `build_workspace_mapper` | `/aidesktop/…/workspace/<rel>` → engine rewrites |
| teclaw | `to_engine_relative` (`_namespaced_mapper` checks provider first, `:213`) | `/workspace/<rel>` |
| local | `build_workspace_mapper` | absolute host path — pathlib mode writes to the backend's own disk |

But it does **not** bound the write. `build_workspace_mapper` composes with
`Path.__truediv__`, which does not normalize `..`:

```
rel  = "../../etc/passwd"
     → /aidesktop/…/claude_code/workspace/../../etc/passwd
```

The engine's regex still matches that prefix and rewrites it to
`/home/admin/.aicoding/workspace/../../etc/passwd`; `write_bytes` then lets the
OS resolve the `..` out. Deeper prefixes climb further. Nothing on the engine
side normalizes or asserts containment.

**So input validation is mandatory, not optional, and is the only barrier in
this iteration.** Runtime-side containment is the deferred half.

## Changes

### 1. `core/resources/service.py` — validate and compose

`upload_file` (line 354) currently sets `file_path = filename` with no
validation. Replace with:

- Sanitize, mirroring the console's `preserve_structure` filter
  (`core/services/resource_file_service.py:409`):
  ```python
  safe = "/".join(p for p in filename.lstrip("/").split("/") if p and p != "..")
  ```
  Reject rather than silently drop when the input contained a `..` segment — an
  explicit API should error, not quietly rewrite the caller's path.
- Enforce the extension allow-list and size cap. Import `ALLOWED_EXTENSIONS` /
  `MAX_FILE_SIZE` from `core/resources/services/file_service.py:21` rather than
  redefining them.
- Derive the three record fields from that one input, matching what the console
  writes to the same table (`core/resources/services/resource_service.py:359`):

  | field | value for `a/b/c.txt` | role |
  |---|---|---|
  | `name` | `c.txt` | user-facing leaf |
  | `path` | `a/b/c.txt` | where the bytes are — read and delete use it |
  | `parent_path` | `a/b` | uniqueness key |

  There is no new API parameter: the caller sends `name` only, and it may carry
  a relative path. The engine creates intermediate directories itself
  (`final_path.parent.mkdir(parents=True, exist_ok=True)` in every engine's
  `upload`).

`check_name_exists` already keys on `(name, type, parent_path, user)` and is
already called with `parent_path=parent_path or None` (line 375), so populating
`parent_path` fixes the cross-directory false-409 with no further change.

The `workspace/` prefix is **not** added here — that is the device-filesystem
boundary's job, same split the console uses, where
`resource_file_service._logical()` adds it at the `device_fs` call site.

Read and delete (`349`, `426`, `462`) use `resource.path` and must go through the
same logical composition as the write, or they will read from a different place
than they wrote.

### 2. `openapi_v1/resources/router.py` — addressed dispatch

Four handlers resolve `device_fs` via `dispatch(ctx)`: `upload_resource:271`,
`delete_resource`, `download_resource`, `preview_resource`. All four switch to
`dispatch_addressed(ctx, namespace=WORKSPACE_NS, …)` — the existing method, with
the existing mappers. Nothing about the dispatcher changes.

`DeviceContext` carries only `provider/conn_info/binding_id/bot_id/user_id/bot_type`
— no `entity_id`/`entity_type`/`engine_type`. Follow the console router's
`_resolve_params` (`adapters/http/resources/file_router.py:71`): inject
`BotRepository`, use `resolve_engine_for_bot` to default `engine_type` to the
bot's `active_engine`, and default `entity_type` to `"staff"` (matching
`resource_file_service.upload_file`, line 383). A small local helper keeps this
out of all four handler bodies.

### 3. Resource schema — additive fields

`openapi_v1/resources/schemas.py` `Resource` exposes no path today, so a
filesystem-backed listing cannot express directories or locate an entry. Add
optional `path: str | None` and `parent_path: str | None`, and a `parent_path`
query parameter on `list_resources` for browsing a subdirectory. (This one *is* a
listing parameter — unrelated to the upload input, which carries its own path.)
Both additions are additive; no existing field changes.

### 4. `list_resources` — filesystem-backed

Today it is purely repo-backed (`router.py:124`). New shape:

1. List the workspace via the addressed `device_fs` (non-recursive,
   `parent_path`-scoped) — the same call the console listing makes
   (`resource_file_service.list_dir:235`).
2. Map entries: directories → `ResourceType.FOLDER`, files → `FILE`. Use each
   entry's `relative_path`, never the absolute `path`; the latter is engine-view
   and must not be exposed (`_rel_path`, `resource_file_service.py:200`, already
   prefers `relative_path` for this reason).
3. Join to DB rows by workspace-relative path to attach `resource_id`. Entries
   with no row get an empty `resource_id`.
4. Append LINK resources from the repo — they have no filesystem presence.
5. Paginate the merged list in the handler; `total` becomes the merged length.
   The existing service-level `limit`/`offset` push-down no longer covers the
   file half.

Type filtering (`_legacy_type_for`) still applies: `FOLDER` currently
short-circuits to an empty page (`router.py:141`) and must now return
directories.

## Testing

- **Service** — sanitization matrix (`..` at various depths, leading `/`,
  disallowed extension, oversize) and the name/path/parent_path split for both a
  flat and a nested name.
- **Router** — endpoint cases replacing the `coverage_baseline.txt:346` entry: a
  happy upload asserting the logical path handed to `device_fs`, a nested-name
  upload, and a rejection case. Remove the baseline line **by hand** — the file
  carries hand-written notes that `--regen` drops (see its header).
- **Provider coverage** — assert the composed address per provider: baas/arca
  gets the OSS-view path, teclaw gets `/workspace/<rel>` (this path raises today,
  since `to_engine_relative` rejects the bare name, and should start passing),
  local gets an absolute host path.
- **Listing** — one page containing a DB-backed file, a bot-created file with no
  row, a directory, and a link.

## Risks

**Listing performance and availability.** The merged listing does a device round
trip where the old one hit only the DB. Non-recursive and `parent_path`-scoped
keeps it bounded, but an unbound or unreachable bot now affects an endpoint that
was previously local.

**Pagination semantics change** on a public endpoint: `total` becomes the merged
length and paging moves into the handler.

**No defense in depth until the follow-up.** Caller-input validation is the only
barrier against traversal in this iteration; the engine still normalizes nothing.
The follow-up closes that.

## Follow-up (separate issue, needs OCB)

Move the wire format to a short, engine-resolved namespace-relative address
(`workspace/<rel>`), with each engine resolving it against its own root,
asserting containment, and rejecting unanchored names. Requires matching changes
in `corp/engines/{aicoding,claude_code,hermes}/file.py` plus
`plugins/openclaw/_file.py`, an engine-first rollout, and a `target_path`
absoluteness guard in `BaasDeviceFileSystem.write_file` so a mid-rollout skew
fails loudly instead of silently. teclaw already uses this model
(`to_engine_relative` → `/workspace/<rel>` on `/api/v1/file/*`) and needs no
change.
