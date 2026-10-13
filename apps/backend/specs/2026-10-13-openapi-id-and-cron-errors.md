# OpenAPI decimal identifiers and cron error statuses

## Problem

Malformed SkillSet and shared Repo Skill path identifiers reach repository
`int()` conversions and answer HTTP 500. Cron business rejections carrying
400, 403, or 409 are flattened to the generic relay HTTP 502 fallback.

## Contract

- SkillSet `set_id`, including Skill Center Reference addresses, and shared
  Repo Skill `skill_id` remain strings on the HTTP and Service API surfaces.
  The HTTP path accepts ASCII decimal digits; other formats answer 422.
- A well-formed decimal identifier for an unavailable resource continues to
  answer 404. Existing resource lookups and authorization rules are unchanged.
- `CronRelayError` with 400, 403, or 409 preserves that HTTP status and the
  matching six-digit envelope code. Its internal message is not public.
- Other relay errors retain the generic 502 response. `CronApiTimeoutError`
  retains its specific 504 status and fixed public message.

## Scope and compatibility

This changes public HTTP validation and error translation only. No Service API,
Plugin API, persistence schema, stored data, or runtime behavior changes.
Gateway consumers need the regenerated backend OpenAPI artifact for the path
patterns. Invalid-input clients should handle 422; cron callers must distinguish
business rejection from upstream failure. Rollback restores the HTTP adapters.

OCB composes Community and Corp code and consumes the backend schema in its
gateway. Its integration must preserve the pinned Avernet tree and apply this
fix rather than substitute a divergent public branch. Validate Corp DI and
backend interfaces with that exact integrated source.

## Reviewed test expectations

- `open-folder`: `{}` opens the workspace root; omitting the required body
  answers 422. Keep the existing contract.
- `auth-status`: a pending authorization can precede a persisted Bot. Preserve
  creation completion on ISSUED and the public PENDING answer when Passport is
  not ready. Do not gate this poll on a persisted Bot's existence.
- Routine run and Repo sync have no request-body contract. An empty or absent
  body alone is not an error. Upstream failures require runtime evidence.

## Acceptance

Assembled backend interface tests exercise malformed and nonexistent numeric
identifiers through real repositories. A real cron relay with a non-ACTIVE
device produces 409 without forwarding; the public PATCH must also answer 409.
An ACTIVE-device control continues forwarding successfully. Mapper tests cover
400/403/409, fixed messages, 502 fallback, and timeout subclass precedence.
