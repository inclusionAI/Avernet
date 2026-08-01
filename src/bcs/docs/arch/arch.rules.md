# Microkernel Architecture Constitution

**Version 3.1**  
**Purpose:** Preserve structural integrity under rapid human and LLM-assisted development.

## Mission

This constitution prevents architectural drift by making structural boundaries explicit, enforceable, and cheap to review. It governs contracts, dependency direction, plugin behavior, configuration-driven assembly, and change control.

## Scope

This constitution applies to:
- kernel/core modules
- Service APIs
- Plugin APIs
- delivery adapters
- plugin implementations
- configuration and composition roots
- structural tests and verification

This constitution does not prescribe:
- domain modeling style
- naming unrelated to architecture
- language-specific syntax except where project supplements define it

## Rule Classifications

Each rule is classified as:

- **Invariant** — mandatory; violations require explicit waiver
- **Policy** — strong default; deviations require justification
- **Guideline** — recommended practice; not a structural violation by itself

Unless otherwise stated, rules below are **Invariants**.

---

# Part I: Foundation — Interfaces & Contracts

## 1. The API Specification Is the Single Authority
**Classification:** Invariant

**Rule**  
All inter-component behavior must be governed by explicit contract definitions. For interface behavior, the authoritative source is the API specification: Service API definitions, Plugin API definitions, schema/protocol definitions, capability declarations, and lifecycle definitions.

Implementations must not introduce undocumented semantics that other components depend on.

**Prevents**
- semantic drift
- undocumented coupling
- implementation-defined behavior
- guessed semantics in generated code

**Required checks**
- contract changes update specs/docs in the same change
- changed contracts trigger compatibility review
- conformance tests validate implementations against current contract definitions

**Interpretation**  
If code and commentary appear to disagree about interface behavior, the formal contract governs interface meaning; this constitution governs structural constraints.

---

## 2. Define and Use Terms Consistently
**Classification:** Invariant

**Rule**  
Architectural terms used in this constitution are canonical and must be used consistently in code, documentation, review, and design discussion.

Minimum required terms:
- Kernel
- Service API
- Plugin API
- Plugin
- Consumer
- Extension Point
- Contract
- Composition Root
- Isolation Tier

Alternative terms may be used only if documentation explicitly maps them to the canonical term.

**Prevents**
- directional confusion
- review ambiguity
- accidental conflation of roles
- terminology drift over time

**Required checks**
- docs introducing new architecture terms define them
- PRs changing architecture docs preserve canonical terminology or explicitly map variants
- reviewers reject ambiguous use of terms like “API,” “plugin,” or “provider” when direction matters

**Note**  
The goal is not to ban all synonyms in prose, but to prevent synonyms from obscuring architectural roles.

---

## 3. Distinguish Service APIs from Plugin APIs
**Classification:** Invariant

**Rule**  
Service APIs and Plugin APIs are different contract types and must be defined, documented, versioned, and tested separately.

- **Service API**: consumer calls kernel/core — must be defined as a Protocol/interface separate from delivery layer
- **Plugin API**: kernel/core calls plugin/provider — ALWAYS defines capability contracts for external dependencies

A module may act as both consumer and plugin in different interactions, but the direction of control must remain explicit.

**Prevents**
- incorrect compatibility assumptions
- incorrect testing strategy
- callback misuse
- confusion between registration calls and callback contracts
- HTTP/transport concerns leaking into core business logic
- direct coupling between delivery layer and concrete service implementations

**Required checks**
- each changed contract is classified as Service API or Plugin API
- Service APIs have explicit Protocol/interface definitions separate from delivery layer
- documentation identifies caller and callee
- tests match interaction direction
- registration functions and registered callbacks are not described as the same API surface
- core services are testable without delivery layer framework setup

---

## 4. Interfaces Mean What Their Specs Define Them to Mean
**Classification:** Invariant

**Rule**  
A contract has only the semantics its specification explicitly defines. Caller context, implementation convenience, and historical habit must not silently redefine a method or callback.

New behavior requires explicit contract evolution.

**Prevents**
- hidden protocol drift
- interface semantics inferred from one implementation
- accidental cross-type assumptions
- undocumented behavior becoming de facto architecture

**Required checks**
- if code depends on behavior not documented in the contract, either the contract is updated or the code changes
- contract changes follow propagation analysis under Rule 16
- optional behavior, version negotiation, and no-op semantics are documented explicitly

