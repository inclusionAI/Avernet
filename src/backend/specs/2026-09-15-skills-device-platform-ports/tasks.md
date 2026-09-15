# Tasks: `skills` installs through the device/platform ports

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Plan: `plan.md` in this directory. No `spec.md` — the approved scope is
transcribed into that plan's **Context** section.

Base `origin/REL20260915` @ `4d970281`; develop on `REL20260915`.
Paths below are relative to `src/backend/src/agentclaw/community/` unless they
start with `tests/` (relative to `src/backend/`) or are written out in full.

> **Tooling note.** The brief says "run the repo's lint, typecheck". For
> `src/backend` there is a linter (`ruff`, declared at
> `src/backend/pyproject.toml:101`) but **no static type checker** — no mypy,
> no pyright, and `src/backend/scripts/ci_test.sh` runs pytest only, unlike the
> gateway/proxy variants. This is deliberate, not an oversight: it is the
> stated reason `SkillPackageUploadPort`'s members are `@abstractmethod`
> ("a structurally-satisfied Protocol is verified by nothing at all"). So
> "typecheck" below means `ruff check`, and no task may claim a typecheck ran.

---

## Task 1: Reproduce the teclaw replace failure
- **Goal:** A test that fails on `4d970281` because a second upload for an
  existing skill name on a teclaw bot raises `LocalSkillStorageError`.
- **Files:** `tests/community/core/skill_center/test_local_skill_upload_service.py`
- **Done when:**
  - [ ] A teclaw-shaped fake `list_dir` returns recursive rows with
        `name`/`path`/`is_dir`/`size` and **no** `relative_path`
  - [ ] First upload of `my-skill` succeeds; second upload of the same name
        fails with `LocalSkillStorageError`
  - [ ] The test FAILS against `4d970281` — recorded in the commit message
  - [ ] The failure is traced to `factories.py:349`, not to some other raise
- **Depends on:** —

## Task 2: Reproduce the digest returning `None` on teclaw
- **Goal:** Pin the second half of P1 — the reason an entry can never plan
  `unchanged`.
- **Files:** `tests/community/core/skill_center/test_local_skill_upload_service.py`
- **Done when:**
  - [ ] `installed_package_digest` returns `None` for an *installed* teclaw
        package, against `4d970281`
  - [ ] A sibling assertion shows the same call returns a real
        `sha256:` digest on an ARCA-shaped listing — proving the fault is the
        listing contract, not the digest logic
- **Depends on:** Task 1

---

## Task 3: Derive `relative_path` from `path` in the recursive listing
- **Goal:** `TeclawDeviceFileSystem.list_dir(recursive=True)` returns the
  documented contract, deriving each row's `relative_path` by stripping the
  listed dir's prefix.
- **Files:** `core/devices/services/teclaw_device_filesystem.py`,
  `tests/community/plugins/prod/test_teclaw_device_filesystem.py`
- **Done when:**
  - [ ] `_rel_from_path` maps `/workspace/x/sub/a.csv` under `/workspace/x` to
        `sub/a.csv`, and returns `None` for a row with no `path` or one outside
        the listed dir
  - [ ] Shape mirrors `ResourceFileService._rel_path`'s contract **without
        importing it** (it is private)
  - [ ] Non-recursive listings are returned untouched — no synthesis, no
        extra calls
  - [ ] Task 1's test still fails (the walk is not in yet, but `path`-bearing
        rows now pass) — or passes, if the fake supplies `path`; state which
- **Depends on:** Task 2

## Task 4: Walk fallback for rows without a usable `path`
- **Goal:** Correctness that does not depend on the `teclaw_file_promotion`
  inference — compose the tree from non-recursive listings when a row gives
  nothing to strip.
- **Files:** `core/devices/services/teclaw_device_filesystem.py`,
  `tests/community/plugins/prod/test_teclaw_device_filesystem.py`
- **Done when:**
  - [ ] A recursive listing whose rows carry only `name`/`is_dir` still yields
        correct nested `relative_path` values
  - [ ] Two files named `run.sh` at different depths get distinct paths — the
        silent-flatten case
  - [ ] The walk reads only `name` and `is_dir`, the two keys confirmed present
        by the fixture at `test_teclaw_device_filesystem.py:178`
  - [ ] A subdirectory that vanishes mid-walk is skipped, not fatal — the race
        rule `resource_file_service.py:514` already follows
