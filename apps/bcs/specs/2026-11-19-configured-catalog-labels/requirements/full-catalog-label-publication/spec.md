## ADDED Requirements

### Requirement: Full current publication map
Every Agent/Group catalog write SHALL contain the complete current label map, built from persisted custom labels and existing system-generated dimensions. Agents SHALL retain visibility and user_visibility; Groups SHALL retain visibility without inventing a new Group user_visibility. Custom keys SHALL NOT override system labels. No incremental tombstones or old-key readback SHALL be added in this change.

#### Scenario: Full Agent and Group labels
- **WHEN** an Agent or eligible public Group labeled running is published
- **THEN** `labels.lifecycle` equals `["running"]` and all existing resource-specific system labels are present outside the `card` document

#### Scenario: Custom collection cleared
- **WHEN** custom labels have been cleared and publication occurs
- **THEN** the payload contains only current system labels and no stale custom keys or empty-list deletion patches

### Requirement: Latest-state retries and unchanged eligibility
Publication SHALL queue identities, re-read committed current state on every retry and react to label-only writes. Existing Group kind/strategy/visibility/status eligibility and deletion behavior SHALL remain unchanged. Downstream incremental processing SHALL remain outside this implementation and its final full-replacement behavior SHALL be recorded as external acceptance.

#### Scenario: Rapid changes before retry
- **WHEN** lifecycle changes trial to running before a queued retry publishes
- **THEN** the retry publishes the current persisted running state instead of a queued trial snapshot

#### Scenario: No accidental Group eligibility expansion
- **WHEN** a Group is Chat, DM, inactive or not public
- **THEN** custom labels do not make it newly publishable

### Requirement: Expand-first upstream-first deployment and honest compatibility
Release SHALL expand schema and distribute the policy before new binaries, roll every backend before enabling label-aware callers, and publish the tested public revision before downstream-host integration. The release record SHALL distinguish API acceptance, DB persistence, payload publication and downstream application. Old-writer omission-preservation, closed old REST DTO rejection and strict old response decoder risks SHALL be tested and documented; no unverified downgrade SHALL be called label-preserving.

#### Scenario: New client reaches old REST server
- **WHEN** a new label request targets the pre-change closed DTO
- **THEN** deployment tests record rejection and client enablement remains blocked until all replicas support the contract

#### Scenario: Rollback after non-empty labels exist
- **WHEN** operators roll back application binaries
- **THEN** new clients are stopped first, added columns/data/history remain, migration-runner compatibility is verified and old-writer/publication loss risks are explicitly controlled

### Requirement: Post-commit response reads MUST NOT suppress catalog notification

A successfully committed versioned Group mutation MUST reach the existing Core
catalog notification boundary even if a later response-oriented database read
would fail. Transaction failures MUST still propagate and emit no committed
notification. This requirement does not add process-crash durability or a catalog
outbox; the existing publication queue remains identity-based.

#### Scenario: Committed label change survives a response-read failure boundary
- **GIVEN** a persistent Group and a fault that rejects Group reads after a successful mutation transaction
- **WHEN** the Group labels are committed through the versioned mutation path
- **THEN** the committed version and normalized labels are durable and the Group identity is notified
- **AND** the write path does not require a second post-commit Group read before notification

#### Scenario: Failed Group transaction cannot notify a rolled-back change
- **GIVEN** a failure inside the Group/template/labels transaction
- **WHEN** a combined metadata and label mutation is attempted
- **THEN** all writes roll back, the error propagates and no catalog notification is emitted
