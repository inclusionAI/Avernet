# Plan: ARCA engine CLI tool endpoints

## Approach

CLI tool placement is **pure local filesystem work** — write a file, set the
executable bit, list, delete — unlike `skills`, which relays through the
OpenClaw gateway. So there is one engine-agnostic service implementation
parameterised by a directory, and each engine supplies only that directory.
This is exactly the split the contract asks for: *「目录常量归引擎」*
(`engine-requirements.zh-CN.md` §4 A2) — the directory is the engine's, the
mechanics are not.

The service is reached the way every other domain is: a Protocol under
`core/`, an attribute on `BaseEngine`, a property on `EngineManager`, and a
router that marshals HTTP↔service and applies the capability guard.

## Affected Components

- `src/engine/community/core/cli_tools/` — **new.** Protocol, models, and the
  shared filesystem implementation.
- `src/engine/community/core/engine/capability.py:64` — new `CLI_*` members.
- `src/engine/community/core/engine/base.py:61,196` — new `_cli_tools` slot and
  its `_PLUGIN_CAPABILITY_DOMAINS` entry, so `validate_capabilities()` covers it.
- `src/engine/community/core/engine/protocol.py:81` — `cli_tools` on the
  `Engine` Protocol.
- `src/engine/community/manager.py:298` — `EngineManager.cli_tools`.
- `src/engine/community/engines/openclaw/engine.py:169` and
  `engines/claude_code/engine.py:139` — bind the service, declare capabilities.
- `src/engine/community/api/cli/` — **new.** Router package.
- `src/engine/community/api/app.py:96,264` — import and register.

Only **two** engines exist in this repository's community build
(`engines/openclaw`, `engines/claude_code`); `plugins/aicoding` and
`plugins/hermes` carry ports, not `Engine` subclasses. This narrows the spec's
third Open Question: those two are the whole surface here.

## Data Model Changes

None. Tools are files on disk; the platform's `ac_bot_cli_tool` table remains
the definition of "installed".

## API / Interface Changes

Five new routes. Paths and payloads are fixed by the platform caller
(`src/backend/.../cli_tools/arca_port.py:108-140`) and must not drift.

```python
# src/engine/community/api/cli/router.py (new)
router = APIRouter(prefix="/api/cli", tags=["cli"])

@router.post("/install")   # {name, size_bytes, content_b64} → ApiResponse
@router.post("/delete")    # {name}                          → ApiResponse
@router.post("/replace")   # {tools: [{name, size_bytes, content_b64}]}
@router.get("/list")       # —                               → {tools:[{name,md5,size_bytes}]}
@router.get("/download")   # ?name=…                         → {name,size_bytes,md5,content_b64}
```

```jsonc
// POST /api/cli/replace → 200. Every requested name MUST appear in results;
// the platform raises CliToolDeliveryError on an omission (arca_port.py:_failures_in).
{ "success": true,
  "data": { "results": [ { "name": "mycli",    "success": true },
                         { "name": "othercli", "success": false,
                           "message": "not an executable" } ] } }
```

```jsonc
// GET /api/cli/download?name=absent → 200, NOT 404.
// 404 is reserved: it means "this engine build has no CLI endpoints".
{ "success": false, "error": "not_found", "message": "no such tool: absent" }
```

`POST` for delete, not `DELETE`: the name travels in a body, and proxies strip
bodies from `DELETE` (`arca_port.py:_DELETE_METHOD`).

## Key Files & Functions

```python
# src/engine/community/core/cli_tools/protocol.py (new)
@runtime_checkable
class CliToolsService(Protocol):
    @abstractmethod
    async def install(self, name: str, data: bytes) -> None: ...
    @abstractmethod
    async def delete(self, name: str) -> None: ...           # absent == success
    @abstractmethod
    async def list_tools(self) -> list[CliToolInfo]: ...      # reads disk, never a cache
    @abstractmethod
    async def read_tool(self, name: str) -> CliToolBytes | None: ...
    @abstractmethod
    async def replace_all(self, tools: Sequence[CliToolPayload]) -> list[CliToolResult]: ...
```

**Every member is `@abstractmethod`, and implementations subclass the Protocol
explicitly** — `class LocalCliToolsService(CliToolsService)`, never structural
satisfaction alone. This repository runs no static type checker, so a
structurally-satisfied Protocol is verified by nothing: not at import, not at
construction, not in CI. The two rules together are what make the declaration
load-bearing — a plain `...` stub is silently *inherited* in place of a method
an implementation forgot, so the name still resolves and the call returns
`None` instead of failing. With `@abstractmethod`, instantiating an
implementation that dropped a method raises at construction.