- **Depends on:** Task 3

## Task 5: Preserve every original row key (the promotion guard)
- **Goal:** Stop the synthesis from silently breaking teclaw publish.
- **Files:** `core/devices/services/teclaw_device_filesystem.py`,
  `tests/community/plugins/prod/test_teclaw_device_filesystem.py`
- **Done when:**
  - [ ] A recursive listing returns every key the engine sent — `path`
        especially — with `relative_path` **added**, never substituted
  - [ ] A test asserts `path` survives verbatim, citing
        `core/service_bot/services/deploy/teclaw_file_promotion.py:170` as the
        consumer that would otherwise promote zero files
  - [ ] A row synthesised by the walk (Task 4) also carries `path`, so the two
        roads produce interchangeable rows
- **Depends on:** Task 4

---

## Task 6: Widen `SkillPackageUploadPort` with the directory route
- **Goal:** The port gains `upload_local_skill_files`. **[OVERRIDE — user
  decision; see plan.md *Alternatives Considered*.]**
- **Files:** `core/ports/skill_package_upload_port.py`
- **Done when:**
  - [ ] The third member is declared `@abstractmethod`, matching
        `LocalSkillUploadServiceProtocol`'s signature
  - [ ] The module docstring's "**What it deliberately omits**" paragraph is
        rewritten to state the new three-method surface and why the narrowing
        moved (the materialiser-level test in Task 17), not merely deleted
- **Depends on:** Task 5

## Task 7: Move install/replace/read-back behind the port
- **Goal:** `LocalSkillUploadService` delegates the device I/O;
  `installed_package_digest` becomes a port method.
- **Files:** `core/skill_center/services/local_skill_upload_service.py`,
  `core/bot_config_manifest/apply/skill_package_upload.py`,
  `tests/community/core/skill_center/test_local_skill_upload_service.py`
- **Done when:**
  - [ ] `installed_package_digest`'s body moves into `DeviceSkillPackageUpload`
        unchanged (same "unreadable is unknown, not equal" semantics)
  - [ ] The service still owns, untouched: the edit lock and its
        busy/paused/rollback/unavailable refusals; `_authorize`,
        `is_bot_ready`, the scope re-check under the lock; `validate_zip` and
        the duplicate-name check; the skill row write and its locator
  - [ ] Tasks 1 and 2 now PASS
  - [ ] ARCA tests unchanged and green — no observable behaviour change
- **Depends on:** Task 6

## Task 8: Implement the directory route on the platform port
- **Goal:** `PlatformSkillPackageUpload` satisfies the widened port.
- **Files:** `core/bot_config_manifest/managed_files/ports.py`,
  `tests/community/contracts/test_local_skill_storage.py`
- **Done when:**
  - [ ] It repacks via its own `self._validator.pack_directory` then calls its
        own `upload_local_skill`
  - [ ] The single-`.zip` passthrough quirk at
        `local_skill_upload_service.py:222-225` is reproduced exactly — one
        file whose name ends `.zip` is used as-is, `pack_directory` skipped
  - [ ] A contract test drives both implementations through the same
        three-method surface and asserts matching outcomes
- **Depends on:** Task 7

---

