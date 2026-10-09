# Engine Community / Corp Architecture Refactor

Status: **Single-residency end state (2026-07-04)** — the neutral
`api/core/plugin_api/kernel/di/shared/openclaw` layers + `manager/config/process`
have been hoisted INTO `community/` as the shared base (Phase B atomic switch);
the dead `corp/plugins/openclaw/` duplicate has been deleted (Phase A cleanup,
shared openclaw code now lives only in `community/plugins/openclaw/`); the community/corp
boundary is enforced by the AST ratchet `tests/architecture/test_community_corp_boundary.py` (stricter than the import-linter contract this tree replaced — see §note).
`engine/` now contains exactly two implementation namespaces: `community/`
(base + shared + community-only) and `corp/` (corp-only extension importing
`engine.community` freely).
Scope: `src/engine/src/engine`
Primary goal: single residency — every file lives in exactly one of `community/` or `corp/`; shared code lives only in `community/` and corp imports it; same-function-different-impl is resolved via Protocol + DI + `ENGINE_PROFILE`.

## 0. Phase 0 status (2026-07-02)

Adopted **Phase 0 only** (this doc + guardrails; no code moves yet).

**Foundation already in place (not built from scratch):**
- DI is already community/corp-split: `di/modules/infrastructure/{community,corp,test}/`.
- Profile model exists: `di/profile.py` + `di/profile_modules.py`; `ENGINE_PROFILE` default is community.
- OSS leakage arch tests exist: `tests/architecture/test_no_dima_in_oss_layers.py`, `test_no_internal_vendor_in_core_api.py` (scan set already includes `plugins/community`).

**Done in Phase 0:**
- This document committed as the authoritative design.
- New import-linter contract **`community-no-corp`** added (`.importlinter`): community impls (`plugins/community` + `engines/claude_code_community`) must not *directly* import corp impls (`plugins/prod` + `engines/{aicoding,hermes,claude_code}`). Direct-import ban (`allow_indirect_imports = True`) so the sanctioned `community.router -> engine.community.manager -> (all engines)` aggregation path stays legal. Green at **7 kept / 0 broken**.

**Deviations from the §4 target to reconcile in later phases (deferred):**
- `plugins/prod` is corp; **not yet renamed** to `corp/` (§11 / Phase 3-4).
- Community claude_code engine currently lives at `engines/claude_code_community/`; the §4 target is `community/engines/claude_code/` (Phase 4 move).
- Community plugins under `plugins/community/`; §4 target `community/plugins/` (Phase 3).
- **`export-exclude` still enumerates scattered internal paths**; §14 target is a single `corp/` line (after the moves).

**Out of scope here (separate pending decision):** the §21 Claude Code gateway placement. `src/engine/claude_code_gateway/` is currently **excluded** from the OSS export; whether to ship it in-repo (Option B) or publish separately (Option C) for a runnable OSS engine is deferred.

## 1. Background

The engine package is being prepared for an open-source architecture split. Today the codebase already has partial separation:

- `engine.plugins.community`: open-source or no-op implementations.
- `engine.plugins.prod`: internal/corp implementations.
- `engine.community.di.modules.infrastructure.community`: community DI bindings.
- `engine.community.di.modules.infrastructure.corp`: corp DI bindings.
- `ENGINE_PROFILE=community|corp|test`: runtime profile selector.
- `.github-export-exclude`: export-time exclusion of internal files.
- architecture tests that prevent internal vendor references from leaking into neutral layers.

However, the current structure mixes different concepts:

- `prod` actually means internal/corp, not merely production deployment.
- internal engines live under `engine.engines.*` beside public engines.
- community engines and plugins are partly under `engine.plugins.community` rather than under a top-level community namespace.
- export exclusions must list many scattered internal paths.

This document proposes a more explicit and stable structure based on top-level `engine.community` and `engine.corp` namespaces.

## 2. Goals