Same rule the backend states for its outbound ports
(`src/backend/src/agentclaw/community/core/ports/README.md`, "Rules"), applied
here for the same reason.

```python
# src/engine/community/core/cli_tools/models.py (new)
@dataclass(frozen=True)
class CliToolInfo:      name: str; md5: str; size_bytes: int
@dataclass(frozen=True)
class CliToolPayload:   name: str; data: bytes
@dataclass(frozen=True)
class CliToolResult:    name: str; success: bool; message: str | None = None
```

```python
# src/engine/community/core/cli_tools/service.py (new)
class LocalCliToolsService(CliToolsService):
    """Engine-agnostic. The directory is the only per-engine variable."""
    def __init__(self, directory: Callable[[], Path]) -> None: ...
```

Load-bearing details of the implementation:

- **`install` is atomic**: write to a temp file in the same directory,
  `chmod 0o755`, then `os.replace` onto the target. A partial write must never
  be left as a runnable command.
- **`name` is validated as a bare filename** — no separators, no `..`, not
  empty. The platform already enforces this, but this service writes to a path
  built from it, so it re-checks. This is a path-traversal guard, not a
  duplicate of the platform's *uniqueness* rule.
- **`replace_all` never partially destroys**: install/replace every named tool
  first, collect per-name verdicts, then delete only names not in the request.
  A tool that failed to install is not deleted from the old set.
- **`list_tools` stats the directory** every call. No cache, no replay — this
  is the whole point of the endpoint (spec: it must catch manual edits and
  snapshot restores).
- **No sha256 re-verification** (contract: the platform is the single
  enforcement point). The `md5` returned by `list`/`download` is computed from
  the bytes actually read, as a *change* test.

```diff
# src/engine/community/core/engine/capability.py:74 — after the Skills block
+    # ── CLI tools ──
+    CLI_INSTALL = "cli.install"
+    CLI_DELETE = "cli.delete"
+    CLI_LIST = "cli.list"
+    CLI_REPLACE = "cli.replace"
+    CLI_DOWNLOAD = "cli.download"
```

```diff
# src/engine/community/core/engine/base.py:196 — BaseEngine.__init__
     self._skills: SkillsService | None = None
+    self._cli_tools: CliToolsService | None = None
```

```diff
# src/engine/community/core/engine/base.py:143 — _PLUGIN_CAPABILITY_DOMAINS
+    (
+        "_cli_tools",
+        (Capability.CLI_INSTALL, Capability.CLI_DELETE, Capability.CLI_LIST,
+         Capability.CLI_REPLACE, Capability.CLI_DOWNLOAD),
+    ),
```

Adding to this table is what makes `validate_capabilities()` (`base.py:326`)
catch a declared-but-unassigned or assigned-but-undeclared CLI service at
engine startup, which is the class of bug that would otherwise surface as a
501 in production.

```diff
# src/engine/community/engines/openclaw/engine.py:169
     self._skills = OpenClawSkillsAdapter(self._port)
+    self._cli_tools = LocalCliToolsService(openclaw_cli_dir)
```

```python
# src/engine/community/core/cli_tools/directories.py (new)
def cli_dir_beside(workspace: Path) -> Path:
    """The rule, stated once: a bot's `cli/` is its workspace's sibling."""
    return workspace.parent / "cli"

def openclaw_cli_dir() -> Path:
    """`$OPENCLAW_WORKSPACE_DIR`'s sibling, else `~/.openclaw/cli`."""
    root = workspace_root_strict()
    return cli_dir_beside(root) if root else Path.home() / ".openclaw" / "cli"

def claude_code_cli_dir(home: Path = Path("/home/admin")) -> Path:
    """`<home>/.claude_code/cli` — that engine's workspace has no env override."""
    return cli_dir_beside(home / ".claude_code" / "workspace")
```

**Every engine needs its own resolver, because every engine has its own
workspace.** OpenClaw's is `$OPENCLAW_WORKSPACE_DIR` or `~/.openclaw/workspace`
(`plugin_api/workspace_root.py:21`); Claude Code's is
`<home>/.claude_code/workspace` with no env override
(`plugins/claude_code/layout_pool.py:45`). What is shared is the *rule* — the
`cli/` directory sits beside the workspace — so that lives in
`cli_dir_beside()` and each engine supplies only its own workspace. A single
`openclaw_cli_dir()` would have put Claude Code's tools under OpenClaw's tree.

Resolved lazily, never at import: OpenClaw's reads an env var BaaS injects at
spawn time. On the ARCA deployment this yields `/home/admin/.openclaw/cli` —
the value the contract names — and on singlebox it stays per-bot, which a
hardcoded constant would break.

