# Session-scoped Caller token updates for TeClaw

## Requirement

When an existing Caller-enabled service Bot uses a TeClaw runtime, refresh its Caller credential in the session that is sending the message. The IAM boundary accepts the original `sessionKey`; the BaaS PaaS outbound boundary receives `mode=append` and `session_key`. Business Bot types remain unchanged. Non-TeClaw Caller behavior and base outbound rules remain compatible.

## Existing boundaries and allowed changes

The existing chain is IAM HTTP adapter -> Caller application service -> Caller identity exchange -> runtime updater -> BaaS Caller appender. Extend its contracts and implementations with optional session context. Resolve TeClaw devices through the existing authorized binding and current-device lookup; do not treat the logical Bot ID as a PaaS device ID.

The BaaS PaaS facade already supports session append. No BaaS, Engine, Relay, JIT, Passport, or Bot-type migration is part of this change. Corporate runtime-updater and frontend implementations live outside this source repository and must adopt the compatible parameter when integrating this version.

## Session context validation

Revision (2026-10-11): remove the runtime `/api/sessions` ownership lookup and multi-target session matching. Existing Bot permissions and authorized runtime-binding selection remain the access boundary. For TeClaw bindings, require a nonempty session key and forward the original value; do not verify session owner/agent response fields or exclude targets based on a session-list response. A lightweight validator only reads the binding provider and resolves the existing authorized binding when necessary.

## Outbound contract

Keep the PaaS `device_id@template_id` intact. Use HTTP query parameters to encode the original key once; do not Base64, trim, rewrite, or concatenate it into a URL. Continue using the configured BaaS client, existing timeout, `x-caller-token` header rule, `set` action, and existing domains. Use APPEND rather than REPLACE to preserve base rules. Require the existing successful HTTP and business response code.

## Observability

Record existing IAM and BaaS append request/success/failure events. Session keys are diagnostic business identifiers in TeamClaw and must be logged as supplied. Remove `boundary_logging.py`, `redact_boundary`, HTTPX filters and recursive/encoded/oversized-payload protection. Keep a simple mask for the actual IAM token and outbound Caller token rule values; use normal response diagnostics and preserve the existing error contract.

## Verification

Behavior tests cover parameter propagation across real services and protocols; nonempty TeClaw session keys; absent/blank keys; multi-target updates without ownership filtering; special-character query encoding; current-device uniqueness; compatible non-TeClaw and no-Bot requests; unavailable implementations; test-exchange key validation; HTTP/business/transport failures; and request/success/failure logging with plaintext session keys and masked actual token fields.

Run affected unit and contract tests, architecture/DI checks, unused-import/variable lint, and the complete Backend CI suite. Record actual case counts and coverage denominators. The task requires all cases to pass and changed-line coverage of at least 90%; do not lower repository gates or introduce coverage exclusions. Remote PR checks are independent evidence and remain pending until their actual jobs finish.

## Delivery

Rebase the feature commits onto the latest source `dev` branch, then open a source PR targeting `dev`. Do not merge or deploy as part of this task. Any downstream integration must verify that the selected source revision is available through its configured repository before updating a submodule reference.
