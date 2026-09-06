# Tasks: ARCA engine CLI tool endpoints

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1 `[x]`: Add the `cli_tools` core package — protocol, models, directory
- **Goal:** Define the service contract and the per-engine directory resolution,
  with no implementation behind them yet.
- **Files:** `src/engine/community/core/cli_tools/__init__.py`,
  `core/cli_tools/protocol.py`, `core/cli_tools/models.py`,
  `core/cli_tools/directories.py`
- **Done when:**
  - [x] `CliToolsService` Protocol declares `install`, `delete`, `list_tools`,
        `read_tool`, `replace_all`, and is `@runtime_checkable`.
  - [x] **Every Protocol member is decorated `@abstractmethod`**, so an
        implementation that drops one fails at construction rather than
        inheriting a `...` stub that silently returns `None`.
  - [x] `CliToolInfo`, `CliToolPayload`, `CliToolResult` are frozen dataclasses.
  - [x] `cli_dir_beside(workspace)` states the shared rule once: a bot's `cli/`
        is its workspace's sibling.
  - [x] `openclaw_cli_dir()` resolves lazily from `workspace_root_strict()`,
        returning the workspace's sibling when the env var is set and
        `~/.openclaw/cli` when it is not — never resolved at import time.
  - [x] `claude_code_cli_dir()` resolves `<home>/.claude_code/cli` from that
        engine's own workspace root (`plugins/claude_code/layout_pool.py:45`),
        which has no env override.
  - [x] Module docstrings cite `engine-requirements.zh-CN.md` §4 A2 as the
        contract and state that the directory is the engine's, not the platform's.
- **Depends on:** —

## Task 2: Implement `LocalCliToolsService`
- **Goal:** The engine-agnostic filesystem implementation, parameterised by a
  directory callable.
- **Files:** `src/engine/community/core/cli_tools/service.py`
- **Done when:**
  - [ ] `LocalCliToolsService` **explicitly subclasses `CliToolsService`** —
        structural satisfaction alone is verified by nothing in this repo.
  - [ ] `install` writes to a temp file in the target directory, sets mode
        `0o755`, then `os.replace`s onto the target — never leaving a partial
        file runnable.
  - [ ] `install` leaves every other tool in the directory untouched.
  - [ ] `delete` removes the named tool and reports success when it was absent.
  - [ ] `replace_all` installs or replaces every named tool first, collects a
        per-name verdict, and only then prunes names absent from the request.
  - [ ] `replace_all` with an empty sequence removes every tool.
  - [ ] `replace_all` returns a `CliToolResult` for **every** requested name,
        including failures, and does not raise on a partial failure.
  - [ ] `list_tools` stats the directory on every call and computes `md5` from
        the bytes on disk — no cache, no replay of the last write.
  - [ ] `read_tool` returns `None` for an absent name rather than raising.
  - [ ] A name that is empty, contains a path separator, or is `.`/`..` is
        refused before any path is built.
  - [ ] No sha256 verification anywhere — the platform is the single
        enforcement point, and a comment says so.
- **Depends on:** Task 1

## Task 3: Service-level tests
- **Goal:** Pin the behaviours that are invisible at the HTTP layer.
- **Files:** `src/engine/community/core/cli_tools/tests/__init__.py`,
  `core/cli_tools/tests/test_service.py`
- **Done when:**
  - [ ] `list` reflects a file written behind the service's back (drift, not replay).
  - [ ] `list` reports a changed `md5` when a binary is swapped in place under
        the same name.
  - [ ] `replace_all` installs before pruning — asserted by observing the
        directory mid-call, so no "tool is gone" window exists.
  - [ ] A failed install inside `replace_all` does not delete the tool it was
        replacing.
  - [ ] An install interrupted before `os.replace` leaves no runnable file.
  - [ ] A name with a separator or `..` is refused and writes nothing.
  - [ ] An install at the documented single-file cap (200 MiB) succeeds.
- **Depends on:** Task 2

## Task 4: Wire `cli_tools` into the engine abstraction
- **Goal:** Make the service declarable, assignable and reachable the way every
  other domain is.
- **Files:** `src/engine/community/core/engine/capability.py`,
  `core/engine/base.py`, `core/engine/protocol.py`, `manager.py`
- **Done when:**
  - [ ] `Capability` gains `CLI_INSTALL`, `CLI_DELETE`, `CLI_LIST`,
        `CLI_REPLACE`, `CLI_DOWNLOAD`.
  - [ ] `BaseEngine.__init__` initialises `_cli_tools` to `None` and exposes a
        `cli_tools` property.
  - [ ] `_PLUGIN_CAPABILITY_DOMAINS` gains a `_cli_tools` entry, so
        `validate_capabilities()` catches declared-but-unassigned and
        assigned-but-undeclared at engine startup.
  - [ ] `Engine` Protocol declares `cli_tools: CliToolsService | None`.
  - [ ] `EngineManager.cli_tools` raises `CapabilityNotSupportedError` when the
        active engine has none.
  - [ ] Existing engine foundation tests still pass unchanged.