---

## 5. API Prototypes Are Separate from Implementations
**Classification:** Invariant

**Rule**  
Public contracts must be reviewable independently from implementations. Outside composition roots and tests, code must depend on declared contracts rather than concrete implementations.

Where the language permits, public modules should expose interfaces, protocols, traits, or abstract contracts separately from implementation code.

**Prevents**
- concrete implementation coupling
- environment-specific logic leaking into consumers
- loss of swappability
- generated code bypassing intended abstraction boundaries

**Required checks**
- static dependency rules prevent direct implementation imports outside approved assembly locations
- factories and dependency injection return contract types where practical
- public-facing modules do not require importing implementation internals for correct use

---

# Part II: Architecture — Layers & Boundaries

## 6. Architectural Layers Constrain and Are Enforced
**Classification:** Invariant

**Rule**  
The architecture must define explicit structural layers and allowed dependency directions. Violations must fail in CI.

Minimum required distinguishable roles:
- contracts
- kernel/core
- delivery adapters
- plugin contracts
- plugin implementations
- composition roots
- tests

**Prevents**
- boundary collapse
- illegal cross-layer shortcuts
- transport or infrastructure leaking inward
- plugin entanglement

**Required checks**
- dependency/import rules are enforced in CI
- new top-level modules declare their architectural role
- cross-layer exceptions require explicit waiver under the governance rules

**Note**  
Layer names may vary by repository, but dependency direction must be explicit and enforceable.

---

## 7. Core APIs Are Library-Style; Delivery Is a Thin Adapter
**Classification:** Invariant

**Rule**  
Core logic must be transport-agnostic. Delivery adapters may translate requests, authentication context, validation results, and domain failures into protocol-specific forms, but they must not own domain policy.

**Allowed in delivery adapters**
- request parsing
- auth interpretation
- protocol validation
- serialization
- domain-to-transport error mapping

**Forbidden in core**
- transport, web, or RPC framework imports
- protocol-specific exception or status types
- request/response object dependencies
- delivery-specific routing logic

**Prevents**
- HTTP or RPC concerns leaking into business logic
- poor testability
- protocol lock-in
- framework logic creeping into core services

**Required checks**
- static checks forbid transport frameworks and transport exceptions in core
- reviewers reject domain policy embedded in adapters
- core remains callable without delivery framework setup

---

## 8. Directory Organization Matches Architectural Roles
**Classification:** Policy

**Rule**  
Repository structure should make architectural roles visible and enforceable. Contracts, core logic, adapters, plugin contracts, plugin implementations, composition roots, and tests should be separable by tooling.

Exact directory names are repository-specific, but role ambiguity is not acceptable.

**Recommended directory structure** (for Python projects):
```
src/
├── api/                    # Service API Protocols (what core exposes)
│   ├── bot_service.py      # BotServiceProtocol
│   └── device_service.py   # DeviceServiceProtocol
├── adapters/               # Delivery adapters (HTTP, WebSocket, etc.)
│   ├── http/               # HTTP routers (thin, just transport translation)
│   │   └── bot_management/
│   │       └── router.py
│   └── websocket/
├── core/                   # Business logic (implements Service API Protocols)
│   ├── bot_management/
│   │   ├── services/       # BotService implements BotServiceProtocol
│   │   └── repositories/
├── plugin_api/             # Plugin API Protocols (what kernel calls)
│   ├── database.py         # DatabasePlugin
│   └── cache.py            # CachePlugin
├── plugins/                # Plugin implementations
│   ├── local/              # Local/mock implementations
│   └── prod/               # Production implementations
├── hooks/                  # Cross-cutting extension points (auth, logging, etc.)
└── config/                 # Composition roots and DI factories
```

**Prevents**
- hidden boundary mixing
- review confusion
- weak tooling support
- architecture encoded only in tribal knowledge
- `api/` directory serving dual purpose (HTTP + Service Protocols)

**Required checks**
- each top-level architectural directory or package has a declared role
- one path does not serve incompatible roles simultaneously
- if `api/` exists, it has one unambiguous meaning within the repository

**Note**  
Recommended directory layouts belong in the architecture playbook, not in the constitution itself.

---

## 9. Functions and Files Serve Single Purposes
**Classification:** Guideline

**Rule**  
Files and functions should have one dominant reason to change. Large or mixed-purpose modules are review warnings because they often hide boundary violations.