1. Make it obvious which implementation code is open-source and which is internal.
2. Keep `api`, `core`, `plugin_api`, `kernel`, and `manager` shared — they live INSIDE `community/` as the base layer, and `corp` imports them.
3. **Single residency.** Every file lives in exactly one of `community/` or `corp/`: shared code (identical behavior across profiles) lives only in `community/` — one copy — and corp imports it; community-only code lives only in `community/`; corp-only code lives only in `corp/`; "same function, different impl" is expressed as a Protocol in `community/plugin_api/` (or `community/core/`) with one community impl and one corp impl, bound per profile via `di/profile_modules`. Corp is a community extension and may import `engine.community` freely.
4. Ensure community builds can import and start without internal dependencies.
5. Ensure internal implementations cannot accidentally leak into open-source layers.
6. Make GitHub export and package build rules simpler.
7. Keep profile-specific imports branch-local and explicit.
8. Preserve runtime behavior through the migration.

## 3. Non-goals

1. Do not duplicate `api` and `core` logic into two parallel trees. (Target: the neutral `api`/`core`/`plugin_api`/`kernel`/`di` layers live INSIDE `community/` as the shared base — see §4. They are not a third top-level bucket alongside community/corp; they live inside community and corp imports them.)
2. Do not change external HTTP/WebSocket protocols as part of the directory refactor.
3. Do not rewrite all engine implementations in one large atomic change.
4. Do not make community code depend on internal SDKs, internal domains, MOSN-only services, DIMA, ZeroCheck, or DingTalk.
5. Do not use `prod` as the long-term name for internal/corp code.

## 4. Target package layout

Target structure:

```text
engine/
├── community/              # base: neutral shared layer + shared code + community-only
│   ├── api/                # HTTP/WS front door
│   ├── core/               # domain services + ACL adapters
│   ├── plugin_api/         # Port Protocols + DTOs
│   ├── kernel/             # bottom primitives
│   ├── di/                 # composition root: container, profile_modules, modules/
│   ├── shared/             # shared utilities
│   ├── openclaw/           # shared openclaw neutral pieces
│   ├── manager.py
│   ├── config.py
│   ├── process.py
│   ├── internal_defaults.py
│   ├── conftest.py
│   ├── engines/            # openclaw (shared) + claude_code (community-only)
│   ├── plugins/            # auth_gate/notification/work_item (community base impls)
│   │                       # + openclaw (shared) + claude_code (community-only)
│   └── local/              # test doubles
│
└── corp/                   # extension: corp-only, imports community to reuse base
    ├── engines/            # aicoding/hermes/claude_code (corp-only impls)
    ├── plugins/            # auth_gate(ZeroCheck)/notification(DingTalk)/work_item(DIMA)/dima
    ├── di/                 # corp infrastructure DI modules
    └── transport/          # corp-only transport
```

### Layer meaning

| Namespace | Meaning | OSS shipped? |
| --- | --- | --- |
| `engine.community.kernel` | Bottom-layer primitives. No internal imports. | Yes |
| `engine.community.plugin_api` | Plugin Protocols, DTOs, facade interfaces. | Yes |
| `engine.community.core` | Profile-neutral domain logic and abstract service ports. | Yes |
| `engine.community.api` | Profile-neutral HTTP/WS adapters. | Yes |
| `engine.community.di` | Composition root, profile detection, and controlled implementation loading. | Yes |
| `engine.community` | Open-source engine/plugin implementations and no-op fallbacks. | Yes |
| `engine.corp` | Internal SDK/product integrations and internal-only engines. | No |

## 5. Architectural principle

The split is by implementation, not by product layer. The neutral layers
(`api`, `core`, `plugin_api`, `kernel`, `di`, `shared`, `openclaw`, `manager`,
`config`, `process`) live INSIDE `community/` as the shared base. `corp/` is an
extension that imports `engine.community.*` to reuse the base; it does not copy
the base. Profile-specific behavior is expressed through Protocols (in
`community/plugin_api/`) and DI bindings selected by `ENGINE_PROFILE`, never
through copied layers.

## 6. Dependency direction

Expected dependencies:

