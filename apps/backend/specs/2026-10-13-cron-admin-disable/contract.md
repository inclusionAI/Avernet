# Published service-Bot cron administration

## Problem and boundary

An incident operator must be able to stop future scheduled invocations of a
service Bot without obtaining the owner's login credentials or restarting the
Bot. This additive Backend Service API uses the existing cron runtime relay;
Engine and BaaS protocols and persistence schemas do not change.

## Authorization and identity

Both endpoints require the existing authenticated user dependency. The core
service checks the authenticated staff ID against `user_config.admin.cron_admin`.
The community default is `[]`; missing, malformed or unavailable configuration
denies access. `super_admin` does not implicitly grant this scope. Operators
populate the list in their deployment overlay, never with credentials in source.

The core service resolves a unique live Bot in the repository's current
environment/tenant scope, verifies `bot_type == service`, and derives the owner
ID and name from that record. Ambiguous IDs fail closed. Requests cannot supply
an owner ID or choose another tenant/environment.

## HTTP contract

- `GET /api/cron/admin/bots/{bot_id}/tasks?runtime_stage=online`
  lists that Bot's tasks in the explicitly selected `verify` or `online` stage.
- `POST /api/cron/admin/bots/{bot_id}/tasks/{task_id}/disable`
  requires JSON `{"runtime_stage":"online","reason":"incident mitigation"}`.
  Stage is required and restricted to `verify`/`online`; reason must be nonblank
  and at most 1000 characters. Additional body fields are rejected. Task IDs
  must start with an ASCII letter/digit and contain only letters, digits, `_`,
  `.`, `:`, or `-`, with a maximum length of 256. No bulk Bot selector is allowed.

Disable calls the existing `update_cron` with **only** `{"enabled": false}`,
using the stored owner identity. It offers no enable, edit, delete or trigger
capability. Published-stage fan-out uses the existing runtime target resolver.

Responses follow the existing cron `ApiResponse` convention: HTTP 200 for domain
outcomes, with `success`, `error_code`, `message`, `data`, and `failed_targets`.
Authentication and request-validation failures retain the framework's HTTP
behavior (including HTTP 422). Permission/type rejection is envelope code 403;
missing Bot is 404; ambiguous Bot or missing owner is 409; invalid core input is
400; unexpected exceptions return a generic envelope code 500. A relay response
without an explicit successful acknowledgement, or with any failed targets,
is unsuccessful (default envelope code 502). Original per-instance results and
failed targets are preserved, so a partial disable is never reported as complete.

## Audit and operational limits

Each core disable attempt logs `CRON_ADMIN_DISABLE` with operator ID, stored
owner ID when resolved, Bot/task IDs, runtime stage, reason, outcome, error code,
and failed targets. Logging adds the timestamp; user strings are escaped to
avoid multiline log injection. Denied attempts and forwarding exceptions are
recorded. Framework rejection before the core call uses existing HTTP logs.

Disabling prevents future scheduled starts; it does not cancel an already
executing job. Only the chosen stage changes. Draft configuration is untouched,
so publishing again can restore the task's enabled state; update the owner's
draft through the existing authorized flow before republishing. Partial fan-out
may leave some instances enabled: inspect receipts and retry failed operations.
No transaction or rollback across instances is implied.

## Compatibility and verification

Existing owner/collaborator cron routes are unchanged. No database migration,
new runtime paths or credentials are required. Rollback removes the cron-admin
allow-list or reverts this additive endpoint change. Unit and HTTP contract tests
cover identity derivation, scope isolation, Bot type and ambiguity, validation,
partial failures, exceptions, audit, route mounting and DI wiring. Tests use
isolated runtime responses; they do not prove deployed containers are stopped.
