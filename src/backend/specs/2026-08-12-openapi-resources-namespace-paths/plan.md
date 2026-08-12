# Plan — Namespace-relative addressing for OpenAPI bot resources

Spec: [`spec.md`](./spec.md) · Issue: [inclusionAI/Avernet#1000](https://github.com/inclusionAI/Avernet/issues/1000)

## Background: the two addressing schemes

Backend → engine file access goes through `/api/file/*` on the engine. Today the
backend sends an OSS-view absolute path:

```
/aidesktop/aidesktop_{env}/bolt_data/{entity_type}_{entity_id}/{bot_id}/{engine_type}/workspace/<rel>
```

composed by `build_workspace_mapper` (`core/services/resource_addressing.py:38`)
→ `get_bot_engine_dir` (`core/workspace/path_factory.py:117`). That tree does not
exist inside the container; each engine's `_convert_path` folds the prefix onto
its own root (community openclaw: `plugins/openclaw/_file.py:34` →
`/home/admin/.openclaw/`).

The OpenAPI path skips composition entirely: `openapi_v1/resources/router.py:271`
uses the generic `dispatch(ctx)` whose mapper is `_passthrough_mapper`
(`device_filesystem_dispatcher.py:174`, the identity function), and
`core/resources/service.py:381` sets `file_path = filename`. A bare `111.txt`
reaches the engine, matches no rewrite rule, and `Path("111.txt")` resolves
against the engine process CWD.

This plan moves the OpenAPI path to a **namespace-relative** wire format rather
than to the OSS-view format, so one of the two translation layers disappears and
the engine can enforce containment. The console path is untouched.

## teclaw is already the target state

teclaw is not a laggard to be migrated — it is the model being copied, and it is
worth being precise about why, because it also reveals a second symptom of the
same defect.

`TeclawDeviceFileSystem` talks to `/api/v1/file/*` on the teclaw engine, a
different endpoint from the `/api/file/*` that `BaasDeviceFileSystem` uses. What
the two share is the backend-side `DeviceFileSystem` abstraction, not the wire
protocol. teclaw's mapper is `to_engine_relative`
(`core/config_compose/teclaw_paths.py:43`), which emits `/workspace/<rel>` — the
leading slash is correct **there**, because the teclaw engine exposes a
namespace-rooted logical filesystem in which `/workspace` genuinely is absolute.
The four engines on `/api/file/*` expose the real container filesystem, where `/`
is the real root and a leading slash would be a lie. Hence: slash-free on the
wire for those four, and the discriminator accepts both forms so a later
unification is free.

**Second symptom.** `to_engine_relative` raises on anything not
namespace-prefixed, and `dispatch(ctx)` hands teclaw bots exactly that mapper
(`device_filesystem_dispatcher.py:187`). So on a teclaw bot the OpenAPI upload
does not misplace the file — it raises, and the handler returns 502. Same root
cause, opposite failure mode.

Once the service composes `workspace/<rel>`, `to_engine_relative` accepts it and
teclaw works with **no teclaw-specific change**. Two consequences for this plan,
both benign: the Group D `target_path` guard lives in `BaasDeviceFileSystem`, so
teclaw is untouched by it; and the new namespace-relative mapper must not
displace `to_engine_relative` in `_namespaced_mapper`, which branches on provider
before namespace and must keep doing so.

## Wire contract

Backend emits `workspace/<rel>` — **no leading slash**. Engines also accept
`/workspace/<rel>` (normalize by dropping empty segments) so a future
unification with teclaw's format (`to_engine_relative`,
`core/config_compose/teclaw_paths.py:43`, which does emit a leading slash) is
free, but the backend only ever emits the slash-free form.

**Engine discriminator — purely additive:**

```python
_NAMESPACES = ("workspace", "identity", "config")

parts = [p for p in target.split("/") if p and p != "."]
if parts and parts[0] in _NAMESPACES:
    → namespace-relative branch
else:
    → every existing branch, unchanged
```

Only strings whose first segment is exactly one of those three names are
claimed. This must not be inverted into "doesn't start with `/aidesktop` ⇒
relative": live callers pass hardcoded container-absolute paths
(`skill_symlink_verify_service.py:56` → `/home/admin/.openclaw/workspace/skills`;
`identity_addressing.py:30` → `/home/admin/.claude_code/workspace/.claude`), and
an inverted rule would silently re-interpret all of them.

Only `workspace` is implemented. `identity` and `config` are recognized and
raise — never falling through to passthrough, which is what makes today's silent
CWD write possible.