A third ARCA engine added later supplies one more resolver here; it does not
touch the service.

```diff
# src/engine/community/manager.py:315 — after the skills property
+    @property
+    def cli_tools(self) -> CliToolsService:
+        cli = self._require_engine().cli_tools
+        if cli is None:
+            raise CapabilityNotSupportedError(self._engine, Capability.CLI_LIST)
+        return cli
```

```diff
# src/engine/community/api/app.py:96
+ from engine.community.api.cli import router as cli_router  # noqa: E402
# …and beside the other include_router calls
+ app.include_router(cli_router)
```

## Dependencies

None. `hashlib`, `base64`, `os`, `pathlib` are stdlib; FastAPI and Pydantic are
already direct dependencies.

## Risks & Mitigations

- **Risk:** A 200 MiB binary base64-encodes to ~267 MiB, held in memory twice
  (encoded body + decoded bytes) per concurrent call. A `replace` of several
  tools multiplies it.
  **Mitigation:** Out of our hands at the wire level — the contract fixes
  base64-in-JSON. Decode once and stream to the temp file rather than holding a
  second copy; assert the body-size ceiling is configured above the encoded
  size, and cover it with a test at the documented cap.
- **Risk:** `replace_all` deleting before installing would publish an
  intermediate "tool is gone" state — the exact window the whole-set endpoint
  exists to avoid.
  **Mitigation:** Ordering is pinned above (install-then-prune) and asserted by
  a dedicated test.
- **Risk:** The router returning 404 for an unknown tool would collide with the
  contract's reserved meaning ("this engine build has no CLI endpoints") and
  make the platform report a permanently broken engine.
  **Mitigation:** `download` answers `200 + success:false + error:"not_found"`.
  Asserted by test.
- **Risk:** A name containing `/` or `..` would escape the directory.
  **Mitigation:** Validated in the service, not only the router, so no caller
  can bypass it.

## Alternatives Considered

- **Per-engine adapters** (`OpenClawCliToolsAdapter`, …), mirroring
  `core/adapters/openclaw/skills.py:89`. Rejected: skills needs an adapter
  because it relays to the gateway; CLI tools touch only the local filesystem,
  so per-engine classes would be four copies differing by one constant.
- **Reusing `FileService`** for the writes. Rejected: it is path-rewriting
  workspace plumbing (`_convert_path`, `plugins/openclaw/_file.py:33`) built
  for OSS-view paths, and it has no notion of the executable bit.
- **Adding PATH injection now.** Out of scope per spec — a recorded v1
  trade-off.
- **Serving `download` from a platform-side cache.** Defeats the purpose: the
  read endpoints exist to observe *the disk*, not to replay what was sent.

## Rollout

No flag, no migration. The capability declaration is the switch: an engine that
does not assign `_cli_tools` and does not declare `CLI_*` refuses with 501, and
`validate_capabilities()` refuses to start an engine whose declaration and
assignment disagree. Deploy order is irrelevant — the platform caller already
handles the "no endpoints" case, so an engine without this change behaves
exactly as it does today.

## Test Strategy

Mirrors `api/tests/test_skills_*.py` (router-level, `app.include_router`) plus
service-level tests against a `tmp_path` directory.

```python
# src/engine/community/api/tests/test_cli_router.py (new)
def test_install_places_an_executable_and_leaves_others_alone(): ...
def test_delete_of_an_absent_tool_reports_success(): ...
def test_replace_removes_tools_not_named_in_the_request(): ...
def test_replace_with_empty_list_clears_every_tool(): ...
def test_replace_answers_for_every_requested_name_including_failures(): ...
def test_replace_partial_failure_is_still_http_200(): ...
def test_download_of_an_absent_tool_is_200_not_found_not_404(): ...
def test_unsupported_engine_refuses_with_501(): ...
```

```python
# src/engine/community/core/cli_tools/tests/test_service.py (new)
def test_list_reflects_a_file_written_behind_the_service(): ...   # drift, not replay
def test_list_md5_changes_when_the_binary_is_swapped_in_place(): ...
def test_replace_installs_before_pruning(): ...                    # no intermediate gap
def test_install_is_atomic_under_a_failed_write(): ...
def test_a_name_with_a_separator_or_dotdot_is_refused(): ...
def test_install_at_the_documented_size_cap(): ...
```

One end-to-end assertion belongs on the platform side but is only meaningful
once this lands, so it is named here and left for a follow-up: an ARCA apply
declaring `cli_tools` reports the category `succeeded`.
