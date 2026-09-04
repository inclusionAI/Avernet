## CI Enforcement

This document defines the minimum enforcement mechanisms required to make the constitution operational.

### Goal

Architectural rules that are not checked will decay.  
Therefore, the repository must enforce structural integrity through automation wherever practical.

---

## A. Required CI Gates

At minimum, CI must enforce:

1. **Dependency boundary checks**
2. **Forbidden transport/framework usage in core**
3. **Restricted environment access**
4. **Configuration schema validation**
5. **Conformance test execution**
6. **Structural PR checklist completion**
7. **Pull request title convention**

---

## B. Dependency Boundary Checks

The repository must define and enforce allowed dependency directions between these roles:

- contracts
- core
- adapters
- plugin contracts
- plugin implementations
- composition roots
- tests

### Minimum required checks

- core must not depend on adapters
- core must not depend on concrete plugin implementations
- contracts must not depend on implementations
- plugin contracts must not depend on implementation modules
- adapters must not select implementations directly except in approved composition roots
- plugin implementations must not depend directly on sibling plugin implementations unless explicitly allowed

### Recommended tooling
- Python: `import-linter`, `deptry`, AST checks
- Go: custom package dependency checks, `golangci-lint` rules
- Rust: crate/module boundary checks via linting + workspace conventions
- TS/JS: ESLint import rules, dependency-cruiser
- Java/Kotlin: ArchUnit
- Polyglot: custom graph checks in CI

---

## C. Forbidden Dependencies in Core

Core must remain transport-agnostic.

### Core must not import:
- HTTP/web frameworks
- RPC frameworks
- request/response types
- transport-specific exception/status types

### Examples to forbid
- `fastapi`
- `flask`
- `starlette`
- `grpc`
- `HTTPException`
- web request/response objects
- protocol transport status/result objects

### Enforcement options
- linter banned import rules
- AST-based forbidden symbol checks
- grep-based CI checks as a baseline

---

## D. Environment Access Restrictions

Raw environment access must be limited to:
- config loading modules
- bootstrap/composition root modules
- test bootstrap or fixtures

### Forbidden elsewhere
- `os.getenv`
- `os.environ`
- equivalent language runtime environment access

### Why
This prevents scattered mode selection, hidden wiring logic, and environment-dependent behavior leaking into business code.

---

## E. Configuration Validation

Configuration must be validated against an explicit schema.

### Required behavior
- unknown keys fail validation
- required fields are enforced
- invalid enum/mode values fail validation
- startup fails early on invalid config

### Validation scope
Configuration must cover:
- implementation selection
- endpoints
- timeouts
- feature flags
- secret sources
- environment/mode selection

---

## F. Conformance / Contract Tests

Every declared Service API, Plugin API, and replaceable capability contract must map to conformance tests.

### CI requirements
- changed contracts trigger corresponding conformance tests
- protected branches should run all conformance suites or a validated equivalent
- new implementations are blocked from merging without contract coverage

### Test categories
- unit tests
- conformance / contract tests
- integration tests
- end-to-end tests

Conformance tests are mandatory for boundary contracts.

---

## G. Structural PR Gate

PRs affecting boundaries must include completed structural analysis.

### Required PR fields
- title matching `<type>(<scope>): <concise outcome>` with an allowed type from
  `feat`, `fix`, `refactor`, `docs`, `test`, `ci`, `build`, or `chore`; scope is
  required
- whether a contract changed
- if changed, what kind of contract
- affected consumers
- affected implementations
- compatibility status
- migration/deprecation plan if needed
- whether a waiver is required

### Enforcement
- PR template required
- `.github/workflows/pr-title.yml` validates every PR title and reports the
  stable `Validate PR title` status check
- protected-branch rulesets must require `Validate PR title`; the workflow
  result alone does not block merging unless the status check is required
- reviewer must confirm completion
- bots may reject PRs missing required sections

---

## H. Waiver Enforcement

Waivers are allowed only for explicit invariant violations.

### Required waiver properties
- linked from PR
- owner assigned
- review or expiry date present
- exact rule named
- removal plan documented

### Enforcement
- PRs claiming exceptions without waiver are rejected
- expired waivers trigger review failure or warning escalation

---

## I. Red-Flag Detection

CI should flag or fail on likely structural violations, including:

- transport framework imports in core
- concrete implementation imports outside composition roots or tests
- raw environment access outside config/bootstrap
- hardcoded external URLs or tokens
- contract changes without associated conformance updates
- new top-level package/directory without declared role

Not every detection must block immediately, but all should at least surface in review.

---

## J. Recommended Baseline Checks

Even before stronger tooling is installed, use simple fail-fast checks.

### 1. Forbid transport concerns in core
```bash
! grep -R "from fastapi import\|import fastapi\|HTTPException\|starlette\|grpc" src/core
```

### 2. Forbid env reads outside config/bootstrap
```bash
! grep -R "os.getenv\|os.environ" src --exclude-dir=config --exclude-dir=bootstrap --exclude-dir=tests
```

### 3. Forbid direct plugin imports outside approved modules
```bash
! grep -R "from .*plugins\|import .*plugins" src/core src/adapters src/contracts
```

### 4. Detect hardcoded URLs
```bash
! grep -R "https\?://" src/core src/adapters src/plugins
```

These are crude but useful until AST or dependency-graph checks are in place.

---

## K. Example Python `import-linter` Rules

```ini
[importlinter]
root_package = your_project

[importlinter:contract:core_no_adapters]
name = core must not import adapters
type = forbidden
source_modules =
    your_project.core
forbidden_modules =
    your_project.adapters

[importlinter:contract:core_no_plugins]
name = core must not import plugin implementations
type = forbidden
source_modules =
    your_project.core
forbidden_modules =
    your_project.plugins

[importlinter:contract:contracts_no_plugins]
name = contracts must not import plugin implementations
type = forbidden
source_modules =
    your_project.contracts
    your_project.plugin_api
forbidden_modules =
    your_project.plugins

[importlinter:contract:adapters_no_plugins]
name = adapters must not import plugins directly
type = forbidden
source_modules =
    your_project.adapters
forbidden_modules =
    your_project.plugins
```

If bootstrap or composition-root modules need exceptions, place them in a separate package and exempt them explicitly.

---

## L. Suggested Enforcement Rollout

### Phase 1 — Immediate
- adopt PR template
- add grep-based anti-drift checks
- define composition roots
- define architectural role map

### Phase 2 — Near-term
- add dependency boundary tooling
- add config schema validation gate
- add conformance test suite mapping
- add waiver process

### Phase 3 — Strong enforcement
- AST-based forbidden import/symbol checks
- changed-contract detection
- auto-labeled structural PRs
- automated expiry reminders for waivers

---

## M. Compliance Standard

A repository may claim compliance with the constitution only if it has at least:

- documented architectural roles
- declared composition roots
- boundary checks in CI
- validated configuration
- conformance tests for boundary contracts
- structural PR review process
- waiver mechanism

Without these, compliance is aspirational rather than enforced.