```text
api ───────▶ core ───────▶ plugin_api ───────▶ kernel
 │            │                 │
 │            │                 └────────────▶ kernel
 │            └──────────────────────────────▶ kernel
 │
 di ───────▶ api/core/plugin_api/community/corp

community ─▶ plugin_api/kernel/shared-neutral utilities
corp      ─▶ plugin_api/kernel/shared-neutral utilities and internal SDKs
```

Rules:

1. `api`, `core`, `plugin_api`, and `kernel` must not import `engine.community` or `engine.corp`.
2. `engine.community` must not import `engine.corp`.
3. `engine.corp` MAY import `engine.community` freely — corp is a community extension and reuses the shared base. (This replaces the earlier "corp should not import community" final-state goal.) The one-directional constraint that remains: `engine.community` must NOT import `engine.corp`. Shared code lives only in `community/`; corp imports it, never copies it.
4. `engine.community.di.profile_modules` is the only normal place that may import both `engine.community` and `engine.corp` — it selects corp vs community infrastructure modules by profile.
5. Runtime engine registration/loading imports profile-specific implementations through `engine.community.engines.__init__` (the registry loader), gated on `ENGINE_PROFILE`.
6. Internal imports must be branch-local: the `corp` package must only be imported when `ENGINE_PROFILE=corp` or an explicit internal test requires it.

## 6.1 Single-residency rules (authoritative)

Every file has exactly one residency.

| Code nature | Sole residency | How the other profile uses it |
|---|---|---|
| Shared (identical behavior across profiles) | `community/` — one copy | corp imports `engine.community...` directly |
| Community-only | `community/` — one copy | corp does not use it |
| Corp-only | `corp/` — one copy | — |
| Same function, different impl | `community/` (base impl) + `corp/` (corp impl) — one copy each | Protocol lives in `community/plugin_api/` (or `community/core/`); `community/di/profile_modules` binds the right impl per profile |