**Prevents**
- accidental coupling
- hidden multi-layer logic
- “utility” dumping grounds
- hard-to-review generated code

**Review heuristics**
- mixed imports from multiple architectural roles
- adapters containing domain logic or persistence logic
- services containing transport or framework logic
- oversized files with multiple unrelated responsibilities

**Note**  
Line-count thresholds may be used as local heuristics, but they are not constitutional invariants.

---

# Part III: Plugin System — Design & Lifecycle

## 10. Component Types Are Explicitly Declared and Swappable
**Classification:** Invariant

**Rule**  
Any capability intended to be replaceable, environment-specific, isolated, or implementation-variable must be represented by a declared contract and one or more implementations.

Components that are purely internal and not architectural boundaries need not be abstracted.

**Prevents**
- hardwired infrastructure coupling
- missing abstraction at true replacement boundaries
- pointless abstractions where no boundary exists

**Required checks**
- new external or system capability integrations declare whether they are replaceable architectural boundaries
- if a capability is environment-specific or expected to have local and production variants, business code does not depend directly on a concrete implementation
- if no contract is introduced, the change justifies why replacement or isolation is not required

---

## 11. The Plugin Lifecycle Is Uniform and Enforced
**Classification:** Invariant

**Rule**  
The kernel/core must define canonical lifecycle semantics and ordering for plugin types. Each plugin type must declare:
- which lifecycle phases it participates in
- the meaning of each phase
- failure behavior
- cleanup behavior

Framework-specific phase names may vary, but they must map unambiguously to the canonical lifecycle model.

**Prevents**
- ad hoc initialization
- inconsistent startup or shutdown behavior
- leaked resources
- plugins relying on undocumented ordering assumptions

**Required checks**
- lifecycle participation is declared in plugin contracts or plugin type documentation
- startup, shutdown, and restart behavior is testable
- lifecycle changes require structural review

---

## 12. Plugin Hooks Enable Cross-Cutting Concerns
**Classification:** Invariant

**Rule**  
Cross-cutting behavior needed by multiple independent callers should be integrated through declared extension points, hooks, middleware, or event mechanisms—not by embedding the same concern directly across unrelated modules.

**Examples of cross-cutting concerns**
- tracing
- metrics
- profiling
- auditing
- policy interception
- fault injection
- **authorization and permission checks**

**Signal/Slot Pattern for Cross-Cutting Concerns**  
For cross-cutting concerns that cut across many service boundaries (e.g., authorization, permission checks, logging), use a signal/slot (hook) pattern:

1. **Core services emit signals** — they do not call specific auth/permission services directly
2. **Hook implementations register at startup** — local mode registers permissive hooks, prod mode registers real RBAC hooks
3. **Hook contracts are declared once** — centralized, not scattered across services

```
# Core service (no direct auth dependency)
def create_bot(user, data):
    if not hooks.emit("bot.create", user, data):
        raise PermissionError(...)
    # pure business logic

# Hook registration at startup
if is_local_mode():
    hooks.register(AuthHooks, LocalAuthHooks())  # Always returns True
else:
    hooks.register(AuthHooks, ProdAuthHooks())   # Real RBAC checks
```

**Prevents**
- repeated edits across unrelated modules
- direct coupling between otherwise independent components
- kernel/core contamination by orthogonal concerns
- auth/permission logic scattered across dozens of service methods
- inability to test core logic without mocking auth everywhere

**Required checks**
- new cross-cutting behavior identifies the extension mechanism it uses, or justifies why direct dependency is necessary
- extension points are governed by explicit contracts
- repeated copy-paste cross-cutting logic is rejected in review
- authorization checks go through a registered hook, not direct service calls

---

## 13. Every Plugin Matches Its Isolation Tier
**Classification:** Invariant

**Rule**  
Every plugin type must declare its isolation tier, allowed capabilities, disallowed capabilities, and enforcement mechanism. Plugins must not gain capabilities beyond their assigned tier without explicit reviewed elevation.

**Minimum declaration**
- trust tier
- execution boundary
- allowed capabilities
- restricted or disallowed capabilities
- enforcement mechanism

**Prevents**
- privilege creep
- accidental trust expansion
- unsafe third-party or semi-trusted execution

**Required checks**
- new plugin types require capability review
- privilege changes require architecture or security review
- waivers are required for elevated access outside normal tiering

---

