# Internal Candidate Search Optional Principals Design

- **Date:** 2026-08-19
- **Status:** Approved
- **Scope:** `GET /api/v1/collaboration/bots/{bot_id}/candidates/search`

## Problem

The Gateway route `/api/v1/collaboration/**` admits User, App, and Bot
Principals as optional inputs, but the internal candidate-search OpenAPI
operation still declares User and App as required. The checked-in contract
therefore describes a stricter Gateway boundary than the deployed Gateway
configuration.

BCS already owns the real authorization policy. `BotServiceImpl` requires an
authenticated Human and then verifies that the selected physical Bot is
managed by that Human, or that the Human Actor perspective represents the same
Human. App and Bot Principals do not replace the required Human identity.

## Decision

Declare all three Gateway Principal inputs as optional for this operation:

```yaml
x-avernet-security:
  user: optional
  app: optional
  bot: optional
```

This extension describes only Gateway admission. The operation description and
403 response continue to document the stricter BCS application policy: a
request without a usable Human identity is forbidden, and an authenticated
Human may use only an authorized candidate-search perspective.

Do not add a new `x-bcn-identity-policy: human_only` value. The repository has
no established contract vocabulary or validator for that value, so the
application policy remains explicit in prose, tests, and implementation.

## Compatibility and Validation

The change relaxes Gateway admission metadata and does not alter paths,
parameters, response schemas, or Rust behavior. Contract tests lock the three
optional inputs and the BCS-owned authorization description. The deterministic
Gateway internal OpenAPI snapshot is regenerated from `internal.yaml` and its
snapshot test locks the same metadata.
