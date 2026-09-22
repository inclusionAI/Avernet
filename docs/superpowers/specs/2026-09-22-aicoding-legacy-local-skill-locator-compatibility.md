# AICoding Legacy Local Skill locator compatibility

**Status:** Implemented on the feature branch; validation in progress

**Baseline:** Avernet `REL20260922@a7369c677`; OCB `REL20260922@9dcd183914`, `ocb-public@8117aba91`

**Scope:** Phase 1 Backend compatibility for Bot-local Skill upload replacement, query, and deletion

## 1. Outcome

An AICoding Runtime using the Legacy Skill layout must be able to operate on a Local Skill whose existing `git_path` was recorded under the historical Claude Code namespace. Backend resolves the historical locator to the current AICoding package storage without changing the recorded `git_path`.

This phase changes only AICoding Legacy compatibility. OpenClaw, Claude Code, Hermes, Teclaw, and Pool layout retain their existing locator rules. It does not modify AIX Engine, introduce a database migration, or change the Local Skill HTTP contract.

## 2. Correct path model

A Local Skill path has three independent dimensions:

```text
Local Skill locator
├── Address View
│   ├── Host/NAS view: /aidesktop/...
│   └── Engine view:   /home/admin/...
├── Layout
│   ├── LEGACY: workspace/skills/skills-local
│   └── POOL:   workspace/skills-pool/skills-local
└── Engine namespace
    ├── claude_code / .claude_code
    └── aicoding / .aicoding
```

`/home/admin` does not imply Pool, and `/aidesktop` does not imply Legacy. Layout comes from the authoritative Bot layout state and its directory topology; Runtime identity comes from `resolve_runtime_engine_for_bot(...)`, not from `active_engine` alone.

For an AICoding Runtime in Legacy layout, these historical pairs may identify the same package:

```text
Host/NAS view
.../<bot>/claude_code/workspace/skills/skills-local/<skill>
.../<bot>/aicoding/workspace/skills/skills-local/<skill>

Engine view
/home/admin/.claude_code/workspace/skills/skills-local/<skill>
/home/admin/.aicoding/workspace/skills/skills-local/<skill>
```

Acceptance is exact and scoped. It requires the current Bot to resolve to AICoding, its layout to be Legacy, the Host/NAS locator to belong to that Bot, and the package leaf to equal the expected Skill name. Path traversal, another Bot, another Skill, another Engine, another directory topology, and Pool layout remain invalid.

## 3. Current failure

### Legacy fallback

```text
upload ZIP
  → package capability is absent
  → Backend legacy replace
  → read existing git_path
  → reopen locator against current AICoding root
  → historical claude_code locator fails containment
  → Local Skill cleanup locator escapes skills-local
```

Even if containment accepted the historical locator, legacy replace currently also requires raw equality between the recorded locator and the current canonical locator. Different Address Views or Engine namespaces therefore still fail despite identifying the same Runtime package.

Delete and Local content query reopen the recorded locator through the same Factory, so this is not only an upload problem.

### Package-apply path

When `skills.local_package.apply.v1` is declared, package replacement already preserves the historical locator by committing `new_locator=old_locator`. New package creation persists Backend's resolved `location.directory`; Engine `target_path` is logged as execution evidence and is not copied into the database.

The missing compatibility is therefore primarily the shared locator Factory plus the legacy replace caller.

## 4. Target flow

```text
ac_skill.git_path (Recorded Local Skill Locator)
  → SkillServiceFactory validates current Bot, Runtime, Layout, and Skill name
  → exact current locator or allowed AICoding Legacy alias
  → current canonical LocalSkillPackageStorage
  → upload replace / query / delete

Metadata commit after replace
  old_locator = recorded locator
  new_locator = recorded locator
```

The Factory is the only component that understands the alias. Services operate on the returned storage and never implement AICoding path substitution.

## 5. Factory contract

Evolve the existing Factory entry point to require the expected Skill name:

```python
local_skill_package_storage_for_locator(
    *,
    entity_id: str,
    owner_id: str,
    bot_id: str,
    engine_type: str | None,
    entity_type: str,
    is_desktop: bool,
    is_teclaw: bool,
    locator: str,
    skill_name: str,
) -> LocalSkillPackageStorage
```

The Factory must:

1. Resolve the Bot's filesystem Runtime identity and authoritative Layout.
2. Resolve the current canonical package location for `skill_name`.
3. Accept the exact canonical recorded locator under existing rules.
4. Only for AICoding plus Legacy, accept the two exact Claude Code address-family aliases described above.
5. Require an exact package leaf, not merely containment somewhere below `skills-local`.
6. Return storage for the current canonical Runtime package.
7. Preserve fail-closed behavior and the existing external error mapping for every rejected locator.

The alias policy belongs with the shared workspace/locator domain rules, while `SkillServiceFactory` owns applying it to the current Bot context and constructing storage. Repository code must not interpret Runtime paths, and upload/delete/query services must not duplicate the policy.

