---
status: accepted
---

# Separate Skills Pool admission from rollout observation

Skills Pool admission uses exact Bot allowlists, Owner-plus-Engine rules,
Environment-plus-Engine rules, exact Bot exclusions, and an Engine Admission
Switch. Historical migration outcomes remain observable, but they do not block
later policy changes. This removes Batch as a rollout gate while preserving
Engine isolation, auditability, sticky claims, and explicit rollback.

## Consequences

- Every allow and exclusion rule is Engine-bound. The expected rollout target
  is OpenClaw; enabling OpenClaw never admits another Engine.
- Global feature disable, then Engine Admission disable, then exact exclusion
  are evaluated before allow rules.
- Removing a rule prevents future first claims only. Claimed migrations keep
  moving through their state machine; Legacy rollback stays explicit.
- The existing config row and rollout audit table remain the persistence seam.
  Writes use expected-revision CAS and atomically append a non-Batch audit event.
- v1 is read-compatible. The first successful v2 mutation binds historical
  exact entries to each Bot's current Engine and atomically writes schema v2.
- Old Batch writes return `410 Gone`. Historical Batch GET remains temporarily
  read-only for diagnosis and is not called by the new path.
