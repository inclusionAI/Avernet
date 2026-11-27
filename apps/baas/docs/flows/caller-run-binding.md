# Caller run binding and instance-status gate

## Scope and ownership

A run may carry `user_id` in its metadata to address a per-user caller sandbox.
The bot-run runner decides caller mode and defers binding to a background
dispatch; `BotBindingResolver` selects the binding branch and
`BotServicePlugin` either queries status or reuses a connection. The remote
expert-chats service owns the caller-instance ledger and the liveness
observation. Source and tests at the target revision are authoritative; this
page does not establish which revision is deployed.

## Mode admission

- `is_caller_mode` is true only when metadata carries `user_id` and the
  resolved binding engine is not `teclaw`; missing `binding_info` is treated
  conservatively as non-caller.
- A `caller_token` is optional. When present, the credential branch validates
  it; when absent, the instance-status gate below applies.
- Caller tokens stay in the in-memory request path; persisted run metadata is
  stripped with `strip_sensitive_metadata`.

## Binding branches

`resolve_caller_binding` requires `user_id` (`CONFIG_INVALID` otherwise) and
then:

1. **Token present** — validate the credential `type` fail-closed, request the
   instance through `POST /api/v1/expert-chats/app-caller-connection` with a
   self-minted app-principal JWT, poll until ready, then install the caller
   runtime identity once. Install failure is not retried.
2. **`user_id` only** — call the read-only `get_caller_instance_status`
   (`GET /api/v1/expert-chats/app-caller-instance/status`, BaaS service JWT
   with `iss=baas`). Both `exists` and `alive` must be true; otherwise the
   binding fails with `NOT_FOUND` before any container is created. A passing
   status reuses the instance through `get_existing_caller_connection` and
   installs nothing — the runtime credential was installed when the instance
   was created with a token.

Binding runs in the runner's background caller dispatch. A binding or dispatch
error marks the run `FAILED` with that error; the request path does not retry.

## Failure boundaries

- Missing `user_id`, missing or unsupported credential `type`, or missing
  `bot_service` base URL / principal signer: `CONFIG_INVALID`, and no container
  is created.
- `exists=false` or `alive=false`: `NOT_FOUND`. The status API is an
  observation — it does not create, recover, restart or re-sign an instance.
- A status business failure with `error_code=403` (source bot not found or
  domain rejection) maps to `NOT_FOUND`; other business failures map to
  `PLATFORM_ERROR`. Neither means `exists=false`, so an error must not be read
  as "no instance".
- Missing `data`, non-boolean `exists`/`alive`, or `alive=true, exists=false`
  violates the response contract and fails the binding.
- Transport, HTTP status or envelope failure propagates `PaasError`; the status
  query has no retry or polling.
- The local/singlebox plugin raises `PLATFORM_UNAVAILABLE` for both
  `get_caller_instance_status` and `get_existing_caller_connection`; caller
  mode depends on the remote bot-service capability.

## Contracts and evidence

- [Mode predicate](../../src/secbaas/community/core/service/bot_run/_bot_run_utils.py)
- [Resolver branches](../../src/secbaas/community/core/service/bot_run/_binding_resolver.py)
- [Background dispatch and failure marking](../../src/secbaas/community/core/service/bot_run/_runner.py)
- [SPI methods](../../src/secbaas/community/spi/bot_service/_protocols.py) and [status model](../../src/secbaas/community/spi/bot_service/_models.py)
- [Real plugin](../../src/secbaas/community/plugins/bot_service/real/_plugin.py), [local plugin](../../src/secbaas/community/plugins/bot_service/local/_local_plugin.py), [stub plugin](../../src/secbaas/community/plugins/bot_service/stub/_stub_plugin.py)
- [Status JWT minting](../../src/secbaas/community/plugins/bot_service/real/_principal_signer.py)
- [Mode predicate tests](../../tests/unit/core/service/bot_run/test_bot_run_utils.py)
- [Plugin status and connection tests](../../tests/unit/plugins/bot_service/test_bot_service_plugin.py)