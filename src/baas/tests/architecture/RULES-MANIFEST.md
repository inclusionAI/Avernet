# Architecture Rules Manifest

**Source**: `../../docs/arch/arch.rules.md` (Microkernel Architecture Constitution v3.1, relative to this file — also reachable as `docs/arch/arch.rules.md` from monorepo root)  
**Purpose**: Maps each constitutional rule to its test coverage, enforcement mechanism, and gap rationale.  
**Updated**: 2026-06-26

---

## Legend

| Column | Meaning |
|--------|---------|
| Rule | Rule number and classification (I=Invariant, P=Policy, G=Guideline) |
| Testable | ✅ = fully automatable, 🔶 = partially automatable, ❌ = human review only |
| Covered | ✅ = enforced by automated test, 🔶 = partially covered, ❌ = not covered |
| File | Test file(s) that enforce this rule, or reason why not covered |
| Notes | Caveats, known gaps, pre-existing violations |

---

## Part I: Foundation — Interfaces & Contracts

| # | Rule | Classification | Testable | Covered | File / Reason | Notes |
|---|------|----------------|----------|---------|---------------|-------|
| 1 | The API Specification Is the Single Authority | I | 🔶 | ❌ | Human review — semantic drift detection | Conformance tests (Rule 25) are partial coverage |
| 2 | Define and Use Terms Consistently | I | ❌ | ❌ | Human review — canonical term usage | CLAUDE.md conventions provide guidance |
| 3 | Distinguish Service APIs from Plugin APIs | I | ✅ | ✅ `test_contract_rules.py` | pytestarch: api ↔ spi mutual import ban `test_api_does_not_import_spi` | — |
| 4 | Interfaces Mean Only What Their Specs Define | I | ❌ | ❌ | Human review — semantic contract adherence | — |
| 5 | Contracts Separate from Implementations | I | ✅ | ✅ `test_contract_rules.py` + `test_protocol_exports.py` | pytestarch: api/spi must not import core/plugins/infra; 6 tests all passing; AST: Protocol `__all__` exports cleared | Known debt: 22 core files still import infra (see waiver log) |
| 6 | Architectural Layers Constrain and Are Enforced | I | ✅ | ✅ `test_layer_rules.py` | Layered import bans per module role; 7 layer tests all passing | Function-body lazy imports not caught |

---

## Part II: Architecture — Layers & Boundaries

| # | Rule | Classification | Testable | Covered | File / Reason | Notes |
|---|------|----------------|----------|---------|---------------|-------|
| 7 | Core APIs Are Library-Style; Delivery Is a Thin Adapter | I | ✅ | 🔶 `test_core_rules.py` + `test_adapter_thinness.py` | pytestarch: adapters not imported in core ✅; AST: transport framework scan ✅ (4 known debt files excluded); AST: domain-logic patterns in adapters ✅ (5 known violations exempted) | 5 known violations logged in waiver: `bot_invocation_router.py` (orchestration), `open_api/dependencies.py` (auth policy), `routers/session_router.py` (session state), `websocket/local_management_ws.py` (asyncio), duplicate `_filter_headers` |
| 8 | Directory Organization Matches Architectural Roles | P | ✅ | ❌ | Not yet automated — AST structural check planned | Ambiguous roles are acceptable if documented |
| 9 | Functions and Files Serve Single Purposes | G | 🔶 | 🔶 `test_function_boundaries.py` (new) | AST heuristic: file size (>500 lines) + mixed-import detection | Warnings only (Guideline); `paas_facade_router.py` (1067) and `bot_health_checker_router.py` (524) flagged |
| 10 | Component Types Are Explicitly Declared and Swappable | I | 🔶 | 🔶 `test_contract_rules.py` + `test_plugin_rules.py` (extended) | pytestarch concrete impl detection; AST: SPI→plugin method mapping ✅ | `ConnectionProvider` exempted (sub-contract); capability registry TBD |

---

## Part III: Plugin System — Design & Lifecycle