Examples:
- **Shared** (one copy in community): `community/plugins/openclaw/` (used by both profiles' OpenClaw engine). The corp duplicate was deleted in Phase A cleanup.
- **Same function, different impl** (one copy each, DI-selected): `OpenClawClientProxy` — community impl returns 4004; corp impl forwards to the real gateway; both bound via their respective `di/infrastructure/openclaw_client_proxy.py` modules selected by `profile_modules`.
- **Corp-only**: `corp/plugins/dima/`, `corp/engines/aicoding/`, `corp/engines/hermes/`.

## 7. Runtime profile model

Keep the existing profile vocabulary:

```text
ENGINE_PROFILE=community | corp | test
```

Meaning:

| Profile | Meaning |
| --- | --- |
| `community` | Default open-source profile. Loads community engines/plugins only. |
| `corp` | Internal profile. Loads corp engines/plugins and may use internal SDKs/services. |
| `test` | Deterministic test profile. Should prefer community/no-op implementations unless a test explicitly exercises corp behavior. |

`community` must be the default when no environment variable is set.

`singlebox`, if inherited from another service as `DEPLOY_PROFILE=singlebox`, should continue to alias to `community` unless the engine later defines a dedicated singlebox profile.

## 8. Profile-specific DI

Profile-specific DI should be selected in one matrix-like selector, currently `engine.community.di.profile_modules`.

Target shape:

```python
from engine.community.di.profile import EngineProfile


def modules_for(profile: EngineProfile):
    if profile is EngineProfile.COMMUNITY:
        from engine.community.di.auth_gate import CommunityAuthGateModule
        from engine.community.di.notification import CommunityNotificationModule
        from engine.community.di.work_item import CommunityWorkItemModule

        return [
            CommunityAuthGateModule(),
            CommunityNotificationModule(),
            CommunityWorkItemModule(),
        ]

    if profile is EngineProfile.CORP:
        from engine.corp.di.auth_gate import CorpAuthGateModule
        from engine.corp.di.notification import CorpNotificationModule
        from engine.corp.di.work_item import CorpWorkItemModule

        return [
            CorpAuthGateModule(),
            CorpNotificationModule(),
            CorpWorkItemModule(),
        ]

    if profile is EngineProfile.TEST:
        from engine.community.di.auth_gate import CommunityAuthGateModule
        from engine.community.di.notification import CommunityNotificationModule
        from engine.community.di.work_item import CommunityWorkItemModule

        return [
            CommunityAuthGateModule(),
            CommunityNotificationModule(),
            CommunityWorkItemModule(),
        ]

    raise ValueError(f"Unhandled engine profile: {profile!r}")
```

The important property is not the exact code, but the import behavior:

- community/test paths do not import `engine.corp`.
- corp paths import `engine.corp` only inside the corp branch.
- importing `engine.community.di.profile_modules` alone must not require internal dependencies.

## 9. Engine registration/loading

The engine registry should load implementations by profile.

During migration, the existing `engine.engines.__init__` may continue to own this responsibility. Long term, prefer moving the responsibility into a dedicated loader:

```text
engine/di/engine_loader.py
```

Target behavior:

```python
if profile is EngineProfile.COMMUNITY:
    import engine.community.engines.openclaw
    import engine.community.engines.claude_code

elif profile is EngineProfile.CORP:
    import engine.community.engines.openclaw  # if shared public engine remains available internally
    import engine.corp.engines.aicoding
    import engine.corp.engines.hermes
    import engine.corp.engines.claude_code
```

Note: OpenClaw is shared code — there is ONE engine impl at
`engine.community.engines.openclaw` and ONE plugin impl at
`engine.community.plugins.openclaw`. The corp profile imports the same community
impls; the old `engine.corp.plugins.openclaw` duplicate was deleted in Phase A
cleanup (dead code, zero external importers). This is the canonical example of
the single-residency rule.

Constraints:

1. Community startup must not import internal engines.
2. The same engine name must not be registered twice in the same process.
3. If both community and corp implementations use the same canonical name, profile selection must guarantee mutual exclusion.
4. Optional internal engines should fail clearly when requested in community builds.
5. Registry import side effects should be limited to registration, not service startup.

## 10. Placement rules

Use the following rules to decide where a file belongs.

| File/content type | Location |
| --- | --- |
| Protocol, DTO, abstract service, capability model | `engine.community.plugin_api`, `engine.community.core`, or `engine.community.kernel` |
| Generic HTTP router or WebSocket transport | `engine.community.api` |
| EngineManager, registry, neutral lifecycle orchestration | `engine.community.manager` or `engine.community.core.engine` |
| Open-source runnable implementation | `engine.community.plugins` or `engine.community.engines` |
| No-op community fallback | `engine.community.plugins` |
| Internal product integration | `engine.corp.plugins` |
| Internal-only engine implementation | `engine.corp.engines` |
| Internal SDK client wrapper | `engine.corp.plugins` or `engine.corp.shared` if needed |
| Profile selection logic | `engine.community.di` |
| Tests for neutral behavior | `engine.tests` or colocated neutral tests |
| Tests for community implementation | `engine.community.*.tests` or `engine.tests/community` |
| Tests for corp implementation | `engine.corp.*.tests` or `engine.tests/corp`, excluded from OSS export |

Quick check:

> If a file contains internal domains, internal SDKs, internal product names, company auth, DIMA, ZeroCheck, DingTalk, or MOSN-only assumptions, it belongs in `engine.corp` or must be rewritten behind a neutral Protocol.

## 11. Naming rules

1. Use `community` for open-source implementations.
2. Use `corp` for internal implementations.
3. Do not use `prod` as a namespace for internal code.
4. `prod` may remain as a runtime deployment environment value, but not as a package name for internal implementations.
5. Avoid `enterprise` for this boundary because it can mean customer deployment rather than internal company-only code.

## 12. Import-linter contracts

Add or evolve import-linter contracts to lock the boundary.

Recommended contracts:

```ini
[importlinter:contract:neutral-no-profile-impls]
name = neutral layers do not import community/corp implementations
type = forbidden
source_modules =
    engine.community.api
    engine.community.core
    engine.community.plugin_api
    engine.community.kernel
forbidden_modules =
    engine.community
    engine.corp
allow_indirect_imports = True
```

`allow_indirect_imports = True` may be needed during migration if neutral code reaches implementations through sanctioned registry/manager paths. The final target should minimize even indirect implementation reachability outside the composition root.

```ini
[importlinter:contract:community-no-corp]
name = community implementation does not import corp implementation
type = forbidden
source_modules =
    engine.community
forbidden_modules =
    engine.corp
```

Optional final-state contract:

```ini
[importlinter:contract:corp-no-community]
name = corp implementation does not import community implementation
type = forbidden
source_modules =
    engine.corp
forbidden_modules =
    engine.community
```

This `corp-no-community` contract is **deliberately NOT added**. The
single-residency rule (§2 goal 3, §6.1) requires corp to import community to
reuse shared code, so a corp→community ban would contradict the target. Only
the reverse `community-no-corp` contract is enforced.

Keep the existing contracts for:

- no concrete implementation imports from `core`/`api`;
- plugin leaf behavior;
- no HTTP framework imports in `core`/`plugin_api`;
- `kernel` as the bottom layer.

Update their forbidden modules from `engine.plugins` / `engine.engines` to the new namespaces as migration progresses.

## 13. OSS leakage guards

Keep and expand source scanning tests.

Recommended checks:

1. Neutral layers contain no internal domains or internal package names.
2. `engine.community` contains no internal domains or internal package names.
3. `engine.community` contains no references to DIMA/ZeroCheck/internal auth products unless they appear only in deny-list tests or explanatory docs.
4. GitHub export contains no `engine.corp` directory.
5. Community installation can import `engine.community.api.app` and initialize the DI container without `engine.corp` present.
6. Community engine registry does not register corp-only engines.

Example target scan set:

```python
OSS_DIRS = [
    "api",
    "core",
    "di",
    "plugin_api",
    "kernel",
    "community",
]
```

The scan should exclude test fixtures only when the fixture intentionally asserts error behavior; prefer keeping fixtures clean as well.

## 14. Packaging and export

The desired export rule is simple:

```text
src/engine/src/engine/corp/
```

After migration, `.github-export-exclude` should no longer need to enumerate scattered internal implementation paths such as:

```text
src/engine/src/engine/plugins/prod/
src/engine/src/engine/engines/aicoding/
src/engine/src/engine/engines/hermes/
src/engine/src/engine/engines/claude_code/
```

Package build should also exclude `engine.corp` for community/open-source distributions.

Internal builds may include both `engine.community` and `engine.corp`.

## 15. Migration plan

### Phase 0: Document and guard

- Add this design document.
- Add initial import-linter contracts or architecture tests that prevent new `core/api/plugin_api/kernel` imports from `engine.corp` or `engine.community`.
- Keep existing runtime behavior unchanged.

### Phase 1: Create namespaces

Create empty packages:

```text
engine/community/__init__.py
engine/community/engines/__init__.py
engine/community/plugins/__init__.py
engine/community/di/__init__.py
engine/corp/__init__.py
engine/corp/engines/__init__.py
engine/corp/plugins/__init__.py
engine/corp/di/__init__.py
```

Add package docstrings explaining the boundary.

### Phase 2: Move DI bindings

Move:

```text
engine.community.di.modules.infrastructure.community.* -> engine.community.di.*
engine.community.di.modules.infrastructure.corp.*      -> engine.corp.di.*
```

Update `engine.community.di.profile_modules`.

Optionally leave compatibility shims in the old paths during migration.

### Phase 3: Move plugins

Move:

```text
engine.plugins.community.* -> engine.community.plugins.*
engine.plugins.prod.*      -> engine.corp.plugins.*
```

Update imports in DI modules and tests.

Keep old import shims only if necessary and mark them deprecated.

### Phase 4: Move engines

Move internal-only engines:

```text
engine.engines.aicoding    -> engine.corp.engines.aicoding
engine.engines.hermes      -> engine.corp.engines.hermes
engine.engines.claude_code -> engine.corp.engines.claude_code
```

Move community engines:

```text
engine.plugins.claude_code -> engine.community.plugins.claude_code (shared profile-neutral impl; corp reuses the same source, see §11 naming rule)
engine.engines.openclaw              -> engine.community.engines.openclaw, if OpenClaw is public
```

Update registry loading to import from the new namespaces by profile.

### Phase 5: Remove legacy paths

Remove or hard-deprecate:

```text
engine.plugins.prod
engine.plugins.community
engine.engines.aicoding
engine.engines.hermes
engine.engines.claude_code
```

`engine.engines` may remain only as a registry loader/facade, or be replaced by `engine.community.di.engine_loader`.

### Phase 6: Simplify export and build rules

- Replace scattered export exclusions with `engine/corp/`.
- Ensure community package build excludes `engine.corp`.
- Add CI check that an OSS export does not contain `engine.corp`.

## 16. Compatibility guidance

During migration, compatibility shims are acceptable when they reduce risk.

Example:

```python
# engine/plugins/community/notification/logger_impl.py
from engine.community.plugins.notification.logger_impl import LoggerNotificationService

__all__ = ["LoggerNotificationService"]
```

Rules for shims:

1. Shims must not introduce new implementation logic.
2. Shims must not import corp code from community paths.
3. Shims should include a deprecation comment.
4. Shims should be removed after callers have migrated.
5. Corp shims must be excluded from OSS export if they expose internal paths.

## 17. Testing strategy

Minimum tests after each migration phase:

1. `ENGINE_PROFILE=community` imports `engine.community.api.app` without importing `engine.corp`.
2. `ENGINE_PROFILE=community` registers only community engines.
3. `ENGINE_PROFILE=corp` registers corp engines and expected shared community engines.
4. `ENGINE_PROFILE=test` uses deterministic no-op/community services.
5. Import-linter contracts pass.
6. OSS leakage scan passes.
7. Existing HTTP/WS contract tests continue to pass.
8. Existing engine manager switching tests continue to pass.

Recommended focused tests:

```text
engine/tests/architecture/test_no_corp_imports_in_neutral_layers.py
engine/tests/architecture/test_community_no_corp_imports.py
engine/tests/di/test_profile_modules_do_not_import_corp_for_community.py
engine/tests/di/test_engine_loader_by_profile.py
```

## 18. Review checklist

For any PR touching engine architecture, check:

- [ ] Does any neutral layer import `engine.corp` or `engine.community` directly?
- [ ] Does any community file import `engine.corp`?
- [ ] Does any community file mention internal domains, internal SDKs, DIMA, ZeroCheck, DingTalk, or other internal products?
- [ ] Are corp imports branch-local behind `ENGINE_PROFILE=corp`?
- [ ] Does community startup work without corp files present?
- [ ] Are reusable pieces placed in neutral modules rather than copied between community and corp?
- [ ] Are `.github-export-exclude` and package build rules still correct?
- [ ] Are import-linter contracts and architecture tests updated with any path moves?

## 19. Open decisions

1. Whether `engine.engines` remains as the long-term registry loader or is replaced by `engine.community.di.engine_loader`.
2. Whether `engine.corp` may reuse `engine.community` implementations in the short term.
3. Whether OpenClaw should be moved immediately to `engine.community.engines.openclaw` or remain temporarily under `engine.engines.openclaw`.
4. Whether corp tests live under `engine.corp.*.tests` or `engine.tests/corp`.
5. Whether community and internal package builds share one `pyproject.toml` with exclusions or use separate internal build configuration.

## 20. Recommended end state

The recommended end state is:

- one shared neutral core;
- `engine.community` for open-source implementations;
- `engine.corp` for internal implementations;
- `ENGINE_PROFILE` as the only runtime selector;
- DI/engine loader as the only sanctioned cross-profile import location;
- import-linter and architecture tests enforcing the boundary;
- OSS export excluding exactly the `engine.corp` subtree plus non-code local config/secrets.

## 21. Claude Code gateway open-source boundary

`src/engine/claude_code_gateway` is the Node.js relay used by the community Claude Code engine path. The Python community implementation currently talks to it through WebSocket, with a default relay URL similar to:

```text
ws://127.0.0.1:18900
```

### Recommendation

If the project wants to open-source the Claude Code engine as a runnable feature, the gateway or an equivalent relay implementation should also be open-sourced.

Reason:

1. The Python engine side is only an adapter/client. Without the relay, the community Claude Code engine is not independently runnable.
2. Keeping the relay closed while opening the Python wrapper creates a misleading OSS feature: users can import the engine but cannot actually run the default path.
3. The gateway contains the protocol bridge, session handling, and process orchestration that define much of the Claude Code engine behavior.
4. Open-sourcing the relay makes community tests and bug reports reproducible.

### Target placement

Prefer moving the gateway under the community namespace or a community-owned sibling package:

Option A, inside the Python engine tree:

```text
src/engine/src/engine/community/engines/claude_code/       # Python engine/plugin code
src/engine/community/claude_code_gateway/                  # Node relay package
```

Option B, as a top-level package in the engine module:

```text
src/engine/claude_code_gateway/                            # Node relay package, OSS-shipped
src/engine/src/engine/community/engines/claude_code/       # Python engine/plugin code
```

Option C, separate repository/package:

```text
openclaw-claude-code-gateway/                              # published Node package
src/engine/src/engine/community/engines/claude_code/       # Python client depends on configured relay
```

For the current repo, Option B is the lowest-risk short-term choice because it avoids mixing Node packaging under the Python import package. Option C is the cleanest long-term choice if the gateway has an independent release lifecycle.

### What must be checked before open-sourcing the gateway

Before including `claude_code_gateway` in OSS export, audit and clean:

1. `node_modules/` must not be exported.
2. `logs/` must not be exported.
3. local runtime files, generated reports, caches, and lock IDs must not be exported.
4. internal domains, internal npm registries, internal package scopes, and internal author-only metadata must be removed or made neutral.
5. docs must not mention internal endpoints, internal deployment names, or private workflows.
6. license compatibility must be verified for direct dependencies.
7. package name, repository URL, and author metadata should be neutralized if needed.
8. tests should run using public dependencies only.
9. default ports and environment variables must be documented.
10. the gateway must not require company network or internal credentials.

### Export rules for the gateway

If open-sourced, keep:

```text
src/engine/claude_code_gateway/src/
src/engine/claude_code_gateway/public/
src/engine/claude_code_gateway/test/
src/engine/claude_code_gateway/docs/        # after internal-content audit
src/engine/claude_code_gateway/package.json
src/engine/claude_code_gateway/package-lock.json
src/engine/claude_code_gateway/tsconfig.json
src/engine/claude_code_gateway/README.md
src/engine/claude_code_gateway/LEGAL.md
```

Exclude:

```text
src/engine/claude_code_gateway/node_modules/
src/engine/claude_code_gateway/logs/
src/engine/claude_code_gateway/.env
src/engine/claude_code_gateway/.npmrc
src/engine/claude_code_gateway/dist/         # unless choosing to ship built artifacts
src/engine/claude_code_gateway/coverage/
src/engine/claude_code_gateway/.cache/
```

### If the gateway cannot be open-sourced

If legal or product constraints prevent open-sourcing `claude_code_gateway`, the Claude Code engine should not be advertised as a fully runnable OSS engine by default. In that case choose one of these designs:

1. Move Claude Code engine to `engine.corp` together with the gateway.
2. Keep only a neutral `ClaudeCodeRelayClient` Protocol and community stub that returns a clear unsupported/capability error.
3. Provide a documented extension point so users can implement their own relay.
4. Mark the feature as optional and disabled unless `CLAUDE_CODE_RELAY_URL` points to a user-provided compatible relay.

Do not leave the default community profile pointing to a relay implementation that is absent from the OSS export.

### Architectural constraint

The Python community Claude Code engine may depend on the relay protocol, but it must not depend on private relay implementation details. The boundary should be:

```text
engine.community.engines.claude_code  ──WebSocket/protocol──▶ claude_code_gateway
```

Not:

```text
engine.community.engines.claude_code  ──Python import/internal file access──▶ claude_code_gateway internals
```

This keeps the relay replaceable and allows the gateway to become either an in-repo OSS component or a separately published Node package.