## Task 9: Move `DeviceSkillPackageUpload` into `core/skill_center/`
- **Goal:** The device implementation stops living under `bot_config_manifest/`.
  **[OVERRIDE — extends the brief's §4, which named only the platform one.]**
- **Files:** `core/bot_config_manifest/apply/skill_package_upload.py` →
  `core/skill_center/device_skill_package_upload.py`, plus importers
- **Done when:**
  - [ ] `git mv`, body unchanged; every importer updated
  - [ ] Its docstring no longer describes itself as the apply engine's view
        alone — the open API resolves it too now
  - [ ] Full suite green
- **Depends on:** Task 8

## Task 10: Move `PlatformSkillPackageUpload` into `core/skill_center/`
- **Goal:** The brief's §4 lift, **positional only**.
- **Files:** `core/bot_config_manifest/managed_files/ports.py` →
  `core/skill_center/platform_skill_package_upload.py`, plus importers
- **Done when:**
  - [ ] Class body unchanged; the four helpers it uses
        (`_skill_prefix` `:333`, `_under` `:337`, `_own_row` `:351`, plus the
        `CATEGORY_SKILLS`/`SKILLS_LOCAL_DIR` constants) travel with it
  - [ ] It still imports `managed_files/store.py` — the store is NOT moved
        (it also backs `PlatformIdentity` and the resources port)
  - [ ] A module-docstring line states the lift is **positional, not a
        dependency break**, so the new location does not misrepresent itself
  - [ ] `PlatformIdentity` and the resources port are untouched
- **Depends on:** Task 9

---

## Task 11: Add the `BotSkillPackageService` selector
- **Goal:** The `BotCliToolService` analogue — resolve the bot, read engine +
  delivery mode, pick the port, delegate. Nothing else.
- **Files:** `core/skill_center/bot_skill_package_service.py` (new),
  `tests/community/api/skill_center/test_skills_routes_teclaw.py`
- **Done when:**
  - [ ] ARCA → device; teclaw + switch off → device; teclaw + switch on →
        platform. One test per branch
  - [ ] The switch is read from the typed config cluster
        (`di/config.py:985`, a `bool` on `dev`); the selector parses no YAML
        and re-reads no boolean — `teclaw_platform_managed_from_config`
        (`apply/delivery.py:477`) already ends it at boot
  - [ ] The protocol's members are `@abstractmethod`, per the house reason
        (no static type checker to catch a rename)
- **Depends on:** Task 10

## Task 12: Bind the selector and both ports in DI
- **Goal:** One selector singleton, resolvable from both composition points.
- **Files:** `di/modules/local_skill_upload_module.py`,
  `di/modules/manifest_fetch_module.py`
- **Done when:**
  - [ ] `LocalSkillUploadServiceProtocol` stays bound as today (the service is
        still the device port's inner)
  - [ ] A test asserts the open API and the manifest resolve the **same**
        selector instance — the singleton claim in the brief, verified rather
        than assumed
- **Depends on:** Task 11

## Task 13: Route both open-API upload routes through the selector
- **Goal:** `POST /skills` and `POST /skills/upload-folder` stop injecting the
  raw service.
- **Files:** `adapters/http/openapi_v1/skills/router.py`,
  `tests/community/api/skill_center/test_skills_routes_teclaw.py`
- **Done when:**
  - [ ] `:509` and `:561` inject the selector
  - [ ] Both routes' request and response shapes are byte-for-byte unchanged —
        including the 200-vs-201 created/updated split
  - [ ] A second open-API upload for an existing name on a teclaw bot SUCCEEDS
  - [ ] The folder route's single-`.zip` passthrough still behaves as before
- **Depends on:** Task 12

## Task 14: Point the `skills` materialiser at the selector
- **Goal:** The manifest inherits the same seam.
- **Files:** `core/bot_config_manifest/apply/delivery.py`,
  `tests/community/api/skill_center/test_skills_routes_teclaw.py`
- **Done when:**
  - [ ] `MaterialiserPorts.upload_service` (`apply/delivery.py:124`) is supplied
        by the selector — on `dev` that is the single `TeclawDelivery`
        `_platform_ports()`/`_device_ports()` fork at `:459`
  - [ ] A second manifest apply for an existing name on a teclaw bot SUCCEEDS
  - [ ] A re-apply of an unchanged package plans `unchanged` and writes nothing
        — the convergence property `materialisers/skills.py:452` documents
- **Depends on:** Task 13

---

## Task 15: Carry an error code on `LocalSkillStorageError`
- **Goal:** Stop discarding the cause at the six sites.
- **Files:** `core/skill_center/upload_error_codes.py`,
  `core/skill_center/errors.py`,
  `core/skill_center/services/local_skill_upload_service.py`,
  `tests/community/core/skill_center/test_local_skill_upload_service.py`
- **Done when:**
  - [ ] **On `dev` the file does not exist** (#2187 is `REL`-only). Create it
        at the same path, with #2187's enum name and member spelling, then
        add the five storage members — so a later release merge is a union,
        not a collision between two rival schemes
  - [ ] Additive for the legacy mapper either way: 
        `adapters/http/skill_center/skills.py:234-247` maps *from*
        `SkillManifestErrorCode`, so nothing there changes
  - [ ] All six sites carry a code: `:306` WRITE_FAILED, `:451` WRITE_FAILED,
        `:474` ROLLBACK_FAILED, `:507` RESTORE_FAILED, `:522`/`:532`
        CLEANUP_FAILED, `_is_teclaw` DEVICE_CONTEXT_UNRESOLVED
  - [ ] `adapters/http/skill_center/` is otherwise untouched — the constraint
        holds; note in the PR body that this one file inside it is edited
        additively
  - [ ] No raw exception text is carried anywhere
- **Depends on:** Task 14

## Task 16: Non-empty `reason` for an empty-`str()` exception
- **Goal:** `"write failed: "` never ships blank.
- **Files:** `core/bot_config_manifest/apply/orchestrator.py`, its test module
- **Done when:**
  - [ ] An exception whose `str()` is `""` yields a reason naming
        `type(exc).__name__`
  - [ ] `reason` remains composed, never interpolated from transport text —
        the rule the comments in `apply/materialisers/resources.py` state
  - [ ] `partially_written=True` is still set — the honesty half of `_aborted`
- **Depends on:** Task 15

---

## Task 17: Re-site the narrowing guard
- **Goal:** Keep the intent the port's narrowing bought, now that the port is
  three methods.
- **Files:** `tests/community/architecture/test_narrow_ports_are_declared.py`
- **Done when:**
  - [ ] `test_upload_port_never_exposes_the_directory_route` (`:152`) is
        inverted — the port and both implementations now MUST declare it
  - [ ] A new test asserts the **`skills` materialiser** never calls
        `upload_local_skill_files` — the original concern, one level down
  - [ ] The docstring explains the move, so a later reader does not read the
        inversion as the narrowing having been abandoned
- **Depends on:** Task 16

---

## Task 18: Tests & Verification
- **Goal:** Every acceptance item in the brief demonstrably holds.
- **Done when:**
  - [ ] Second upload for an existing name on a teclaw bot succeeds through
        BOTH the open API and the manifest materialiser
  - [ ] A multi-file, nested package round-trips on a teclaw-shaped listing
  - [ ] `installed_package_digest` returns a real digest for an installed
        teclaw package, so a re-apply plans `unchanged`
  - [ ] Port selection: ARCA → device; teclaw off → device; teclaw on → platform
  - [ ] ARCA behaviour byte-for-byte unchanged
  - [ ] Traversal guards still reject `..` and absolute paths
  - [ ] The orchestrator's `write failed:` reason is non-empty for an exception
        with an empty `str()`
  - [ ] `ruff check` clean on every touched file
  - [ ] All five brief-named test modules plus
        `tests/community/plugins/prod/test_teclaw_device_filesystem.py` pass
  - [ ] Full backend suite green via `src/backend/scripts/ci_test.sh`
  - [ ] PR body notes: whether `adapters/http/skill_center/skills.py`'s
        `SkillService.upload_skill` road looks deprecated (brief asks), that
        the lift is positional, and both `[OVERRIDE]`s
- **Depends on:** Task 17

---

## Groups

- **Group A — Reproduce:** Tasks 1, 2
  - Theme: Make the teclaw replace failure and the always-`None` digest fail in
    CI against `4d970281`, before a line of fix exists.
- **Group B — Listing contract (P1):** Tasks 3, 4, 5
  - Theme: `list_dir(recursive=True)` honours the documented contract on
    teclaw — derived from `path`, walked when it can't be, and never at the
    cost of a key `teclaw_file_promotion` depends on. Lands standalone.
- **Group C — Port seam (P2):** Tasks 6, 7, 8
  - Theme: The port becomes the install/read-back seam and both
    implementations satisfy it. Group A's tests turn green here.
- **Group D — The lift:** Tasks 9, 10
  - Theme: Both implementations leave `bot_config_manifest/`. Pure moves —
    reviewable as `git mv` plus import churn.
- **Group E — Selector & wiring:** Tasks 11, 12, 13, 14
  - Theme: One selector, resolved by both callers, closing the latent
    open-API-can't-see-the-switch inconsistency.
- **Group F — Error reporting:** Tasks 15, 16
  - Theme: The two secondary defects. Independent of A–E; could land alone.
- **Group G — Architecture guards:** Task 17
  - Theme: Re-site the narrowing assertion so the override is enforced, not
    merely permitted.
- **Group H — Verification:** Task 18
  - Theme: Final acceptance check against the brief.
