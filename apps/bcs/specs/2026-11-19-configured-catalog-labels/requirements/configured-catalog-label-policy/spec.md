## ADDED Requirements

### Requirement: Config-owned business dictionary and multiplicity
The application SHALL validate Agent and Group catalog labels using one startup-loaded `catalog-labels.toml` policy. Every business key SHALL declare `allowed_values` and `allow_multiple`; no business key or enum value SHALL be hardcoded in production validation. The initial configuration SHALL declare `lifecycle` with values `draft`, `trial`, `running`, `offline` and `allow_multiple = false`. Empty labels SHALL be valid without a default lifecycle.

#### Scenario: Initial single-valued lifecycle
- **WHEN** a caller provides `{"lifecycle":["running"]}` with the initial policy
- **THEN** validation succeeds and no other state is inferred or added

#### Scenario: Disallowed lifecycle multiplicity
- **WHEN** a caller provides `{"lifecycle":["draft","running"]}`
- **THEN** the entire request is rejected before any mutation

#### Scenario: Generic multi-valued key
- **WHEN** a test/deployed dictionary declares another key with `allow_multiple = true` and multiple allowed values
- **THEN** a subset containing multiple distinct allowed values succeeds without production code changes

### Requirement: Strict label object normalization
The delivery boundary SHALL reject top-level null, non-object labels, unknown keys, non-array/non-null values, non-string array entries and unknown enum values. It SHALL validate keys even for deletions, use exact case-sensitive strings, deduplicate equal strings, remove null/empty-list entries and enforce multiplicity on distinct values. The normalized type SHALL contain only maps of non-empty string lists.

#### Scenario: Reject null object but accept a deletion entry
- **WHEN** a request supplies `catalog_labels: null`
- **THEN** it fails; a separate valid request supplying `catalog_labels: {"lifecycle": null}` normalizes to `{}`

#### Scenario: Empty arrays and duplicates
- **WHEN** the caller supplies `{"lifecycle":[]}` or `{"lifecycle":["trial","trial"]}`
- **THEN** the normalized result is respectively `{}` or `{"lifecycle":["trial"]}`

#### Scenario: Unknown key cannot be hidden by deletion
- **WHEN** an unknown key is supplied with null or an empty list
- **THEN** validation rejects that key instead of discarding it silently

#### Scenario: Invalid list member
- **WHEN** a value is a number, nested object, null array element, blank string or a case-modified unlisted value
- **THEN** the entire operation fails with field-specific invalid-request details and no persisted partial result

### Requirement: Environment-independent fail-closed policy loading
The policy SHALL be loaded once from the selected config directory, independently of environment overlays, and injected into application services. Missing/malformed policy, missing multiplicity, conflicting system keys or invalid enum definitions SHALL fail startup. An explicitly empty dictionary SHALL accept only empty labels. Policy changes SHALL require a controlled restart; no request SHALL choose or extend its dictionary.

#### Scenario: Every environment sees the same policy file
- **WHEN** environment selection changes while the config directory and policy file stay the same
- **THEN** allowed business keys/values and multiplicity remain identical

#### Scenario: Policy cannot silently become permissive
- **WHEN** the file is absent/malformed or the loaded dictionary is explicitly empty
- **THEN** startup respectively fails, or only empty label requests are accepted

#### Scenario: Reserved key in policy
- **WHEN** the dictionary declares `visibility` or `user_visibility` as a business key
- **THEN** startup rejects the dictionary and system authorization dimensions are not writable via catalog labels