| # | Rule | Classification | Testable | Covered | File / Reason | Notes |
|---|------|----------------|----------|---------|---------------|-------|
| 11 | The Plugin Lifecycle Is Uniform and Enforced | I | ✅ | 🔶 `test_plugin_rules.py` (extended) | AST: plugins implement SPI lifecycle methods; checks `close()` on sandbox/data plugins; SPI→plugin method mapping ✅ | `ConnectionProvider` sub-contract exempted; `start()`/`stop()` only meaningful for scheduler-like plugins (Tier 2) |
| 12 | Plugin Hooks Enable Cross-Cutting Concerns | I | 🔶 | ❌ | AST pattern detection for auth/permission not going through hooks | Complex; requires call-graph analysis. No `observability/` dir exists |
| 13 | Every Plugin Matches Its Isolation Tier | I | 🔶 | ❌ | Requires plugin declaration metadata | No declaration standard exists yet |
| 14 | Configuration Drives All Wiring | I | ✅ | 🔶 `test_core_rules.py` + `test_env_import_regression.py` + `test_bootstrap_wiring.py` (new) | AST: env-check regression tracker (baseline 21 core files, fails on increase); AST: env-branching call-site scan (warns on core/api/spi) | `infra/config/` still active (143 lines); not yet migrated to `bootstrap/`; 21 tracked debt files in core/ |

---

## Part IV: Dependencies & Configuration

| # | Rule | Classification | Testable | Covered | File / Reason | Notes |
|---|------|----------------|----------|---------|---------------|-------|
| 15 | Think Like a Dependency Auditor | P | ❌ | 🔶 `test_plugin_isolation.py` (new) | pytestarch: cross-plugin import ban (forward-guard — zero violations, prevents regression) | Still a human-review mindset/culture rule; plugin isolation is now structurally enforced |
| 16 | Changes Propagate | I | ❌ | ❌ | PR process — no code-level enforcement | Could add git-hook in future |
| 17 | Distinguish What Is Flexible from What Is Constrained | I | 🔶 | ❌ | Requires surface metadata annotations | — |
| 18 | Resolve Conflicts Explicitly | I | ❌ | ❌ | Process rule — human judgment | — |
| 19 | Abstract After Two Examples, Not Before | G | ❌ | ❌ | Judgment-based review heuristic | Exception: cross-cutting hooks |

---

## Part V: Development Cycle Enablement

| # | Rule | Classification | Testable | Covered | File / Reason | Notes |
|---|------|----------------|----------|---------|---------------|-------|
| 20 | Single-Box Development First | P | ✅ | ❌ | CI integration test — local mode without network | Out of scope for architecture unit tests |
| 21 | Testing Isolation Implementations | P | 🔶 | ❌ | Contract → test double mapping | Requires contract registry |
| 22 | Context Boundaries Are Explicit | P | ✅ | ✅ `test_structure_rules.py` | AST: top-level module docstrings | All 7 top-level modules have context docstrings |
| 23 | Patterns Are Consistent and Cataloged | P | ❌ | ❌ | Culture/documentation rule | — |
| 24 | Architecture Supports Incremental Changes | I | ❌ | ❌ | Design principle — not code-enforceable | — |
| 25 | Protocols Have Self-Validating Contracts | I | ✅ | 🔶 `test_structure_rules.py` | AST: each Protocol in api/spi has test file (28/30 covered) | Conformance test quality not checked; 2 protocol files lack test coverage |

---

## Coverage Summary

| | Total | Automated (✅) | Partial (🔶) | Not Covered (❌) |
|---|-------|-----------|---------|---------|
| **Invariants** | 19 | 3 | 6 | 10 |
| **Policies** | 4 | 1 | 1 | 2 |
| **Guidelines** | 2 | 0 | 1 | 1 |
| **Total** | **25** | **4** | **8** | **13** |

---

## Waiver / Pre-Existing Debt Log

