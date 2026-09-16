# Read-only session listing

## Problem

`GET /groups/{id}/sessions` currently materializes a legacy
`{group_id}:00000000` session when a formal member lists a sessionless Group.
This read also delivers initial GroupContext messages. It prevents a Group
from remaining sessionless and conflates historical migration with normal
resource queries.

## Accepted behavior

- Listing a sessionless Group returns HTTP 200 with `items: []` and its
  `group_id`, including repeated requests and participant-scoped requests.
- Listing must not create/reactivate a Session or deliver initial context.
- Existing Session visibility, filtering, pagination, and collection
  projections retain their behavior.
- Existing legacy Sessions remain queryable with their original IDs.
- Explicit Session creation, default Group creation, history lookup, and
  WebSocket compatibility are unchanged. This change adds no CLI opt-out for
  initial Session creation.

## Compatibility

Historical Groups that never acquired a Session will display an empty list.
Callers can explicitly create one. No migration or production data deletion
is required. Reverting the change restores the old query side effect.

## Implementation plan

1. Update the HTTP contract tests to expect empty responses without Session
   writes for Bot and Human-owner callers; preserve existing legacy listing.
2. Observe the empty-list tests fail against the old implementation.
3. Remove the legacy materialization and initial-context dispatch branch.
4. Update the CLI help/reference and changelog to describe read-only listing.
5. Run session-list, explicit creation, collection, and visibility regression
   tests; check the diff and changed source-file sizes.

## Validation

- Before the handler change, the two empty-group contract tests failed because
  the response contained the newly created legacy Session; the other three
  listing tests passed.
- After the change, `cargo test -p bcs-http --test session_list_contract
  --offline` passed all 5 tests.
- `cargo test -p bcs-http --test session_create_contract
  --test session_create_request_dto --test session_collection_contract
  --test session_members_contract --test groups_contract
  --test cli_session_integration --offline` passed all 73 tests.
- `cargo test -p bcs-cli --bin bcs-cli test_session_ --offline` passed all
  14 selected tests.
- `git diff --check` passed. Independent review found no functional issues.
- Full-workspace and Singlebox coverage gates were not run; validation was
  scoped to the changed HTTP behavior, adjacent contracts, and CLI commands.

## Existing file-size debt

The changed source files were checked. The contract test has 663 lines.
`bcs-http/src/routes/sessions.rs` decreased from 2266 to 2143 lines;
`bcs-cli/src/main.rs` decreased from 7153 to 7152 lines (help text only).
Both existing files remain above the repository's 1000-line rule. This is
unresolved pre-existing debt, not a passing file-size check or a new waiver.
No CI gate was disabled or allowlist added. Splitting the session routes by
operation and separating CLI command definitions/handlers requires a separate
cleanup; doing it here would expand this narrowly scoped behavior removal.
