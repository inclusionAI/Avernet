## Gateways Arch Test Rules Manifest

Maps the 25 rules from `docs/arch/arch.rules.md` to test coverage
in `tests/architecture/`.  Only rules applicable to the gateway module
are listed; rules that are not yet relevant (infra, sandbox, etc.) are
documented with a gap rationale.

### Enforcement mechanisms

| Mechanism  | What it catches                                     |
|------------|-----------------------------------------------------|
| pytestarch | Module-level imports between architectural layers   |
| AST scan   | Function-body imports, env access, patterns         |
| Subprocess | External tool invocation (ruff, mypy)               |
| Baseline   | Tracks known violation count — fail on increase     |

### Rule → Test Coverage

| Rule | Summary                          | Test(s)                                                                      | Mechanism  | Status    | Gap / Note                                                                  |
|------|----------------------------------|------------------------------------------------------------------------------|------------|-----------|-----------------------------------------------------------------------------|
| 1    | Contracts are authoritative      | —                                                                            | —          | Not yet   | No contract spec docs exist yet. Enforced in review.                        |
| 2    | Terminology is canonical         | —                                                                            | —          | Not yet   | Enforced in review. No machine check.                                       |
| 3    | Service/Plugin APIs distinct     | `test_contract_rules.py`                                                     | pytestarch | ✅ Active | `api/` must not import `spi/` and vice versa.                               |
| 4    | Interfaces mean what specs define| —                                                                            | —          | Not yet   | No spec docs. Enforced in review.                                           |
| 5    | Contracts separate from impls    | `test_contract_rules.py`, `test_layer_rules.py`                              | pytestarch | ✅ Active | Contract layers must not import `core/`, `plugins/`.                        |
| 6    | Layer boundaries enforced        | `test_layer_rules.py`                                                        | pytestarch | ✅ Active | All 6 layers have defined ban rules.                                        |
| 7    | Core is transport-agnostic       | `test_core_rules.py`                                                         | pytestarch + AST | ✅ Active | Bans `adapters/` imports + transport framework AST scan.               |
| 8    | Directory org matches roles      | `test_layer_rules.py` (indirect)                                             | pytestarch | ✅ Active | Layer mapping built into ban rules.                                         |
| 9    | Functions/files single purpose   | —                                                                            | —          | Deferred  | Codebase too small for fat-function heuristics. Add later.                  |
| 10   | Component types declared/swappable | —                                                                              | —          | Not yet   | Plugin system is minimal. Add when more plug-ins exist.                     |
| 11   | Plugin lifecycle uniform         | —                                                                            | —          | Not yet   | Lifecycle is basic instantiation. Add when lifecycle phases are defined.    |
| 12   | Cross-cutting via hooks          | `test_protocol_exports.py`, `test_all_exports_valid.py`                      | AST + importlib | ✅ Active | Protocol `__all__` compliance. Hooks pattern not yet used.               |
| 13   | Plugin isolation tiers           | —                                                                            | —          | Not yet   | No isolation tiers defined for gateway plugins.                             |
| 14   | Configuration drives all wiring  | `test_core_rules.py` (bootstrap ban)        | pytestarch | ✅ Active | Env access restricted. Bootstrap import bans enforced.                |
| 15   | Dependency auditor mindset       | —                                                                            | —          | Not yet   | Enforced in review.                                                         |
| 16   | Changes propagate                | —                                                                            | —          | Not yet   | Enforced in PR reviews.                                                     |
| 17   | Stable vs flexible distinction   | —                                                                            | —          | Not yet   | Enforced in review.                                                         |
| 18   | Conflicts resolved explicitly    | —                                                                            | —          | Not yet   | Enforced via PR reviews + waiver log.                                       |
| 19   | Abstract after two examples      | —                                                                            | —          | Not yet   | Enforced in review.                                                         |
| 20   | Single-box development first     | —                                                                            | —          | Not yet   | All gateway plugins have local/bare variants. Enforced by design.           |
| 21   | Testing isolation implementations| —                                                                            | —          | Not yet   | Local plugin impls exist. Conformance tests deferred.                       |
| 22   | Context boundaries are explicit  | `test_structure_rules.py` (module docstrings)                                | AST scan   | ⚠️ Warning | Warns on missing `__init__.py` docstrings.                                  |
| 23   | Patterns are cataloged           | —                                                                            | —          | Not yet   | No pattern catalog exists.                                                  |
| 24   | Architecture supports incremental changes | —                                                                        | —          | Not yet   | Enforced by design.                                                         |
| 25   | Protocols have contract tests    | `test_structure_rules.py` (coverage check), `test_protocol_exports.py`       | AST scan + importlib | ⚠️ Warning | Warns on uncovered Protocols. `_EXEMPT_PROTOCOLS` allowed.      |

### Additional Enforcement (not tied to single rule)

| Test file                                     | What it enforces                                                           | Mechanism  |
|-----------------------------------------------|----------------------------------------------------------------------------|------------|
| `test_no_private_imports.py`                  | No absolute imports of `_module` private modules                           | AST scan   |
| `test_no_executable_private_modules.py`       | Private modules must not have `__main__` blocks                            | AST scan   |
| `test_no_bootstrap_get_container_in_all.py`   | `PluginAccessor` must not leak outside `bootstrap/` and `config/`          | AST scan   |
| `test_ruff_lint_rules.py`                     | `ruff check` + `ruff format --check` pass as CI gates                     | Subprocess |

### Known Tech Debt

_None yet — this is the initial arch test setup.  Add entries here as
violations are discovered and tracked with baseline patterns._

### Waiver Log

_No waivers have been issued yet._

**Waiver template:**

```
- **Rule**: [Number]
- **Reason**: [Why the invariant is intentionally violated]
- **Risk**: [What risk this introduces]
- **Compensating controls**: [How risk is mitigated]
- **Owner**: [Team/member]
- **Expiry / Review date**: [Date]
```

### Cross-Reference Audit

- Architecture constitution: `docs/arch/arch.rules.md`
- CI enforcement: `docs/arch/ci.enforce.md`
- Context boundary format: `docs/arch/context-boundary-format.md`
- Protocol contract tests: `docs/arch/protocol-contract-tests.md`