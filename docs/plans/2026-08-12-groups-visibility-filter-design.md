# Group List Visibility Filter Design

## Problem

`GET /openapi/v1/collaboration/groups` currently accepts `membership=all` as
the default and cannot filter Groups by their `visibility`. The application
therefore builds a combined direct/session relation set before applying its
other filters, even for callers that only need direct Groups.

## Contract

- The public `membership` query accepts only `direct` and `session_only`.
- Omitting `membership` selects `direct`.
- Supplying the removed value `all` returns the existing `400 invalid_request`
  error envelope.
- A new optional `visibility` query accepts `public` and `private`.
- Omitting `visibility` preserves Groups of both visibility values.
- Visibility filtering happens before `total` calculation and pagination.

This is an intentional breaking change for clients that explicitly send
`membership=all`, and an intentional default-behavior change for clients that
omit `membership`.

## Architecture and data flow

The OpenAPI contract and HTTP DTO own query-string validation. The adapter
passes the parsed optional `GroupVisibility` into the application-layer
`ListGroups` command; it does not implement filtering itself.

The Group application service continues to resolve and authorize the selected
View Actor. It selects relation candidates according to membership:

- `direct` reads Groups from the Group participant relation and does not load
  Session participant relations.
- `session_only` reads Session-related Group IDs and excludes any Group with a
  direct participant relation for the View Actor.

The existing internal `MembershipFilter::All` behavior is retained for
non-HTTP application callers to avoid widening this public HTTP contract
change into an unrelated Service API removal. Candidate Groups are filtered by
visibility together with the existing kind, strategy, and search filters,
then sorted, counted, and paginated.

This change does not add a new repository contract. Full database pushdown
would require a cross-Group/Session read model and is intentionally left to a
separate performance change.

## Errors

Serde query parsing rejects `membership=all`, unknown membership values, and
unknown visibility values at the HTTP boundary. These failures use the route's
existing `400 invalid_request` envelope. No new application error code is
introduced.

## Tests

- HTTP route tests verify `visibility` forwarding, the default direct
  membership, explicit `session_only`, and rejection of `membership=all` and
  invalid visibility.
- Application tests verify public/private filtering occurs before total and
  pagination and works for the supported membership modes.
- The focused HTTP and application test suites provide regression coverage for
  unchanged Group list behavior.
