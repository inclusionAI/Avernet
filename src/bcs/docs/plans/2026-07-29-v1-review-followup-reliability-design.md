# BCN OpenAPI V1 Review Follow-up Reliability Design

## Goal

Resolve the five actionable review findings added after commit `591810e7`
without changing Legacy API behavior or mounting the V1 production router
before a signed Gateway Principal verifier exists.

## Scope

This follow-up covers:

1. Propagating session-membership query failures to V1 callers.
2. Propagating friendship query failures during V1 eligibility checks.
3. Propagating persistent session deletion failures during runtime cleanup.
4. Rejecting duplicate participant Actor IDs before group canonicalization.
5. Declaring the existing `non_public_participant` create conflict in OpenAPI.

Production router mounting remains deferred. The existing unresolved thread
stays open until bootstrap can inject the real signed Principal verifier.

### Runtime linkage and coverage boundary

The V1 Group facade lives in the dedicated `bcs-group-v1` service crate.
Workspace unit and contract tests compile that crate, but the production
`bcs` composition root does not depend on it while the V1 router is unmounted.
This keeps runtime E2E coverage scoped to code that the deployed binary can
actually execute. When the signed Principal verifier is available, the
production mounting change must add both `bcs-group-v1` and `bcs-api-http` to
the composition root and add live V1 Singlebox stories in the same change.

## Architecture

### Backward-compatible fallible companions

Legacy core and repository methods currently return `bool` or `Vec<T>` and
convert storage failures into negative or empty results. Changing those
signatures would affect existing callers and violate the compatibility goal.

Instead, the relevant Service API contracts gain fallible companion methods:

- `FriendCoreService::try_are_friends(...) -> ServiceResult<bool>`
- `SessionRepoPort::try_list_group_ids_by_session_participant(...)`
  `-> ServiceResult<Vec<String>>`

Default implementations delegate to the existing infallible methods so test
doubles and Legacy implementations keep compiling. The database-backed
implementations override the companions and propagate query and row-decoding
failures. V1 application paths use only the fallible companions.

### Session deletion

`MySqlSessionStore::delete` must return a `ServiceError` when either the
participant-side-table delete or the session-row delete fails. Successful
deletion remains idempotent and returns whether the session row existed.
Runtime cleanup already propagates `SessionManagementService::delete` errors,
so fixing the repository result prevents Group/runtime state from reporting
successful deletion after a persistence failure.

### Participant uniqueness

V1 collaboration creation validates the supplied `participants` list before
the driver is added implicitly and before values are converted into the Legacy
management command. Any repeated `actor_id`, regardless of role equality,
returns `invalid_participant`. This removes order-dependent role selection
from downstream `HashSet` deduplication.

### Contract alignment

The POST `/openapi/v1/groups` 409 response declares both `conflict` and
`non_public_participant`, matching the existing V1 error mapping. Contract
tests pin this stable code.

## Error behavior

- Storage query failures become V1 `internal_error` responses.
- Storage delete failures abort runtime cleanup and become V1
  `internal_error` responses.
- Duplicate participants become `invalid_participant`.
- Protected participants in public group creation continue returning
  `non_public_participant`, now explicitly documented.

## Verification

Each behavior follows a red-green TDD cycle:

- Inject a failing DB plugin for session membership and deletion.
- Inject a failing friendship repository through the real `FriendCore`.
- Exercise duplicate participant creation through `GroupService`.
- Validate the OpenAPI response code set through the contract test.
- Run the affected Session Store, Session, Friend, Group, Runtime, and OpenAPI
  test suites, then the PR CI gates.