## 6. Service changes

### Upload legacy replace

- Pass the expected Skill name when reopening the recorded locator.
- Remove raw `old_locator != canonical_locator` rejection.
- Treat successful Factory resolution as proof that the recorded locator identifies the canonical package.
- Require the old package to exist; do not manufacture missing bytes.
- Publish through the existing staging, verification, backup, rollback, and cleanup sequence.
- Commit `old_locator` as both the repository compare-and-swap input and output locator.
- Return the original `skill["git_path"]` byte for byte.

### Delete

- Pass the stored Skill name to the Factory.
- Delete the canonical Runtime package returned by the Factory.
- Keep authorization, edit lease, blocking-reference checks, runtime-success requirement, and metadata deletion semantics unchanged.

### Query

- Pass the stored Skill name to the Factory.
- Read content from the canonical Runtime package returned by the Factory.
- Keep source authorization and response contracts unchanged.

## 7. New creation and future AIX package apply

New creation records Backend's current resolved locator. For an AICoding Legacy Runtime whose canonical address is the Engine view, the expected form is:

```text
local:///home/admin/.aicoding/workspace/skills/skills-local/<skill_name>
```

For Pool it is:

```text
local:///home/admin/.aicoding/workspace/skills-pool/skills-local/<skill_name>
```

This phase does not enable AIX package apply. When AIX later implements `POST /api/skills/local/apply`, it receives `skill_name`, `layout`, and the canonical ZIP, selects its physical directory, and performs exact package replacement. Backend continues to own persisted locator identity:

- create records Backend's current resolved `location.directory`;
- replace preserves an existing historical locator;
- Engine `target_path` remains non-authoritative diagnostic evidence.

Capability behavior remains strict:

```text
capability absent
  → use legacy Backend fallback

capability declared
  → use package apply
  → any request failure or unknown result fails
  → never fall back after the new request starts
```

## 8. Data policy

- Do not rewrite historical `ac_skill.git_path` values.
- Do not add migration SQL.
- Do not normalize `claude_code` strings to `aicoding` during replace.
- Do not silently rebuild a missing package from metadata.
- Existing AICoding locators remain valid; new writes do not create the historical Claude Code form.
- Legacy alias support is durable read compatibility. Removing it requires a separate migration with evidence that no supported historical locator remains.

The observed upload failure occurs before canonical publication in the current legacy flow, so this error does not itself require a package cleanup or metadata repair step.

## 9. Acceptance matrix

| Scenario | Expected result |
| --- | --- |
| AICoding Legacy plus same-Bot Host/NAS Claude Code locator, replace | Success; `git_path` unchanged |
| AICoding Legacy plus Engine-view `.claude_code` locator, replace | Success; `git_path` unchanged |
| Either supported alias, Local content query | Reads current AICoding package |
| Either supported alias, delete | Deletes current AICoding package, then metadata under existing rules |
| AIX capability absent | Legacy fallback succeeds |
| AIX capability declared | Package apply is used; replace preserves locator |
| Declared package apply fails or is unknown | Failure; no legacy fallback |
| AICoding new Legacy Skill | Records current AICoding canonical locator |
| OpenClaw, Claude Code, Hermes, or Teclaw | Existing behavior unchanged |
| AICoding Pool plus Legacy alias | Rejected |
| Another Bot, another Skill, arbitrary prefix, or traversal | Rejected |
| Recorded package is absent | Failure; no implicit reconstruction |

Tests must cover Factory contracts plus upload, delete, and query service behavior. Negative tests must prove the behavior is AICoding Legacy-only rather than broadening locator containment globally.

## 10. Repository and release boundaries

### Avernet

- shared locator/layout compatibility rule;
- `SkillServiceFactory` resolution;
- upload, delete, and query callers;
- narrow unit and contract tests;
- Skill Center glossary and ADR.

### OCB

The matching OCB release currently consumes an older Avernet gitlink whose relevant Skill Center and workspace files are equivalent to this baseline. After the Avernet implementation merges, OCB should update `ocb-public` and run its Backend/Engine composition and compatibility gates. No corp business implementation is planned unless those gates expose an actual adapter or DI requirement.

### AIX Engine

No Phase 1 code change. Package-apply implementation and capability declaration are a later delivery following the contract in section 7.

## 11. Verification stages

1. Run Factory contract tests for current, aliased, cross-Bot, cross-Layout, cross-Engine, and traversal paths.
2. Run Local upload, delete, and query service tests.
3. Run affected architecture, DI, type, and compatibility gates.
4. Perform Standards and Spec reviews before creating an implementation PR.
5. Run PR CI and local full CI as separate gates.
6. After deployment, verify replace, query, and delete with a known historical AICoding Skill and confirm the persisted `git_path` is unchanged.

Local tests, PR CI, merge, deployment, and live validation are separate completion states.
