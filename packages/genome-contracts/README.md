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
