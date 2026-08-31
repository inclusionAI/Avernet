# Phase 2 Group 1 Acceptance to Test Matrix

> Scope: GitHub Issue #1170 only. Publication workers, Materializer, Runtime,
> Track Latest, Service Artifact, SC Public Reference and Offline commands are
> owned by later groups. Group 1 only finalizes their shared persistence facts
> and exposes the creation and read contracts described below.

| Acceptance | Public seam | Red-green evidence | Final gate |
| --- | --- | --- | --- |
| Final Offline, Draft, Version and Publication Attempt facts replace the retired/status skeleton | ORM plus `SpaceSkillRepository` | schema/ORM contract tests reject legacy fields and invalid enum states | additive SQL verification and architecture tests |
| Final Space Skill summary, detail and stable keyword pagination | `SpaceSkillQueryServiceProtocol` | service tests cover lifecycle, latest Published, Draft, active Attempt, Owner, actor permissions, pending editor request and Lease summary | Router contract plus generated OpenAPI compatibility |
| Grant, Manager, Owner transfer, Editor Request and Lease are published | real `openapi_v1` Space router | existing domain tests plus route inventory/OpenAPI tests | generated Gateway artifact contains every operation |
| Multipart folder creation is atomic and replayable | `SpaceSkillApplicationServiceProtocol.create_from_folder` | tests cover strict validation, one Identity/Binding/Owner/V1 Draft, same-key replay, cross-request conflict and DB-failure Store cleanup | multipart Router contract and repository transaction tests |
| Git creation is a deterministic Snapshot | `SpaceSkillApplicationServiceProtocol.create_from_git` | tests cover root-first selection, normalized parent bytewise order, persisted branch/commit/subdir and invalid selected package failure | Git Router contract |
| Upgrade copies exact latest Published content | `SpaceSkillApplicationServiceProtocol.create_upgrade_draft` | tests cover PUBLISHED-only ordinal selection, Canonical read, exact SC fallback/repair, one Vn+1 Draft and idempotency | application and Canonical Store contract tests |
| Draft tree/read/save uses immutable revisions and CAS | `SpaceSkillApplicationServiceProtocol` Draft methods | tests cover UTF-8 reads, Personal revision CAS, Team revision plus fencing, immutable write-before-CAS cleanup and FROZEN rejection | Router contract and repository transaction tests |
| Draft refresh never mutates on Git failure | `refresh_draft_from_git` | tests prove failed fetch/selection/validation leaves locator, revision and metadata unchanged | application test |
| Draft deletion distinguishes DRAFT and SKILL | `delete_draft` | tests cover expected revision, Team fencing, FROZEN rejection, external-fact preservation, DB-first delete and best-effort Store cleanup | Router and repository tests |
| Published Version reads address business ordinals | `SpaceSkillQueryServiceProtocol` Version methods | tests exclude MATERIALIZING and cover exact detail, tree and UTF-8 file read from Canonical Store | Router contract |
| Consumable directory is Space/PUBLISHED/Ready/non-Offline only | `list_consumable_space_skills` | tests use persisted `PUBLISHED` as the Canonical Ready SSOT, exclude Draft-only, MATERIALIZING, Offline and unrelated Space assets, and prove DB pagination performs no per-package Store reads or Bot Membership read | Router contract and Singlebox coverage |
| Every route has public governance | `PublicAPIRoute` plus Authorization/Admission inventories | architecture tests enumerate every Group 1 method/path and reject missing inventory entries | OpenAPI dump, compatibility gate and pinned artifact drift test |

## Agreed seams

Tests observe behavior only through the Application Service, Query Service,
Repository transaction contract, immutable Store contracts and real OpenAPI
router. They do not assert private helpers, SQL ordering or Engine/Runtime side
effects. Asset, Draft, Version and ordinary creation reads must not call
`BotCapabilityStateReader` or mutate Installation.