- **Depends on:** Task 1

## Task 5: Bind the service on both community engines
- **Goal:** OpenClaw and Claude Code serve CLI tools; their declarations match
  their assignments.
- **Files:** `src/engine/community/engines/openclaw/engine.py`,
  `engines/claude_code/engine.py`
- **Done when:**
  - [ ] OpenClaw assigns `LocalCliToolsService(openclaw_cli_dir)`; Claude Code
        assigns `LocalCliToolsService(claude_code_cli_dir)` — each its own
        resolver, never a shared constant.
  - [ ] A test asserts the two engines resolve to **different** directories, so
        a future engine cannot silently inherit OpenClaw's tree.
  - [ ] Both declare all five `CLI_*` capabilities in `_CAPABILITIES.supported`.
  - [ ] `validate_capabilities()` passes for both at startup.
- **Depends on:** Tasks 2, 4

## Task 6: Add the `/api/cli` router
- **Goal:** Expose the five endpoints exactly as the platform caller expects.
- **Files:** `src/engine/community/api/cli/__init__.py`, `api/cli/router.py`,
  `api/cli/schemas.py`, `api/app.py`
- **Done when:**
  - [ ] Routes exist at `POST /api/cli/install`, `POST /api/cli/delete`,
        `POST /api/cli/replace`, `GET /api/cli/list`,
        `GET /api/cli/download`, matching `arca_port.py`'s paths and bodies
        byte for byte.
  - [ ] Every handler calls `check_capability` first, so an engine without the
        capability answers 501 rather than appearing to succeed.
  - [ ] `replace` returns `200` with a per-name `results` array even when some
        names failed; a partial failure is never a non-2xx.
  - [ ] `download` for an absent tool returns `200` with
        `success: false, error: "not_found"` — **never 404**, which is reserved
        for "this engine build has no CLI endpoints".
  - [ ] `list` returns `{"tools": []}` for a bot with no tools, not an error.
  - [ ] Responses use the shared `ApiResponse` envelope.
  - [ ] The router is imported and registered in `app.py` beside the others.
- **Depends on:** Task 4

## Task 7: Router tests
- **Goal:** Pin the wire contract the platform parses strictly.
- **Files:** `src/engine/community/api/tests/test_cli_router.py`
- **Done when:**
  - [ ] Install places an executable and leaves other tools alone.
  - [ ] Delete of an absent tool reports success.
  - [ ] Replace removes tools not named in the request; an empty list clears all.
  - [ ] Replace answers for every requested name, including failures, and a
        partial failure is HTTP 200.
  - [ ] A response omitting a requested name would be rejected by the
        platform's parser — asserted by exercising `_failures_in`'s contract
        shape, so the two sides cannot drift.
  - [ ] Download of an absent tool is `200 + not_found`, not 404.
  - [ ] An engine that declares no CLI capability refuses with 501.
  - [ ] A request at the documented size cap is accepted.
- **Depends on:** Task 6

## Task 8: Verification and contract reconciliation
- **Goal:** Confirm the spec's acceptance criteria hold, and close the
  documentation discrepancy the spec recorded.
- **Files:** `src/backend/docs/bot-config-manifest/engine-requirements.zh-CN.md`,
  `src/engine/specs/2026-09-06-arca-cli-tool-endpoints/spec.md`
- **Done when:**
  - [ ] The engine test suite passes, and lint/format are clean.
  - [ ] Every acceptance criterion in `spec.md` is checked off or explicitly
        deferred with a reason.
  - [ ] §4 A2 is updated to match what shipped: the fifth endpoint
        (`download`), the `md5` field on `list`, and the 501-vs-404 refusal
        note — resolving the discrepancy between the repo doc and the engine
        handoff specification, as that document asks.
  - [ ] The end-to-end assertion that an ARCA apply reports `cli_tools`
        succeeded is either added on the platform side or recorded as a named
        follow-up.
- **Depends on:** Tasks 3, 5, 7

---

## Groups

- **Group A — Core service:** Tasks 1, 2, 3
  - Theme: The filesystem behaviour, complete and tested, with no engine or
    HTTP dependency. Reviewable entirely on its own.
- **Group B — Engine wiring:** Tasks 4, 5
  - Theme: The service becomes reachable through `EngineManager` on both
    community engines, with startup validation covering it.
- **Group C — HTTP surface:** Tasks 6, 7
  - Theme: The five endpoints the platform already calls, matching the wire
    contract it parses strictly.
- **Group D — Verification:** Task 8
  - Theme: Acceptance check and reconciling the two written contracts.
