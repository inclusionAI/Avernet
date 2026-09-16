# Plan: `skills` installs through the device/platform ports

Base: `origin/REL20260915` @ `4d970281`. Develop on `REL20260915`.

No `spec.md`: the approved scope is the task brief, transcribed in **Context**
below. Decisions taken during planning are recorded in *Alternatives
Considered*; two of them override the brief and are marked **[OVERRIDE]**.

## Context (the approved scope)

Two problems, one change.

**P1 — replacing a local skill on a teclaw bot always fails.** `_replace`
(`core/skill_center/services/local_skill_upload_service.py:378`) calls
`staged.verify()` (`:391`), which funnels into `_read_package_files`
(`core/skill_center/factories.py:332`). That reader requires `relative_path`
on every row of a recursive listing and uses it as the *read address*
(`:349` validates it, `:357` reads from it).
`TeclawDeviceFileSystem.list_dir`
(`core/devices/services/teclaw_device_filesystem.py:205`) returns engine rows
untouched and teclaw supplies no `relative_path` — documented at
`core/services/resource_file_service.py:351`. Permanent, not intermittent:
`installed_package_digest` (`.../local_skill_upload_service.py:95`) calls the
same reader at `:131`, catches `OSError` and returns `None` ("unknown, never
equal"), so an entry can never plan `unchanged` and is always forced down
`_replace`.

**P2 — only the manifest gets the device/platform split.**
`SkillPackageUploadPort` (`core/ports/skill_package_upload_port.py`) has two
implementations, but sits ABOVE `LocalSkillUploadService`: the manifest picks
one, the open API always gets the raw service. Push the seam down so both
callers inherit it.

**Axis is device vs platform, NOT engine family.** No `arca_port.py` /
`teclaw_port.py` for skills. Teclaw uses the same file primitives as ARCA, so
the two would be identical code; path mapping already differs above the port
(`core/skill_center/factories.py:536`) and transport below it.

| engine | switch off | switch on |
|---|---|---|
| ARCA | device | device (no platform road) |
| teclaw | device | platform |

**Stays in the service, not the ports:** the edit lock and its
busy/paused/rollback/unavailable refusals; `_authorize`, `is_bot_ready`, the
scope re-check under the lock; `validate_zip` and the duplicate-name check;
the skill row write and its locator. Only this goes behind the port: *install
these files under this name; report what is currently installed there.*

**Constraints.** Do not change `SkillService.upload_skill` or
`adapters/http/skill_center/`. Do not change ARCA's observable behaviour. Do
not change the write path (per-file fan-out). Do not flip or remove
`teclaw_platform_managed`. Do not weaken the edit lock, the rollback path, or
the traversal guards. Do not create engine-named skill ports.

> Paths in the brief omit the real prefix. Everything below is under
> `src/backend/src/agentclaw/community/` unless written out in full.

## Base-branch history (deltas retired 2026-09-16)

An earlier revision of this plan carried a *Base-branch deltas* section: when
the work was retargeted from `REL20260915` to `dev`, two commits the brief's
premises rested on were missing from `dev`, and two tasks had to be written
around their absence.

**Both are now on `dev` and the deltas are retired.** `dev` picked up the
`REL20260915` ports as fresh commits (new SHAs, same content):

| brief's premise | REL commit | now on `dev` as |
|---|---|---|
| `core/skill_center/upload_error_codes.py` exists | `bc1a288a` (#2187) | `11c53ed7` |
| the manifest delivery seam is injected, not assembled from a flag | `19c0f197` (#2195) | `430b41be` |

Consequences, both reverting to the brief's own assumptions:

1. **Task 15 extends the contract again, rather than creating it.**
   `upload_error_codes.py` is present, so the brief's "prefer it over a second
   scheme" applies as written, and the new storage members are added to the
   existing enum.
2. **The switch is a `TeclawDeliveryMode` enum again, not a `bool`.**
   `core/bot_config_manifest/delivery_mode.py` exists on `dev`, and
   `di/config.py:989` is `teclaw_delivery_mode` once more. The selector reads
   the enum, as the brief describes.

One unrelated change in the same range is worth noting because it edits a file
this plan touches: **#2231** (*fix(skills): delegate local package deletion to
runtime*) removed `LocalSkillQuarantineRepairError`, `quarantine_to` and
`restore_from` from `core/skill_center/factories.py` (−77 lines). The P1 funnel
is unaffected — `_read_package_files` is still the reader `verify` and
`copy_to` share, still guards the path and still reads from it — but its line
numbers moved, and are corrected throughout this document.

**#2237** (*remove global git clone timeout*) also lands in this range; it
supersedes part of the #2224 bootstrap fix that unblocked this PR's CI, and is
noted only so a later reader does not mistake it for a regression.

## Approach

Four moves, in dependency order.

1. **Repair the listing contract inside `TeclawDeviceFileSystem.list_dir`**, so
   every recursive-listing consumer gets the documented `relative_path` —
   derived, never inferred from a bare filename. Existing row keys are
   preserved, `path` included, because `teclaw_file_promotion.py:170` reads it.
2. **Move the port seam down into `LocalSkillUploadService`**, which delegates
   install/replace/read-back to a `SkillPackageUploadPort`.
   `installed_package_digest` becomes a port method.
3. **Add `BotSkillPackageService`**, the `BotCliToolService` analogue: resolve
   the bot, read engine + delivery mode, pick the port, delegate. Both the open
   API and the manifest materialiser go through it.
4. **Lift both port implementations out of `bot_config_manifest/`** into
   `core/skill_center/`, behaviour unchanged.

Plus the two secondary defects (error messages), which are independent.

### P1 in detail — why no probe was needed, and what the fix derives from

The brief asked for a live-container probe of `POST /api/v1/file/list` to
learn whether teclaw omits `relative_path` or returns it absolute. That is not
available here, and it is not needed: the answer is legible from live code.

`core/service_bot/services/deploy/teclaw_file_promotion.py` is the
publish-promotion sweep — it gathers a teclaw bot's whole `/workspace` and
`/identity` into OSS at draft→verify / verify→publish. It does
`list_dir(ns, recursive=True)` (`:156`) and then:

```python
# core/service_bot/services/deploy/teclaw_file_promotion.py:170
engine_path = entry.get("path") or ""  # e.g. "/workspace/sub/x.csv"
if not engine_path:
    continue
if engine_path != f"/{ns}" and not engine_path.startswith(f"/{ns}/"):   # :177
    ...skip out-of-namespace...
rel = logical[len(ns):].lstrip("/")     # :198  "sub/x.csv"
```

If teclaw's recursive listing did not return an absolute `path`, every row
would `continue` and every teclaw publish would ship an artifact with **zero
files** — against a sweep whose docstring says a missing read is "a hard
failure, not a silent skip". So teclaw returns `path`, nested rows included.
It does not return `relative_path`; both facts hold at once, and
`resource_file_service.py:351` only ever claimed the latter.

This is inference from deployed code, not a confirmed response, so the fix
does not *depend* on it: `path` is the fast road, and a client-side walk is the
fallback when a row has no usable `path`. The walk is not novel —
`core/services/resource_file_service.py:489-523` already enumerates a whole
tree provider-blind out of non-recursive `list_dir` calls, reading only `name`
and `is_dir`, both confirmed present in teclaw rows by the fixture at
`tests/community/plugins/prod/test_teclaw_device_filesystem.py:178`.

Correctness argument for both roads: the parent directory is known by
construction — from the prefix that was stripped, or from the walk that
descended — so two files named `run.sh` at different depths can never collapse
onto one address.

## Affected Components

- `core/devices/services/teclaw_device_filesystem.py` — repair `list_dir`'s
  recursive contract (P1). The only file that changes device behaviour.
- `core/skill_center/services/local_skill_upload_service.py` — delegate
  install/replace/read-back to the port; carry error codes on
  `LocalSkillStorageError`.
- `core/ports/skill_package_upload_port.py` — gains
  `upload_local_skill_files` **[OVERRIDE]**.
- `core/skill_center/device_skill_package_upload.py` — **moved** from
  `core/bot_config_manifest/apply/skill_package_upload.py`.
- `core/skill_center/platform_skill_package_upload.py` — **moved** from
  `core/bot_config_manifest/managed_files/ports.py:214`.
- `core/skill_center/bot_skill_package_service.py` — **new** selector.
- `adapters/http/openapi_v1/skills/router.py` — both upload routes inject the
  selector instead of the raw service.
- `di/modules/local_skill_upload_module.py` — bind the selector and both ports.
- `di/modules/manifest_fetch_module.py` — manifest resolves the same selector.
- `core/bot_config_manifest/apply/delivery.py` — `MaterialiserPorts.upload_service`
  is now supplied by the selector for both teclaw strategies.
- `core/skill_center/upload_error_codes.py` — new storage members (additive).
- `core/bot_config_manifest/apply/orchestrator.py:293` — non-empty `reason`.

**Explicitly NOT touched:** `core/skill_center/services/skill_service.py`
(`upload_skill` at `:1683`), all of `adapters/http/skill_center/`, and the
per-file write fan-out at `core/skill_center/factories.py:172`.

## Data Model Changes

None.

## API / Interface Changes

No HTTP contract changes. `POST /openapi/v1/bots/{bot_id}/skills` and
`/skills/upload-folder` keep their request and response shapes byte for byte;
only what they inject changes.

**The port gains a third method [OVERRIDE].**

```diff
# core/ports/skill_package_upload_port.py
  class SkillPackageUploadPort(Protocol):
      @abstractmethod
      async def upload_local_skill(
          self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
      ) -> dict[str, Any]: ...
+
+     @abstractmethod
+     async def upload_local_skill_files(
+         self, *, bot_id: str, owner_id: str, actor_id: str,
+         files: Sequence[tuple[str, bytes]],
+     ) -> dict[str, Any]:
+         """Install a browser-selected directory: repack, then install."""
+         ...

      @abstractmethod
      async def installed_package_digest(
          self, *, bot: Mapping[str, Any], bot_id: str, owner_id: str, name: str
      ) -> Optional[str]: ...
```

**The device filesystem's recursive listing contract.**

```jsonc
// TeclawDeviceFileSystem.list_dir(dir, recursive=True) — one row, after
{ "name": "run.sh", "is_dir": false, "size": 41,
  "path": "/workspace/skills-local/my-skill/scripts/run.sh",  // preserved as-is
  "relative_path": "scripts/run.sh" }                          // added
```

Non-recursive listings are untouched: no synthesis, no extra calls, rows
returned exactly as today.

## Key Files & Functions

### 1. Repair the listing contract

```diff
# core/devices/services/teclaw_device_filesystem.py:205
  async def list_dir(self, dir_path, *, recursive=False) -> list[dict] | None:
      engine_path = self._path_mapper(dir_path)
      ...
-     return result.get("data", {}).get("files", [])
+     rows = result.get("data", {}).get("files", [])
+     if not recursive:
+         return rows
+     return await self._with_relative_paths(rows, engine_path, dir_path)
```

```python
# core/devices/services/teclaw_device_filesystem.py (new, ~40 lines)
async def _with_relative_paths(self, rows, engine_path, dir_path):
    """Give every recursive row the documented ``relative_path``.

    teclaw returns ``path`` (absolute) but no ``relative_path``. Derive it by
    stripping the listed dir's prefix; when a row carries no usable ``path``,
    fall back to walking non-recursive listings, which need only name/is_dir.
    Every original key is preserved — ``teclaw_file_promotion`` reads ``path``.
    """

@staticmethod
def _rel_from_path(engine_path: str, entry: Mapping[str, Any]) -> str | None:
    """``/workspace/x/sub/a.csv`` under ``/workspace/x`` -> ``sub/a.csv``.
    None when the row has no ``path``, or it is outside the listed dir."""

async def _walk(self, dir_path: str) -> list[dict[str, Any]]:
    """Compose the tree from non-recursive list_dir calls, mirroring the shape
    ResourceFileService._rel_path established (not imported — private)."""
```

Traversal guards at `core/skill_center/factories.py:276-280` are untouched: a
derived path that starts with `/` or contains `..` must still be rejected
there. Synthesis never loosens validation.

### 2. Port seam into the service

```diff
# core/skill_center/services/local_skill_upload_service.py
- async def installed_package_digest(self, *, bot, bot_id, owner_id, name):
-     ...factory -> storage -> read_package_files -> sha256...
+ # moves verbatim into DeviceSkillPackageUpload; the service keeps the lock,
+ # _authorize, is_bot_ready, validate_zip, the duplicate check and the row
+ # write, and delegates only "install these files / report what is installed".
```

### 3. The selector

```python
# core/skill_center/bot_skill_package_service.py (new)
class BotSkillPackageService(BotSkillPackageServiceProtocol):
    """Resolve the bot, pick device or platform, delegate. Nothing else."""

    def __init__(self, *, bot_service, device_port_factory, platform_port_factory,
                 is_teclaw: Callable[[str, str], bool],
                 delivery_mode: TeclawDeliveryMode) -> None: ...

    def _port(self, bot_id: str, owner_id: str) -> SkillPackageUploadPort:
        if not self._is_teclaw(bot_id, owner_id):
            return self._device()                      # ARCA: always device
        if self._delivery_mode is TeclawDeliveryMode.PLATFORM:
            return self._platform()                    # teclaw + switch on
        return self._device()                          # teclaw + switch off
```

`delivery_mode` comes from the typed config cluster (`di/config.py:989`),
where `teclaw_delivery_mode_from_config`
(`core/bot_config_manifest/delivery_mode.py:79`) already ends the boolean at
boot — it is strict on purpose, because `bool("false")` is `True`. The
selector re-reads nothing and parses nothing.

### 4. The lift

`git mv` both implementations into `core/skill_center/`, copying down the four
module-level helpers `PlatformSkillPackageUpload` uses from
`core/bot_config_manifest/managed_files/ports.py` (`_skill_prefix` `:333`,
`_under` `:337`, `_own_row` `:351`, and `CATEGORY_SKILLS`/`SKILLS_LOCAL_DIR`
re-exported from `managed_files/store.py`). Bodies unchanged.

### 5. Secondary defects

```diff
# core/bot_config_manifest/apply/orchestrator.py:293
-     reason=f"write failed: {exc}",
+     reason=f"write failed: {exc.__class__.__name__}" if not str(exc)
+            else f"write failed: {_safe(exc)}",
```

`reason` still never carries raw exception text — composed, not interpolated,
per the comments in `core/bot_config_manifest/apply/materialisers/resources.py`.
`_safe` maps to a `SkillUploadErrorCode` where one exists and to the class name
otherwise; it never interpolates a transport message, which can quote a header,
and a header can carry a token.

```diff
# core/skill_center/upload_error_codes.py  (additive)
  class SkillUploadErrorCode(StrEnum):
      ...
+     STORAGE_WRITE_FAILED = "SKILL_STORAGE_WRITE_FAILED"
+     STORAGE_ROLLBACK_FAILED = "SKILL_STORAGE_ROLLBACK_FAILED"
+     STORAGE_RESTORE_FAILED = "SKILL_STORAGE_RESTORE_FAILED"
+     STORAGE_CLEANUP_FAILED = "SKILL_STORAGE_CLEANUP_FAILED"
+     DEVICE_CONTEXT_UNRESOLVED = "SKILL_DEVICE_CONTEXT_UNRESOLVED"
```

The six sites become `raise LocalSkillStorageError(code) from exc`:
`:306` WRITE_FAILED, `:451` WRITE_FAILED, `:474` ROLLBACK_FAILED,
`:507` RESTORE_FAILED, `:522`/`:532` CLEANUP_FAILED,
`_is_teclaw` DEVICE_CONTEXT_UNRESOLVED.

## Dependencies

None. No new packages, no version bumps.

## Risks & Mitigations

- **Risk:** the `path` inference is wrong and teclaw sends `name` only.
  **Mitigation:** the walk fallback triggers per row, so the package still
  round-trips; only the round-trip count changes. A test drives both shapes.
- **Risk:** synthesising rows breaks `teclaw_file_promotion.py:170`, which
  reads `path` — a silent, repo-wide teclaw publish regression that none of
  the five brief-named test modules would catch.
  **Mitigation:** preserve every original key; add a test asserting `path`
  survives a recursive listing unchanged.
- **Risk:** widening the port lets the `skills` materialiser reach for
  `upload_local_skill_files`, which the narrowing existed to prevent.
  **Mitigation:** replace the port-shape assertion at
  `tests/community/architecture/test_narrow_ports_are_declared.py:152` with one
  asserting the *materialiser* never calls it — same intent, one level down.
- **Risk:** the open API begins resolving the delivery mode, changing which
  road a teclaw+switch-on upload takes. That is the point (brief §3), but it is
  a behaviour change on a live endpoint.
  **Mitigation:** switch defaults off (`delivery_mode.py:118`); explicit tests
  for all three selector branches.
- **Risk:** the walk costs one round trip per subdirectory.
  **Mitigation:** packages cap at 500 files and are typically one or two levels;
  it is the cost `resource_file_service.py:513` already pays per archive.

## Alternatives Considered

- **Probe a live container to settle the listing shape** (the brief's step 1).
  Not available from this environment, and unnecessary once
  `teclaw_file_promotion.py:170` is read. Recorded above as inference, with the
  walk as the hedge.
- **STOP-and-report on P1** (the brief's escape hatch). Rejected: the shape is
  derivable and the walk is shape-independent, so neither trigger holds.
- **Patch `_read_package_files` instead of `list_dir`.** Rejected by the brief
  and on merit: it would leave every other recursive consumer on the broken
  contract.
- **Walk unconditionally, ignoring `path`.** Simplest, but pays the round trips
  always and discards a correct answer when the engine gives one.
- **[OVERRIDE] Keep the port at two methods; repack in the selector.** Planned
  first, then overruled by the user: the port gains
  `upload_local_skill_files`. Consequences: the assertion at
  `test_narrow_ports_are_declared.py:152` inverts, `PlatformSkillPackageUpload`
  grows the method, and two docstrings that argue the narrowing at length are
  rewritten. The upside is that the repack — and its single-`.zip` passthrough
  quirk at `local_skill_upload_service.py:222-225` — stays in one place instead
  of being reproduced in the selector.
- **[OVERRIDE] Lift only `PlatformSkillPackageUpload`** (the brief's §4).
  Extended to move `DeviceSkillPackageUpload` too: once the open API resolves
  both, leaving the device one in `core/bot_config_manifest/apply/` is exactly
  as manifest-shaped as the problem §4 names. Reversible if unwanted.
- **Break the store dependency during the lift.** Rejected.
  `PlatformSkillPackageUpload` extends `_StorePort`
  (`managed_files/ports.py:59`), which also backs `PlatformIdentity` and the
  resources port. Moving the store restructures all of `managed_files/` — a
  separate PR. **The lift is positional, not a dependency break**, and the PR
  body must say so.

## Rollout

No migration, no flag, no ordering constraint. `teclaw_platform_managed` is
untouched and stays default-off, so on a default deployment the selector
returns the device port for every bot and behaviour is identical to today
except that teclaw replace stops failing.

## Test Strategy

Extending the five modules the brief names; no new files.

```python
# tests/community/plugins/prod/test_teclaw_device_filesystem.py
def test_recursive_listing_derives_relative_path_from_path(): ...
def test_recursive_listing_walks_when_rows_have_no_path(): ...
def test_recursive_listing_preserves_path_for_promotion(): ...   # the silent-regression guard
def test_non_recursive_listing_is_unchanged(): ...

# tests/community/core/skill_center/test_local_skill_package_storage_io.py
def test_multi_file_nested_package_round_trips_on_teclaw_listing(): ...
def test_traversal_guards_still_reject_dotdot_and_absolute(): ...

# tests/community/core/skill_center/test_local_skill_upload_service.py
def test_second_upload_for_existing_name_on_teclaw_succeeds(): ...      # the regression
def test_installed_package_digest_returns_digest_for_teclaw(): ...     # so re-apply plans unchanged
def test_arca_behaviour_is_byte_for_byte_unchanged(): ...
def test_storage_error_carries_a_code_for_each_of_the_six_sites(): ...

# tests/community/api/skill_center/test_skills_routes_teclaw.py
def test_openapi_second_upload_succeeds_on_teclaw(): ...
def test_manifest_materialiser_second_apply_succeeds_on_teclaw(): ...
def test_port_selection_arca_device_teclaw_off_device_teclaw_on_platform(): ...
def test_folder_route_goes_through_the_selector(): ...
def test_folder_route_single_zip_passthrough_preserved(): ...

# tests/community/contracts/test_local_skill_storage.py
def test_both_ports_satisfy_the_same_install_and_digest_contract(): ...

# tests/community/architecture/test_narrow_ports_are_declared.py
def test_upload_port_declares_the_directory_route(): ...               # inverted [OVERRIDE]
def test_skills_materialiser_never_calls_the_directory_route(): ...    # intent preserved

# tests/community/core/bot_config_manifest/... (orchestrator)
def test_write_failed_reason_is_non_empty_for_empty_str_exception(): ...
```

**Order:** the regression test is written and seen to FAIL against
`4d970281` before any fix lands.

**Before committing:** the repo's lint, typecheck, and every touched test
module.
