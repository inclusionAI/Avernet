# avernet-genome-contracts

Python 3.12 package `avernet_genome` (`1.0.0`): shared Genome DTOs, patch types,
canonicalization/content hashing, logical paths, tree codecs and strict text edits.
Used by Backend and the independent Evolution service; no transport, environment,
registry storage, Engine filesystem, publication or promotion policy lives here.

## Context Boundary

```yaml
purpose: Share versioned pure Genome data contracts without coupling Evolution to Backend internals.
provides:
  - BotRef
  - GenomeRevision
  - GenomeSpec
  - GenomePatch
  - MutationSelector
  - FrozenMutationScope
  - resolve_scope
  - mutation_problems
  - canonicalize
  - revision_id
  - sha256_digest
consumes: []
internal_dependencies: []
```

### Change impact

Backend keeps compatibility imports pointing to the same module/type objects.
Changing these contracts affects both services, persisted patches and Worker
payloads. Preserve canonical hashes and strict patch semantics; incompatible
changes require explicit versioning and compatibility tests, not a silent DTO fork.
Backend-specific diff policy remains in Backend; generic text diff helpers are
shared. This package does not establish a new external Genome HTTP contract.

Schemas and deterministic vectors are packaged under `src/avernet_genome/`.
Run the shared-package lint/architecture tests through
`apps/evolution/scripts/ci_test.sh`, and Backend Genome conformance suites for
behavior changes. The migration regression commands and limitations are recorded
in `docs/specs/2026-11-17-evolution-service-migration/report.md`.

## Update-only mutation authorization

`mutation_scope.py` owns pure `MutationSelector` / `FrozenMutationScope` DTOs,
`resolve_scope` and `mutation_problems`, shared by both services. A selector names
raw Persona filenames or raw Skill names (`include: "all"` or a nonempty list,
optional `exclude`, `operations: ["update"]`); Skill selectors require
`origin: "local"`. It does not select SkillSet, Center or repository Skills.

Resolution intersects the strategy capability, Binding request, existing parent
inventory, parent and platform mutable policy, pins, and any legacy
`allowed_genes` ceiling. It emits concrete canonical paths and retained pins.
A frozen empty scope grants nothing; `None` is the historical path-only contract,
not an empty grant. Scope metadata is outside Genome revision and patch hashes.

Validation checks actual operations and **every** `skill.update` member, not only
the package root. Backend additionally supplies the parent spec to enforce
existing/local ownership independently. This package neither authenticates the
caller nor persists authorization; the Run service and platform adapter own that.
See the [Run contract](../../apps/evolution/src/avernet_evolution/core/evolution_run/README.md#frozen-mutation-scope)
and its scope/migration tests under `apps/evolution/tests/unit/`.
