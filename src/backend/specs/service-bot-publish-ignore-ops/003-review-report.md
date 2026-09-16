---
agent: tc-code-reviewer
status: in_progress
created: 2026-09-16
iteration: 1
---

# Code review: publish-ignore operations

## Scope

- Branch: `feat/service-bot-publish-ignore-ops-rel20260917`.
- Base: `github/REL20260917` (`e12a495a2d9a3f65fe5a4a6227ed25142fb1d8f4`).
- Includes staged, unstaged and new Backend/Engine files plus runtime identity bootstrap; no tar or copy-algorithm changes.
- Head and remote CI evidence are not yet available. This is an intermediate review, not release approval.

## Evidence independently reproduced

| Check | Result | Evidence |
| --- | --- | --- |
| Backend focused behavior | PASS | 44/44 tests, including service, transport, router, DI and nine real shell startup tests |
| Backend focused line coverage | PASS, local only | New domain service 77/77; transport 63/64; combined 140/141 (99.29%) |
| Engine focused behavior | PASS | 28/28 tests through real router/DI and filesystem |
| Engine focused line coverage | PASS, local only | File implementation 139/140 (99.29%) |
| Static checks | PENDING | Backend new files clean; Engine models.py E302 reported for correction |
| Architecture and endpoint registry | FAIL | Full Backend run reported four architecture/endpoint-registry failures; implementation agent is correcting them |
| Formal ACI | PENDING | No committed feature head or completed remote job; local focused coverage is not ACI evidence |

## Findings and resolution tracking

1. **Resolved: cross-Bot signing authority.** Replaced shared symmetric secret with Ed25519: Backend owns private signing key, Engine receives verification public key only. Target identity is signed and checked against managed runtime credentials. A persistent bounded request journal prevents replay after a later opposite mutation and after process restart.
2. **Resolved: authorization and target handling.** Empty/anonymous callers fail closed even with an admin flag; normal callers reuse existing Bot management permissions, admins can address other Bots. Selection uses source Bot/entity/environment and exact publication version/stage. BaaS enumerates fixed replica UUIDs; ARCA resolves its binding without BaaS enumeration. A second snapshot failure preserves completed results and marks snapshot unknown.
3. **Must fix: dependency direction.** Core and plugin implementation currently import command/error contracts through Service API, and Plugin API imports API/Core record types. Move shared command/error/target contracts to an allowed shared-contract layer; do not add architecture exemptions.
4. **Must fix: endpoint and meaningful contract evidence.** Register the new route with required happy/error coverage. Existing world-based anonymous-denial test proves composition and denial but does not invoke the runtime plugin; add consumer-to-runtime success/failure behavior assertions with recorded target calls.
5. **Must fix: static warning.** Add the missing top-level class separation in Engine `core/publish_ignore/models.py` (E302).

## Requirement checks

| Requirement | Review |
| --- | --- |
| Admin all Bots / ordinary authorized managers | Verified service behavior; endpoint registry completion pending |
| Exact entity/version/stage; no latest fallback | Verified selection and runtime identity guard |
| BaaS and ARCA | Verified separate transport paths and existing ARCA builder headers |
| Atomic fixed-file change, safe literal rules | Verified no-follow regular-file opens, fixed sibling lock, bounded lock wait, same-directory replace, failed replace preservation, concurrent updates, CRLF/comments and Unicode line separator behavior |
| Trusted mutation and replay control | Verified Ed25519 verification, expiry recheck after lock, durable request consumption, mismatch denial |
| Scope and compatibility | Current instances only; no restart, application-file deletion or tar changes. Installer support remains a separate deployment prerequisite |

## Overall conclusion

**REJECT (intermediate):** complete architecture, endpoint/contract coverage and static fixes before final code approval. Formal ACI remains **PENDING** independently of those fixes.

Formal gate thresholds remain unchanged: casePassRate 100%, lineCoverage at least 70%, changeLineCoverage at least 90%. Numerators/denominators for the committed base/head and remote job must be recorded by subsequent regression/PR validation.