| Rule | Violation | Location | Reason | Waiver Date | Owner |
|------|-----------|----------|--------|-------------|-------|
| 5, 7 | core imports infra (`from secbaas.core.utils.env_utils import get_current_env`) | **23 files** across `core/service/` and `core/repository/` — 20 module-level, 3 lazy (function-body). Sub-packages: `infra.config` (1), `infra.k8s` (2 imports in 2 lazy files), `infra.paas` (1 lazy) | Environment detection not yet abstracted behind an SPI; needs refactoring. `infra.utils` resolved — moved to `core.utils`. | 2026-06-24 | TBD |
| 5, 7 | core imports web frameworks (`aiohttp`) | 4 remaining: `core/service/bot_run/_async_session_client.py`, `core/service/bot_run/_baas_service.py`, `core/service/bot_run/_claw_service.py`, `core/service/bcn/uplink/_uplink_client.py`; 3 `fastapi` imports cleaned | HTTP/WS clients not abstracted behind an SPI; needs refactoring | 2026-06-24 | TBD |
| 5, 7 | core imports infra (`from secbaas.core.utils.secret_utils import common_sm4_encrypt/decrypt`) | 2 files: `core/service/device_manage/_device_service.py`, `core/service/template_manage/_device_template_service.py`; also `core/service/paas/_factory.py` (lazy import) | Encryption not abstracted behind SPI; needs refactoring. `infra.utils` resolved — moved to `core.utils`. | 2026-06-24 | TBD |
| 11 | Missing lifecycle methods on some plugins | `close()` not implemented on identity, permission, lock, secret, bot plugins; `start()`/`stop()` only on scheduler + sandbox/arca/local_proc | Lifecycle protocol not yet standardized in `spi/` | 2026-06-24 | TBD |
| 8 | Directory structure not fully aligned with constitution | `domain/` dir removed, but `infra/` still has active code (6 py files, ~780 lines) across config/, k8s/; `_compat/` dir does not exist; no `observability/` dir | Phase 7-8 migration incomplete. `infra/utils/` removed — moved to `core/utils/`. | 2026-06-24 | TBD |
| 7 | Adapter thinness violation — orchestration logic in router | `core/service/...` session state marking + `asyncio.create_task` in `adapters/web/routers/bot_invocation_router.py` | Domain orchestration not yet abstracted behind service layer | 2026-06-26 | TBD |
| 7 | Adapter thinness violation — auth policy in adapter | `_normalize_bot_id`, `match_allowed_bots`, `validate_policy` in `adapters/web/routers/open_api/dependencies.py` | Authorization policy not yet extracted to dedicated module | 2026-06-26 | TBD |
| 7 | Adapter thinness violation — duplicate helper | `_filter_headers` and `HOP_BY_HOP_HEADERS` duplicated in `paas_facade_router.py` and `bot_http_router.py` | Should be extracted to shared utility | 2026-06-26 | TBD |
| 7 | Adapter thinness violation — session state mutations in router | `routers/session_router.py`: `.mark_running()`, `.mark_completed()`, `.mark_failed()` calls | Session lifecycle should be managed by service layer, not adapter | 2026-06-26 | TBD |
| 7 | Adapter thinness violation — websocket orchestration | `websocket/local_management_ws.py`: `asyncio.create_task()` concurrency management | Concurrency orchestration should be in service layer | 2026-06-26 | TBD |

---

## Test File Index

| File | Rules Enforced | Mechanism | Status |
|------|----------------|-----------|--------|
| `test_layer_rules.py` | 6 (layer import bans) | pytestarch | ✅ 7 tests passing |
| `test_no_private_imports.py` | Local convention (CLAUDE.md) | AST | ✅ Passing |
| `test_contract_rules.py` | 3, 5 (contract isolation) | pytestarch | ✅ 7 tests passing |
| `test_core_rules.py` | 7, 14 (transport-agnostic core, wiring) | pytestarch + AST | ✅ 3 tests passing; adapter thinness not checked |
| `test_structure_rules.py` | 22, 25 (context docs, conformance tests, import-linter gap) | AST | ✅ **4** tests passing (2 new: import-linter gap, file-size) |
| `test_plugin_rules.py` | 11 (plugin lifecycle, SPI→plugin method mapping) | AST | ✅ **5** tests passing (1 new: SPI method coverage) |
| `test_env_import_regression.py` | 14 watchdog (core→infra env-import regression) | AST | ✅ **5** tests passing (NEW in Phase 2) |
| `test_plugin_isolation.py` | 15 (cross-plugin import ban) | pytestarch | ✅ **2** tests passing (NEW in Phase 2) |
| `test_protocol_exports.py` | 5 complement (Protocol `__all__` exports) | AST | ✅ **3** tests passing (NEW in Phase 2) |
| `test_adapter_thinness.py` | 7 complement (domain-logic patterns in adapters) | AST + pytestarch | ✅ **4** tests passing (NEW in Phase 2) |
| `test_bootstrap_wiring.py` | 14 complement (env-branching call-site scan) | AST | ✅ **2** tests passing (NEW in Phase 2) |
| `test_function_boundaries.py` | 9 (file-size + mixed-import heuristics) | AST | ✅ **3** tests passing (NEW in Phase 2) |