# Part IV: Dependencies & Configuration

## 14. Configuration Drives All Wiring
**Classification:** Invariant

**Rule**  
Implementation selection and object graph assembly must be driven by validated configuration and performed only in approved composition roots.

Enabling, disabling, or selecting among existing implementations must be a configuration change, not a business-logic code change.

**Prevents**
- hardcoded provider selection
- mode checks scattered across modules
- environment-specific branching in core logic
- invisible deployment behavior

**Required checks**
- composition roots are explicitly identified in the repository
- concrete implementations are selected only in composition roots and tests
- repeated mode detection outside config or bootstrap is forbidden

---

## 15. Think Like a Dependency Auditor
**Classification:** Policy

**Rule**  
Structural review should evaluate changes as a dependency and capability audit, not just as code correctness.

Reviewers should ask:
- Does this create a new boundary crossing?
- Does this bypass a declared contract?
- Could this expand trust or capability unintentionally?
- Is this functionality still swappable?
- Is there hidden coupling via globals, environment checks, or shared state?
- Does the change introduce a new single point of failure or hidden operational dependency?

**Prevents**
- architecture-blind review
- accidental privilege escalation
- hidden coupling
- false confidence from passing tests alone

**Required checks**
- structural PRs include explicit impact analysis
- privilege or dependency expansion requires review by designated owners

---

## 16. Changes Propagate
**Classification:** Invariant

**Rule**  
Changes to contracts, lifecycle semantics, dependency boundaries, or configuration schemas must declare their propagation scope.

At minimum, a structural change must identify:
- affected consumers
- affected implementations
- affected deployments or configuration
- compatibility status
- migration or deprecation plan where needed

**Prevents**
- casual breaking changes
- hidden blast radius
- uncoordinated breakage across implementations
- schema drift in deployments

**Required checks**
- PRs touching Service APIs, Plugin APIs, config schema, lifecycle semantics, or dependency rules include propagation analysis
- breaking changes include migration or deprecation plans
- merge is blocked when required impact analysis is absent

---

# Part V: Governance & Evolution

## 17. Distinguish What Is Flexible from What Is Constrained
**Classification:** Invariant

**Rule**  
Stable architectural surfaces must be treated differently from internal implementation details.

**Constrained surfaces include**
- Service APIs
- Plugin APIs
- lifecycle definitions
- extension point semantics
- configuration schema
- capability declarations
- message and protocol formats

**Flexible internals include**
- algorithms
- internal data structures
- logging details
- performance optimizations that preserve behavior
- internal refactoring that preserves contracts

**Compatibility test**  
If changing something could cause a correctly written consumer or plugin to fail, treat it as constrained.

**Prevents**
- accidental boundary breakage
- underestimating change risk
- treating public contracts like local implementation details

**Required checks**
- constrained-surface changes require coordination and impact analysis
- flexible changes should not require downstream updates if contracts are preserved

---

## 18. Resolve Conflicts Explicitly
**Classification:** Invariant

**Rule**  
When implementation convenience conflicts with this constitution, the conflict must be made explicit and resolved deliberately. Convenience does not silently override structural rules.

A conflict record must state:
- which rule is in tension
- what practical constraint exists
- what risk the workaround introduces
- at least one compliant alternative
- at least one exception-based alternative with compensating controls

**Prevents**
- silent erosion of architecture
- “temporary” shortcuts becoming permanent
- undocumented local optimizations that break global structure

**Required checks**
- intentional invariant violations require a written waiver
- reviewers reject undocumented exceptions
- conflicts are recorded in architecture decisions or waiver records

---

## 19. Abstract After Two Examples, Not Before
**Classification:** Guideline

**Rule**  
Abstractions should be justified by repeated concrete need, a true replacement boundary, or an explicit compatibility requirement. Avoid speculative interfaces that do not protect a real architectural seam.

**Prevents**
- fake abstraction
- unnecessary indirection
- generic “manager” or “handler” layers with no real role
- boilerplate interfaces with one accidental implementation

**Review heuristics**
- does this abstraction protect a real boundary?
- is the capability replaceable, isolated, or environment-specific?
- does the abstraction simplify testing, swapping, or governance?
- if there is only one implementation, is the boundary still architecturally meaningful?

**Exception: Cross-Cutting Concerns and Hook Protocols**  
For cross-cutting concerns (authorization, logging, metrics, tracing), a Hook Protocol may be defined even with zero initial implementations. This is NOT a violation of “abstract after two examples”:

