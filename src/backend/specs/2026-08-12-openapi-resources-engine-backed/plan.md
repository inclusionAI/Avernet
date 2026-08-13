# Plan — Engine-backed file resources for the OpenAPI bot resources API

Spec: [`spec.md`](./spec.md) · Issue: [inclusionAI/Avernet#1000](https://github.com/inclusionAI/Avernet/issues/1000)

## Root cause

The OpenAPI path **uses the same device-filesystem seam** as the console — the
backend log shows `[DEVICE-PLUGIN-DEBUG] → BaasDeviceFileSystem(...)` then
`[BaasDeviceFileSystem.write_file] ... file=111.txt` — but bypasses the
addressing in two places:

1. `openapi_v1/resources/router.py:271` calls `dispatch(ctx)` (generic flow),
   whose mapper for a non-teclaw provider is `_passthrough_mapper`
   (`device_filesystem_dispatcher.py:174`) — the identity function.
2. `core/resources/service.py:381` sets `file_path = filename`, so a bare
   `111.txt` enters the seam with no namespace.

Both are needed for the failure. A real mapper with a bare-name input raises —
which is what happens on teclaw, where `to_engine_relative` rejects it, so the
endpoint 502s there instead of misplacing the file.

## Target architecture

The filesystem is the source of truth for files; the record is enrichment.

```
file endpoints  → ResourceFileService → dispatch_addressed(workspace) → device_fs → engine
                       ↑ sanitization, workspace/<rel> composition (already implemented)
                  ResourceService (repo) → enrichment lookup by path, links
link endpoints  → ResourceService (repo)                    unchanged
```

**Reuse `ResourceFileService` rather than growing `ResourceService`.** The
console's file service already exposes exactly the operations needed —
`list_dir:206`, `read_file:311`, `create_directory:344`, `delete:363`,
`upload_file:380` — and already does the two things this change is about: it
composes the logical `workspace/<rel>` path via `_logical()` and dispatches with
`dispatch_addressed(namespace=WORKSPACE_NS)` (`:182`). It also already sanitizes
(`:401-409`: leaf extension allow-list, size cap, `..` filtering).

Delegating means the public API and the console cannot drift, and most of the
work becomes wiring rather than new logic.

## Endpoint map

Literal routes must be declared **before** `/{resource_id}`, as `/check-name`
(`:163`) and `/upload` (`:248`) already are relative to `:293`.

| method + path | today | after |
|---|---|---|
| `GET ""` | repo only | engine listing + record enrichment + repo links |
| `GET /check-name` | repo, by `name` | `device_fs.exists` by `path` for files; repo by `name` for links |
| `POST ""` | create link (repo) | unchanged |
| `POST /upload?path=` | took `name` | `ResourceFileService.upload_file` + best-effort row |
| `GET /download?path=` | was `/{resource_id}/download` | `ResourceFileService.read_file` |
| `GET /preview?path=` | was `/{resource_id}/preview` | `ResourceFileService.read_file` + text-ify |
| `DELETE ""?path=` | was `DELETE /{resource_id}` | `ResourceFileService.delete` + row cleanup |
| `POST /mkdir?path=` | — (`FOLDER` create was 501) | `ResourceFileService.create_directory` |
| `GET /{resource_id}` | any resource | **links only** |
| `PUT /{resource_id}` | link update | unchanged |
| `DELETE /{resource_id}` | any resource | **links only** |

**One parameter, one name.** Every file endpoint takes `path` — the
workspace-relative path, directories included. Upload's existing `name` is
renamed: it stops being a name the moment it accepts `a/b/c.txt`, and a single
spelling across upload / download / preview / delete / list / mkdir removes the
chance of the two drifting. There is no `parent_path` parameter anywhere; the
directory is part of the path.

## Changes

### 1. Router — file endpoints delegate to `ResourceFileService`

Each file handler needs `(entity_type, entity_id, engine_type)`, which
`DeviceContext` does not carry. Follow the console router's `_resolve_params`
(`adapters/http/resources/file_router.py:71`): inject `BotRepository`, default
`engine_type` via `resolve_engine_for_bot` to the bot's `active_engine`, default
`entity_type` to `"staff"`. One local helper, used by every file handler.

`ResourceFileService` resolves its own `device_fs` internally, so the router no
longer injects `DeviceContextResolver` / `DeviceFilesystemDispatcher` for file
operations. Confirm the injected deps stay out of the served schema — the
existing invariant guarded by `test_public_namespace.py`.

### 2. Upload — reject traversal instead of silently dropping it

`ResourceFileService.upload_file` filters `..` segments out
(`resource_file_service.py:409`) rather than rejecting. The console needs that
leniency for whole-folder drag-upload; an explicit API should error instead
(spec criterion 4). **Validate in the OpenAPI router before delegating** — do not
change the shared service, which would alter console behavior:

```python
if any(p == ".." for p in name.split("/")):
    raise ValueError(...)
```

The extension allow-list and size cap come free from the service.

### 3. Upload — write the enrichment record after the file

Order matters and is already correct in principle: bytes first, record second, so
a failed write never leaves a record. Record creation becomes **best-effort** —
a failure to persist enrichment must not fail an upload whose bytes landed, since
the file is now genuinely there and will be listed regardless. Log and continue.

**The record must carry the uploader.** `ResourceFileService` writes no records —
it is purely a device-filesystem service — so the router owns this write, and it
must populate `user_id` and `created_by` from the caller. The console's resource
list exposes the owner (`ResourceListItem.user_id`,
`adapters/http/resources/schemas.py:99`) off the same shared `ac_resource` table,
so an OpenAPI upload that omitted it would appear in the console with a blank
owner. The OpenAPI response does not expose the uploader and is not gaining it.

**Why the record is written at all.** The row is not merely enrichment for this
API — the publish pipeline reads it. `config_composer.py:105` builds a published
bot's manifest from `ac_resource` file rows via `collector.resources()`, so an
upload that writes no row produces a published bot missing that file. That is the
same silent-loss failure this change exists to remove, relocated downstream.

teclaw is already exempt (`collector.resources()` returns `[]` for it) because
its files reach the next version through the engine gather at promotion instead
of a DB mirror. Extending that model to the other engines is the prerequisite for
ever dropping these rows; it is a change to the publish pipeline, not a side
effect of this one. Until then the write stays — best-effort, never authoritative
for existence.

Dropping the table outright would also remove **link** resources entirely: a link
has no file and exists only as a row.

**On the three record fields.** `name`, `path`, and `parent_path` are all
derivable from one workspace-relative path, and after this change only `path`
earns its place in our flow. `parent_path`'s single functional consumer was
duplicate detection (`check_name_exists` filters on it), which moves to the
filesystem in B7. It is still written, because the console's legacy listing
filters rows by it (`core/repository/implementations/platform/resource.py:112`)
and both flows share the table; `name` is separately non-optional on the model
(`core/resources/models.py:36`). Both are a `basename`/`dirname` off `path`, not
independent inputs — and neither is exposed on the response schema.

### 4. Listing — merge filesystem with records

1. `ResourceFileService.list_dir(path=path)` — non-recursive. The query
   parameter is `path` (the directory to list, empty for the root), matching the
   console (`adapters/http/resources/file_router.py:202`). It is a listing input,
   unrelated to the DB `parent_path` attribute.
2. Map entries: directories → `ResourceType.FOLDER`, files → `FILE`. Use
   `relative_path`, never the engine-view absolute `path`
   (`resource_file_service._rel_path:200`).
3. Enrich from records, matched on workspace-relative path. The join is
   **in-memory**: `path`/`parent_path` live inside the `attributes` JSON text
   column (`core/repository/implementations/platform/resource.py:123,79`), not in
   queryable columns, and the repo already filters `parent_path` in Python over
   materialized rows (`:112`). Consistent with today — the handler already notes
   it reads the full row-set.
4. Entries with no record: `resource_id` empty, `source`/timestamps null.
5. Append LINK resources from the repo.
6. Paginate the merged list in the handler; `total` is the merged length.

`_legacy_type_for` currently short-circuits `FOLDER` to an empty page (`:141`)
and must now return directories.

### 5. Schema

`openapi_v1/resources/schemas.py`:

- add optional `path: str | None` — the full workspace-relative path. No
  `parent_path` field: it is a `dirname` off `path`.
- `name` stays, despite being a `basename` off `path` for files, because a
  **link** has a name and no path — the schema is shared across both resource
  types and links would otherwise lose their only label. That residual overlap
  for files is the floor for a single shared model; removing it means splitting
  the schema into file and link variants, which is a larger contract change than
  this one and not proposed here.
- relax `gmt_create` / `gmt_modified` to `str | None` — the engine's listing
  carries no timestamps (`FileEntry` is `name/path/relative_path/is_dir/size`,
  `core/file/models.py:34`), so a bot-created file has none. The console already
  lives with this: `resource_file_service.py:260` reads a `modified_at` key the
  engine never sends, and `:435` fills `datetime.now()` as a placeholder on
  upload. Uploaded files keep real timestamps from their record.

### 6. Delete

Route by what is on disk: `delete_file` for a file, `delete_tree` for a
directory — `ResourceFileService.delete:363` already handles this. Then remove
any matching record. A missing record is not an error; a missing file is a 404.

## Testing

- **Router** — endpoint cases replacing the `coverage_baseline.txt:346` entry:
  happy upload (asserting the logical path reaching `device_fs`), nested-name
  upload, `..` rejection, disallowed extension.
- **Round trip** — upload → list → download → delete → list, asserting the file
  is gone.
- **Bot-created file** — a file present on the device with no record is listed,
  downloaded, and deleted successfully, with `resource_id` empty and timestamps
  null.
- **Links unaffected** — create/update/get/delete a link by record id.
- **Provider coverage** — composed address per provider: baas/arca OSS-view,
  teclaw `/workspace/<rel>` (raises today, should start passing), local absolute
  host path in both pathlib and baas modes.
- **Enrichment failure** — a repo failure during record creation leaves the
  upload successful.

## Risks

**Breaking contract change.** Four operations change shape and record ids stop
resolving for files. Mitigated by the surface's standing "NOT PUBLIC-READY" gate
(router docstring) — this is the cheap moment to do it.

**Listing availability.** Listing becomes a device round trip; an unbound or
unreachable bot now affects an endpoint that was previously local. Non-recursive
and scoped to one directory bounds it.

**Metadata degradation.** Bot-created files report null timestamps and source.
Accepted per the metadata decision; the alternative was adding mtime to the
engine listing, which reintroduces the cross-repo dependency.

**No defense in depth.** Caller-input validation is the only barrier against
traversal until the follow-up puts containment in the engines.

## Follow-up (separate issue, needs OCB)

Short, engine-resolved addresses (`workspace/<rel>` on the wire) with each engine
resolving against its own root, asserting containment, and rejecting unanchored
names; plus a `target_path` absoluteness guard in `BaasDeviceFileSystem.write_file`
so mid-rollout skew fails loudly. Requires
`corp/engines/{aicoding,claude_code,hermes}/file.py` and
`plugins/openclaw/_file.py`, engine-first. teclaw already uses this model and
needs no change.
