# Architecture Waivers

Register of accepted, time-bounded violations of an **Invariant** in
[`arch.rules.md`](./arch.rules.md).

An entry here is required before CI may allowlist a boundary exception. Each one
carries every field required by `arch.rules.md` § *Waiver Requirement* and
`ci.enforce.md` § H (*Waiver Enforcement*): the exact rule, reason, risk,
compensating controls, owner, review date, and removal plan.

**This register is enforced, not decorative.**
`src/backend/tests/community/architecture/test_waiver_register.py` parses it on
every run and fails when a waiver is missing a required field, when an `Active`
waiver is past its `Review by` date, or when the exception a waiver names is no
longer in the gate it claims to waive. `ci.enforce.md` § L schedules automated
expiry in Phase 3; that gate is the slice of it this register needs to be real.

When the expiry check fails, the waiver has outlived its review date. Remove the
exception, or re-date the waiver as a fresh, owned decision. Do **not** bump the
date purely to turn CI green — that is the one move the whole mechanism exists
to prevent.

> **Scope note.** This register was created with W-001. The `core → api`
> allowlist in `tests/community/architecture/test_architecture_compliance.py`
> already carried six entries that predate it and are **not** covered by any
> waiver — `bot_build_service`, `bot_service`, `beta_quota_service`,
> `publish_approval_service`, `space_skill_query_service` and
> `startup_script_service`. Backfilling those is deliberately out of scope for
> W-001; do not read this file as governing them.

---

## W-001 — `LocalSkillQueryService` imports its Service API Protocol

| Field | Value |
| --- | --- |
| **Status** | Active |
| **Rule violated** | `arch.rules.md` Rule 6 — *Architectural Layers Constrain and Are Enforced* (Classification: **Invariant**), specifically its required check "cross-layer exceptions require explicit waiver under the governance rules" |
| **Gate** | `tests/community/architecture/test_architecture_compliance.py::test_core_layer_does_not_import_api` |
| **Exception** | `core/skill_center/services/local_skill_query_service.py` → `agentclaw.community.api.local_skill_query_service` |
| **Owner** | @totalfrank |
| **Granted** | 2026-08-23 |
| **Review by** | 2027-02-23 |

### Reason

`LocalSkillQueryService` satisfied `LocalSkillQueryServiceProtocol` structurally
only. Nothing linked the contract to its implementation, so the Protocol was not
navigable to its implementation in an IDE, and a dropped or renamed member
surfaced as an `AttributeError` at whichever router called it rather than at
construction. Inheriting the Protocol — with every member `@abstractmethod` —
fixes both, but the Protocol lives in `api/`, so the implementation must import
across the layer boundary.

### Risk introduced

`core` gains a compile-time dependency on `api` for one module. If `api/` later
takes on a dependency that `core` must not have, this import becomes a path for
it to leak inward. The blast radius is one file and one import.

### Compensating controls

- The exception is per-file and per-target-module in the gate's allowlist; it
  admits exactly this one import, not the `core → api` direction generally.
- `api/local_skill_query_service.py` imports nothing outside `typing`/`abc`, so
  the edge currently transits no further dependency.
- `test_api_layer_is_protocols_only.py` keeps `api/` free of routers, services
  and models, bounding what this import could ever reach.
- `test_service_api_conformance.py` still checks the pair, so the contract is
  covered by two independent mechanisms rather than one.
- The `skill_center` `## Context Boundary` declares the import, so
  `test_declared_deps_cover_actual_imports` fails if it is moved or widened.

### Removal plan

Adopt the pattern already used by the governance services (see the module
docstring of `api/governance_service.py`, from the 2026-07-12 核心业务层化改造):
move the **definition** of `LocalSkillQueryServiceProtocol` into
`core/skill_center/`, and have `api/local_skill_query_service.py` re-export it
for router injection. `core` then imports its own abstraction, the boundary
exception disappears, and both this waiver and the allowlist entry are deleted.

That migration is not bundled here because
`test_api_layer_is_protocols_only.py::test_every_api_file_defines_a_protocol`
requires each `api/*.py` to contain at least one top-level
`class X(Protocol):`. A re-export-only module defines none and would fail that
gate, so the pattern needs that gate taught about re-export modules first —
a change to a second architecture gate, and a wider decision than this waiver.
`governance_service.py` avoids the problem only because it also defines other
Protocols in-file.

### Linked PR

inclusionAI/Avernet#1375