- Hooks are signal/slot patterns, not traditional abstractions
- Core services emit signals without knowing implementations
- Implementations register at startup (local vs prod)
- Example: `AuthHooks` Protocol allows centralizing auth logic, even if initial implementation is permissive

This exception exists because:
1. Cross-cutting concerns would otherwise scatter across dozens of services
2. A single Protocol centralizes the extension point definition
3. Local development benefit requires mock implementations from day one

**Note**  
A single example can justify abstraction when it defines a genuine boundary; “two examples” is a heuristic, not a law.

---

# Part VI: Development Cycle Enablement

## 20. Single-Box Development First
**Classification:** Policy

**Rule**  
Externally dependent capabilities should provide at least one non-production implementation suitable for local development, CI, or isolated debugging. Acceptable forms include:
- local implementation
- fake
- simulator
- stub
- noop

The required non-production form depends on the capability.

**Prevents**
- overdependence on remote systems for routine work
- slow and flaky development loops
- inability to verify generated changes locally
- high onboarding friction
- "local mode" that still requires VPN, MOSN, or external binaries

**Required checks**
- each external capability documents its local or CI strategy
- repositories identify which implementations support local development and which require remote dependencies
- local mode is tested in CI without network access
- a new contributor can run the full application in under 10 minutes with no external setup

---

## 21. Testing Isolation Implementations
**Classification:** Policy

**Rule**  
Each test-relevant contract should have at least one isolation strategy appropriate to its role, such as:
- noop
- fake
- stub
- mock
- simulator
- fixture-backed implementation

Not every contract requires every form.

**Prevents**
- brittle tests
- overuse of full environments
- poor unit isolation
- excessive mocking disconnected from real contracts

**Required checks**
- the test strategy for important contracts is documented
- contract tests validate real implementations, not only mocks
- isolated test doubles respect contract semantics relevant to the tests that use them

---

## 22. Context Boundaries Are Explicit
**Classification:** Policy

**Rule**  
Each boundary-significant module should declare its context:
- what it provides
- what it consumes
- its major dependencies
- its major dependents
- expected change impact

This may be documented in structured docs, module metadata, or generated dependency manifests.

**Prevents**
- hidden impact scope
- slow review
- unsafe refactoring
- changes made without awareness of upstream and downstream effects

**Required checks**
- major modules expose enough metadata for change-impact review
- structural PRs update context declarations when boundaries change

---

## 23. Patterns Are Consistent and Cataloged
**Classification:** Policy

**Rule**  
Repeated architectural solutions should be named, documented, and reused consistently. This includes patterns for:
- dependency injection and composition roots
- error translation
- lifecycle handling
- contract implementation
- configuration loading
- extension point usage

**Prevents**
- inconsistent generated code
- repeated architecture debates
- local conventions diverging from system-wide patterns

**Required checks**
- repeated boundary patterns are documented in a pattern catalog
- deviations from known patterns are justified in review

---

## 24. Architecture Supports Incremental Changes
**Classification:** Invariant

**Rule**  
The architecture must support small, independently verifiable changes with bounded blast radius. Most routine work should be local to one implementation, one adapter, one configuration change, or one contract evolution step.

**Prevents**
- high-cost “touch everything” changes
- unnecessary coupling between independent components
- unsafe refactoring under rapid development
- large unreviewable generated diffs

**Required checks**
- structural changes declare scope and verification plan
- contract evolution prefers backward-compatible steps when possible
- changes that unexpectedly require wide edits are treated as signals to re-examine boundaries

---

## 25. Protocols Have Self-Validating Contracts
**Classification:** Invariant

**Rule**  
Every declared Service API, Plugin API, and replaceable capability contract must have conformance tests that all relevant implementations pass.

Implementation-specific tests may extend coverage, but they do not replace conformance tests.

**Prevents**
- silent divergence across implementations
- local and production incompatibility
- contract drift hidden by testing only one implementation
- false confidence from unit tests alone

**Required checks**
- each boundary contract maps to a conformance test suite
- CI runs conformance tests for affected contracts
- new implementations are not mergeable without contract validation

---

# Governance Addendum

## Waiver Requirement

Any intentional violation of an **Invariant** requires a written waiver that includes:
- violated rule
- reason
- risk introduced
- compensating controls
- owner
- expiry or review date

