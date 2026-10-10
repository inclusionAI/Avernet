# Session-scoped Caller token updates for TeClaw

## Requirement

When an existing Caller-enabled service Bot uses a TeClaw runtime, refresh its Caller credential in the session that is sending the message. The IAM boundary accepts the original `sessionKey`; the BaaS PaaS outbound boundary receives `mode=append` and `session_key`. Business Bot types remain unchanged. Non-TeClaw Caller behavior and base outbound rules remain compatible.

## Existing boundaries and allowed changes

The existing chain is IAM HTTP adapter -> Caller application service -> Caller identity exchange -> runtime updater -> BaaS Caller appender. Extend its contracts and implementations with optional session context. Resolve TeClaw devices through the existing authorized binding and current-device lookup; do not treat the logical Bot ID as a PaaS device ID.

The BaaS PaaS facade already supports session append. No BaaS, Engine, Relay, JIT, Passport, or Bot-type migration is part of this change. Corporate runtime-updater and frontend implementations live outside this source repository and must adopt the compatible parameter when integrating this version.

## Session authorization

A session key is untrusted input and is not proof of ownership. Before exchanging a Caller token, inspect the requested session on each authorized fixed TeClaw runtime through the existing device-adapter session-listing contract, scoped to the authenticated user and Bot. Match the original session identifier and owner; never create or register a session to authorize it.

Missing or blank session context is invalid for a TeClaw Caller target. In a multi-runtime refresh, exclude TeClaw targets that explicitly do not own the requested session; at least one TeClaw target must match when TeClaw targets exist. Transport failures remain errors. Keep the existing per-target update aggregation, and distinguish its success from evidence that the actual chat runtime has been updated.

The authorizer is a required dependency of the real Caller services. Community/local implementations preserve their existing no-op or unavailable semantics without installing credentials.

## Outbound contract

Keep the PaaS `device_id@template_id` intact. Use HTTP query parameters to encode the original key once; do not Base64, trim, rewrite, or concatenate it into a URL. Continue using the configured BaaS client, existing timeout, `x-caller-token` header rule, `set` action, and existing domains. Use APPEND rather than REPLACE to preserve base rules. Require the existing successful HTTP and business response code.

## Observability

Record stable request, success, and failure events at the IAM, session-lookup, and BaaS append boundaries. Include system, direction, operation, request/operation identity, method/route, full non-sensitive business inputs/outputs, status/result, and duration. Recursively redact credentials and session material, including token rule values, nested errors, encoded credential echoes, and exception URLs. Do not log original session keys, cookies, authorization headers, or raw HTTP exceptions.

## Verification

Behavior tests cover parameter propagation across real services and protocols; valid TeClaw sessions; absent/blank/foreign sessions; fixed-runtime ownership; multi-target filtering; special-character query encoding; current-device uniqueness; compatible non-TeClaw and no-Bot requests; unavailable implementations; test-exchange authorization; HTTP/business/transport failures; and request/success/failure logging without plaintext credentials.

Run affected unit and contract tests, architecture/DI checks, unused-import/variable lint, and the complete Backend CI suite. Record actual case counts and coverage denominators. The task requires all cases to pass and changed-line coverage of at least 90%; do not lower repository gates or introduce coverage exclusions. Remote PR checks are independent evidence and remain pending until their actual jobs finish.

## Delivery

Rebase the feature commits onto the latest source `dev` branch, then open a source PR targeting `dev`. Do not merge or deploy as part of this task. Any downstream integration must verify that the selected source revision is available through its configured repository before updating a submodule reference.