**Namespace root, community openclaw:** `OPENCLAW_WORKSPACE_DIR` when set
(singlebox, injected per-bot at spawn — see `workspace_root_strict`), else
`/home/admin/.openclaw/workspace`. This reuses the existing singlebox branch's
anchor rather than inventing a second one.

**Containment:** resolve both sides before comparing, because the root itself can
be a symlink.

```python
final = (root / "/".join(rest)).resolve()
if not final.is_relative_to(root.resolve()):
    raise ValueError(...)
```

Corp engines (`aicoding`, `claude_code`, `hermes`) implement the same contract in
OCB; their roots are `/home/admin/.{aicoding,claude_code,hermes}/workspace`.
Out of scope here, tracked on the issue.

## Rollout safety

Engine ships first. The backend needs to survive the reverse order without
silently misplacing files, and cannot probe for support: there is no
capability-query client in the backend today (`core/engine_runtime/errors.py:34`
describes the endpoint but nothing fetches it), and adding one would introduce a
cross-repo ordering dependency, since the `Capability` enum lives in this repo
while the corp declarations live in OCB.

Instead, use the response the engine already returns. `api/file/router.py:72`
echoes `data.target_path`, which is `str(final_path)` — the **resolved** path. An
engine that has not learned the new format passes `workspace/111.txt` through
unchanged and echoes it back relative; an updated engine echoes an absolute path.
A `startswith("/")` check distinguishes them with no extra round trip.

The guard belongs in `BaasDeviceFileSystem.write_file`
(`core/devices/services/baas_device_filesystem.py:82`) — the engine seam itself —
conditioned on the path sent being namespace-relative. It is a no-op for the
console path, which sends absolute paths.

Failure mode on an un-updated engine: the file is written to the engine's CWD
(an orphan), `write_file` raises, the handler maps it to 502, and **no resource
record is created**. Loud, and no phantom row.

## Changes

### 1. `core/resources/service.py` — sanitize + compose

`upload_file` (line 354) currently sets `file_path = filename` with no
validation. Replace with:

- `name` must be a leaf: reject on any `/` with `ValueError` (explicit contract →
  error, not the console's silent `basename` truncation, which exists there only
  to support whole-folder drag-upload).
- `parent_path`: drop empty segments and `.`; reject any `..`; reject a leading
  `/` (or strip it — decide in implementation, prefer reject for symmetry).
- Extension allow-list and size cap, matching `ALLOWED_EXTENSIONS` / `MAX_FILE_SIZE`
  (`core/resources/services/file_service.py:21`). Import rather than redefine.
- `file_path = f"{parent_path}/{name}"` when a directory is given, else `name` —
  workspace-relative, matching the console's `rel_path` semantics
  (`file_service.py:430`). **The DB column keeps storing a workspace-relative
  path; no migration.**
- The `workspace/` prefix is *not* added here. It is the device-filesystem
  boundary's job — same split as the console path, where
  `resource_file_service._logical()` adds it at the call site of `device_fs`.

`check_name_exists` already keys on `(name, type, parent_path, user)` and is
already called with `parent_path=parent_path or None` (line 375), so passing a
real `parent_path` fixes the cross-directory false-409 with no further change.

Read/delete (`349`, `426`, `462`) use `resource.path` and must go through the
same logical composition as the write, or they will read from a different place
than they wrote.

### 2. `openapi_v1/resources/router.py` — addressed dispatch

Four handlers resolve `device_fs` via `dispatch(ctx)`: `upload_resource:271`,
`delete_resource`, `download_resource`, `preview_resource`. All four switch to:

```python
device_fs_dispatcher.dispatch_addressed(
    ctx, namespace=WORKSPACE_NS,
    entity_type=..., entity_id=..., bot_id=..., engine_type=...,
)
```

`DeviceContext` carries only `provider/conn_info/binding_id/bot_id/user_id/bot_type`
— no `entity_id`/`entity_type`/`engine_type`. Follow the console router's
`_resolve_params` (`adapters/http/resources/file_router.py:71`): inject
`BotRepository`, use `resolve_engine_for_bot` to default `engine_type` to the
bot's `active_engine`, and default `entity_type` to `"staff"` (matching
`resource_file_service.upload_file`'s signature, line 383). A small local helper
keeps this out of all four handler bodies.

