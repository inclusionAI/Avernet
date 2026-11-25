## ADDED Requirements

### Requirement: Agent management replacement and readback
Agent registration and explicit updates SHALL accept top-level `catalog_labels`, separate from raw Card JSON. Registration omission SHALL initialize empty labels. An otherwise valid Agent explicit update omitting labels SHALL preserve existing labels from the latest record on each CAS attempt; supplying an object SHALL replace the entire collection after shared validation. A label-only explicit object, including `{}`, SHALL be a valid update. Existing completely empty update requests SHALL remain invalid. Register/Get/Find/Update/Refresh views SHALL return normalized labels.

#### Scenario: Legacy Agent writer preserves labels
- **WHEN** a valid source-only or visibility-only update omits labels for an Agent previously labeled running
- **THEN** existing labels remain unchanged and the response/publication reflect that result

#### Scenario: Label-only replacement
- **WHEN** an authorized caller updates only `catalog_labels` to `{"lifecycle":["offline"]}` or `{}`
- **THEN** labels are replaced or cleared without fetching a URL or requiring a new Card source

#### Scenario: Preserve existing all-empty rejection
- **WHEN** an Agent update provides no source, no visibility and no explicit label object
- **THEN** the existing `UPDATE_REQUIRED` rejection is preserved

### Requirement: Group creation and PATCH preserve-or-replace
Eligible normal manager-worker/state-machine Group creation SHALL accept labels through the existing Group create route and return them in detail. Group PATCH omission SHALL preserve labels; an explicit object SHALL replace the entire collection, with `{}` and objects normalized to empty clearing it. No Group Card upload or Group Triple service SHALL be required. Non-empty labels for Chat/DM Groups SHALL fail explicitly; their existing omitted/empty requests SHALL remain compatible.

#### Scenario: Old Group PATCH does not clear labels
- **WHEN** a name/context/visibility PATCH omits labels
- **THEN** stored labels remain unchanged

#### Scenario: Supplied object is a full replacement
- **WHEN** an eligible Group PATCH explicitly supplies `{}` or `{"lifecycle":[]}`
- **THEN** all prior custom labels are removed atomically

#### Scenario: Private Group later published
- **WHEN** an eligible private Group has validated labels and later becomes public through a PATCH omitting labels
- **THEN** labels survive and the existing publication eligibility rules apply

#### Scenario: Existing Group returned from idempotent creation
- **WHEN** a create request omitting labels recovers an already existing Group
- **THEN** the existing Group's labels are preserved rather than reinitialized

### Requirement: Single-source additive persistence
The stores SHALL add `catalog_labels` to `bcs_bots` and `bcs_group_agent_cards` using new migrations, without editing committed historical migrations. SQL NULL/missing legacy Card rows SHALL read as empty; invalid non-null stored label JSON SHALL surface as a storage error. Labels SHALL NOT be duplicated in raw Card documents, bot_info, bcs_groups or A2A runtime tables. Memory and persistent implementations SHALL obey the same semantics.

#### Scenario: Existing rows and existing inserts
- **WHEN** the new server reads an old NULL column value or an old writer inserts a row on the expanded schema without the new column
- **THEN** the result reads as `{}` without a request-time schema change

#### Scenario: Missing eligible Group template row
- **WHEN** an eligible historical Group without a Card row receives an explicit label write
- **THEN** the normal template and labels are created together in the existing Group transaction; reads alone do not backfill

### Requirement: Atomicity authorization and refresh preservation
All input validation and existing authorization SHALL precede persistence. Combined metadata writes SHALL commit atomically with existing identity/revision fencing. Manual/background URL refresh and unrelated Group/template maintenance SHALL preserve labels and SHALL NOT replay stale copied label values. Successful label changes SHALL invalidate affected metadata and notify catalog publication only after commit.

#### Scenario: Invalid mixed update
- **WHEN** a request includes a valid name/Card change and invalid labels
- **THEN** neither change is persisted and no catalog success notification is emitted

#### Scenario: Concurrent label update and URL refresh
- **WHEN** a URL refresh races with a successful label replacement
- **THEN** superseded results are fenced or preserve the latest labels; refresh cannot restore an earlier label map

#### Scenario: Storage failure
- **WHEN** label persistence fails inside a combined update
- **THEN** no partial Card/Group success is reported and no committed-change notification is emitted

### Requirement: Creation MUST NOT bypass existing Group management authorization

Normal Group creation MUST be create-only, including legacy caller-supplied IDs.
An existing identity MUST conflict atomically without replacing its management
anchors, participants, kind/strategy, version, Card template or labels. An exists
check followed by an unconditional upsert MUST NOT implement this boundary.
Authorized PATCH and DM pair-based reuse retain their separate contracts.

#### Scenario: Existing Group identity cannot be taken over through creation
- **GIVEN** an existing Group with labels and an unrelated caller who cannot PATCH it
- **WHEN** the caller submits a valid normal-Group creation request with that same ID
- **THEN** creation returns conflict and the complete existing Group and labels are unchanged
- **AND** no committed catalog change notification is emitted for the rejected creation

#### Scenario: Concurrent creators cannot overwrite the winning resource
- **GIVEN** two otherwise valid create commands with the same ID and different management anchors or labels
- **WHEN** both race against an initially absent identity
- **THEN** exactly one creation succeeds and the other conflicts
- **AND** memory and persistent stores retain the winner's complete identity and labels