---

## Cross-Reference Audit

All architecture doc references in the codebase (verified 2026-06-25):

| Referencing File | Reference | Resolution |
|---|---|---|
| `src/baas/tests/architecture/RULES-MANIFEST.md` | `../../docs/arch/arch.rules.md` | ✅ Resolves correctly from manifest location |
| `src/baas/tests/architecture/test_core_rules.py` | `RULES-MANIFEST.md` (3 references) | ✅ Same directory |
| `src/baas/tests/architecture/test_structure_rules.py` | `RULES-MANIFEST.md` (3 references) | ✅ Same directory |
| `src/baas/docs/micro-kernel-refactor.md` | `docs/arch/arch.rules.md` | ⚠ Does NOT resolve from `src/baas/docs/` — should be `../../docs/arch/arch.rules.md` |
| `src/baas/docs/micro-kernel-refactor-compliance-audit.md` | `docs/arch/arch.rules.md` (3 references) | ⚠ Does NOT resolve from `src/baas/docs/` — should be `../../docs/arch/arch.rules.md` |
| `src/baas/openspec/changes/add-arch-rules-tests/` | `arch.rules.md` (3 references) | ⚠ Relative paths may not resolve; depends on working directory |

---

## Known Issues Discovered During Audit (2026-06-25)

### Issue 1: `core/` imports `infra/` at scale (25 files, ~29 import sites)

Largest structural debt. Breaks Rules 5 and 7. `infra.utils.env_utils` alone accounts for 22 of 29 imports. Fix requires abstracting environment config behind an SPI.

### Issue 2: No `import-linter` or equivalent CI enforcement

Per Rule 6 (CI enforcement) and the refactor plan (Phase 2), an `import-linter` config should block illegal imports automatically. Currently only pytestarch tests run — which catch module-level imports but not lazy function-body imports.

### Issue 3: No `observability/` directory (Rule 12)

Cross-cutting concerns (auth, permission, logging, metrics) have no hook-plugin mechanism. Auth/permission checks scattered as direct service calls.

### Issue 4: `infra/` still contains 9 active Python files (1021 lines)

Phase 7-8 migration incomplete. Migrated items: `infra/dal/` → `core/repository/` + `plugins/database/`. Remaining: `infra/config/` (143 lines), `infra/k8s/` (283 lines), `infra/utils/` (595 lines). No `_compat/` dir exists.

### Issue 5: Arch doc path inconsistency

`src/baas/docs/micro-kernel-refactor.md` and `src/baas/docs/micro-kernel-refactor-compliance-audit.md` reference `docs/arch/arch.rules.md` which does not resolve from their location. Should use `../../docs/arch/arch.rules.md`.

### Issue 6: No cross-plugin import restrictions enforced (Rule 15) — ✅ RESOLVED

`test_plugin_isolation.py` now enforces cross-plugin import bans. Zero violations found (project was already compliant).

### Issue 7: Watcher test coverage for adapters importing core — ✅ RESOLVED

`test_adapter_thinness.py` now enforces adapter thinness via AST domain-logic pattern detection. 5 known pre-existing violations documented in waiver log.

---

**Status as of 2026-06-26**:  
- **47 architecture tests pass** (24 existing + 23 new from Phase 2)  
- 14 warnings: 4 known `aiohttp` debt files (excluded), adapter thinness known violations (5 files with domain logic), env-import regression (22 files in core/), env-branching in core (100+ call-sites), oversized routers (2 files, 1067+524 lines), 28 fat functions, missing import-linter, device_manage re-export, `dependency_injector.wiring` usage outside bootstrap/adapters  
- 22 `infra/` imports in `core/` remain as tracked debt item (was "25+", corrected to 22)  
- 9 active Python files (1021 lines) still in `infra/` awaiting Phase 7-8 migration  
- `import-linter`, `observability/`, `_compat/` do not exist yet  
- **Corrected stale entries from Phase 1 audit**: `CachePlugin` already has `close()` on Protocol and all implementations (was listed as missing); all 30 SPI protocol files have conformance tests (was listed as 2 gaps)