`build_workspace_mapper` currently composes the OSS-view absolute path. It must
instead return the namespace-relative form for these engines. **Decision:** add a
new mapper rather than change `build_workspace_mapper` — the console path calls
the same function and must keep emitting OSS-view. The dispatcher's
`_namespaced_mapper` (`device_filesystem_dispatcher.py:191`) selects by
`namespace`; extend the selection so the OpenAPI flow can opt into the relative
mapper without disturbing the console flow. Simplest shape: a distinct namespace
constant or an explicit flag on `dispatch_addressed`; pick during implementation
and record the choice in the PR.

`upload_resource` gains a `parent_path` query parameter — name chosen for
consistency with the service signature and the stored attribute, over the
console's `path` and the friendlier `dir`.

### 3. Resource schema — additive fields

`openapi_v1/resources/schemas.py` `Resource` exposes no path today, so a
filesystem-backed listing cannot express directories or locate an entry.
Add optional `path: str | None` and `parent_path: str | None`, and a
`parent_path` query parameter on `list_resources` for browsing a subdirectory.
Both additions are additive; no existing field changes.

### 4. `list_resources` — filesystem-backed

Today it is purely repo-backed (`router.py:124`). New shape:

1. List the workspace directory via the addressed `device_fs` (non-recursive,
   `parent_path`-scoped) — same call the console listing makes
   (`resource_file_service.list_dir:235`).
2. Map entries: directories → `ResourceType.FOLDER`, files → `FILE`. Use each
   entry's `relative_path`, not `path`; the absolute `path` is engine-view and
   must not be exposed (`_rel_path`, `resource_file_service.py:200`, already
   prefers `relative_path` for this reason).
3. Join to DB rows by workspace-relative path to attach `resource_id`. Entries
   with no row get an empty `resource_id` (spec: listing-visibility only).
4. Append LINK resources from the repo — they have no filesystem presence.
5. Paginate the merged list in the handler; `total` becomes the merged length.
   The existing service-level `limit`/`offset` push-down no longer applies to
   the file half.

Type filtering (`_legacy_type_for`) still applies: `FOLDER` currently short-circuits
to an empty page (`router.py:141`) and must now return directories instead.

### 5. `plugins/openclaw/_file.py` — namespace branch

Add the discriminator and namespace resolution ahead of the existing three
branches in `_convert_path` (line 34), leaving all three byte-identical. Reject
bare relative paths (currently they reach passthrough → CWD, which is the root
defect) with `ValueError` → 400 via `_map_fs_error` (`api/file/router.py:48`).

`api/file/router.py` also logs nothing on upload — it declares
`log = logging.getLogger("api-file")` (line 28) and never uses it. Add one INFO
line recording the requested `target_path` and the resolved `result.target_path`;
its absence is what made this bug a container expedition.

## Testing

- **Service** — sanitization matrix (separator in `name`, `..` in `parent_path`,
  leading `/`, disallowed extension, oversize), and path composition with and
  without a directory.
- **Router** — endpoint cases replacing the `coverage_baseline.txt:346` entry
  (`POST /openapi/v1/bots/resources/upload: missing [happy, error]`): a happy
  upload asserting the logical path handed to `device_fs`, and a rejection case.
  Remove the baseline line rather than regenerating the file — it carries
  hand-written notes that `--regen` drops (see its header).
- **Device filesystem** — the `target_path` guard: relative path sent + relative
  echo → raises; relative sent + absolute echo → passes; absolute sent (console)
  → guard inert.
- **Listing** — merged view: a DB-backed file, a bot-created file with no row, a
  directory, and a link, all in one page.
- **Engine** — `_convert_path` matrix: `workspace/foo.txt`, `/workspace/foo.txt`,
  `workspace` (root), `workspace/../../etc/passwd` (raises), `identity/x` and
  `config/x` (raise, not-yet-supported), `111.txt` bare (raises), plus regression
  on all three existing branches with unchanged assertions.

## Risks

**Behavior change: bare relative paths now rejected.** The only known emitter is
the broken OpenAPI flow. Confirm by grepping `/api/file/*` callers before
landing; if a real dependant exists, downgrade to a WARNING and keep the old
behavior.

**Listing performance.** The merged listing does a device round trip per call
where the old one hit only the DB. Non-recursive and `parent_path`-scoped keeps
it bounded, but it is a new remote dependency on a previously local endpoint —
an unbound or unreachable bot now affects listing.

**Cross-repo skew.** Corp engines must land the same contract before the backend
ships. The `target_path` guard makes skew fail loudly rather than silently, which
is the mitigation, not a reason to relax the ordering.