Temporary exceptions without review dates are not allowed.

---

# Structural Review Red Flags

The following are presumptive violations unless explicitly justified:

- core imports transport, web, or RPC framework code
- an adapter contains domain policy or persistence logic
- a concrete plugin implementation is imported outside composition roots or tests
- environment access is scattered outside config or bootstrap
- implementation selection occurs outside composition roots
- a contract changes without propagation analysis
- a config key is added without schema update
- a new capability is introduced without a declared boundary decision
- a privilege or capability increase occurs without isolation review
- repeated cross-cutting logic is added by copy-paste instead of through an extension mechanism
- HTTP router directly imports core service implementation (not Service API Protocol)
- local mode still requires network access, VPN, or external binaries
- auth/permission checks scattered across service methods instead of centralized hooks
- Service API exists only as HTTP router, not as explicit Protocol

---

# Quick Reference Card

| Rule | Summary | How It Helps LLM-Assisted Development |
|---|---|---|
| 1 | Contracts are authoritative | Prevents LLMs from inventing undocumented behavior or inferring semantics from one implementation. |
| 2 | Terminology is canonical and consistent | Reduces ambiguity in prompts, reviews, generated docs, and code changes. |
| 3 | Service APIs and Plugin APIs are distinct; Service APIs need explicit Protocols | Helps LLMs preserve call direction, choose correct tests, and create Protocol files for service boundaries (not just HTTP routers). |
| 4 | Interfaces mean only what the contract says | Stops LLMs from silently extending interface behavior based on assumptions or local convenience. |
| 5 | Contracts are separate from implementations | Prevents generated code from importing concrete classes directly and breaking swappability. |
| 6 | Layer boundaries are explicit and CI-enforced | Gives LLM-generated code hard structural limits and catches illegal dependency shortcuts early. |
| 7 | Core is transport-agnostic; adapters translate | Keeps LLMs from leaking HTTP/RPC/framework concerns into business logic. |
| 8 | Code organization exposes architectural roles (api/ = Protocols, adapters/ = delivery) | Makes it easier for LLMs to place new code in the correct location and follow dependency rules. |
| 9 | Single responsibility is a review heuristic | Helps detect generated “god files” and mixed-purpose code before they become structural debt. |
| 10 | Replaceable capabilities use declared contracts | Encourages LLMs to preserve real architectural seams instead of hardcoding infrastructure choices. |
| 11 | Plugin lifecycle semantics are canonical | Prevents LLMs from adding ad hoc startup, shutdown, or cleanup behavior that breaks runtime expectations. |
| 12 | Cross-cutting behavior (e.g., auth) uses hooks/signal-slot | Keeps LLMs from scattering auth/permission checks across services; centralize in registered hook implementations. |
| 13 | Plugins match declared isolation tiers | Reduces the risk of LLMs accidentally expanding plugin privileges or bypassing trust boundaries. |
| 14 | Wiring happens only in composition roots via config | Prevents LLMs from scattering env checks and implementation selection throughout the codebase. |
| 15 | Review as a dependency and capability audit | Gives reviewers a better lens for evaluating LLM-generated changes beyond “tests pass.” |
| 16 | Structural changes declare propagation | Forces LLM-assisted edits to account for downstream consumers, implementations, and configs. |
| 17 | Stable surfaces are constrained; internals are flexible | Helps LLMs distinguish safe refactors from changes that require coordination and migration. |
| 18 | Architectural conflicts are made explicit | Prevents LLM-generated shortcuts from being accepted without risk discussion and compensating controls. |
| 19 | Abstract for real boundaries (exception: cross-cutting hooks) | Reduces useless generated interfaces |
| 20 | Single-box dev must work completely without network | Lets LLMs and developers verify changes quickly without depending on fragile remote systems. |
| 21 | Important contracts should support isolated testing | Gives LLMs safe ways to test behavior in isolation without building full environments. |
| 22 | Module context boundaries should be explicit | Helps LLMs reason about what a module provides, consumes, and what a change will affect. |
| 23 | Repeated patterns should be cataloged | Improves consistency by giving LLMs standard templates for recurring architectural problems. |
| 24 | Architecture supports incremental change | Encourages LLMs to make smaller, safer, independently verifiable changes with bounded blast radius. |
| 25 | Every contract has conformance tests | Gives LLMs a reliable verification target to catch breakage across multiple implementations. |
