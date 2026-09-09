# Coordination reference echo v2

Large `message`/`summary` arguments must not travel in tool stdout: providers may
truncate even successful Bash output. All three coordination tools may return:

```json
{"__bcs_coordination__":true,"v":2,"tool":"bcs_assign_task","intent_id":"bcs_intent_0123456789abcdef0123456789abcdef","status":"stored"}
```

This is an additive **echo** version. Native `coordination_intent` remains v1,
with unchanged argument shapes. Inline echo v1 remains accepted. Provider v1
callbacks retain their historical tool-name trimming and tolerance for unused
status metadata; the added source-tool restriction applies only to v2. V1 ignores
`intent_id` as an unknown extension field and never resolves it. The reference
contains no arguments, task dispatch ID, consumer identity, or resolver URL.
`intent_id` identifies a stored request; an actual `task_id` exists only after
successful dispatch. A compact response fits within 512 bytes.

## Resolver Service API v1

Bootstrap injects `CoordinationIntentPort`. Public BCS has no cache vendor SDK.
The HTTP adapter uses only its configured base URL and bearer token, disables
redirects and idle connection reuse, bounds response size to 2 MiB, and never logs payloads or credentials.

All paths begin `/internal/bcs-coordination/v1/intents/{intent_id}`:

| Method / suffix | Request | Response |
| --- | --- | --- |
| GET | none | `{payload, claimed, result}` |
| POST `/claim` | `{context}` | `{acquired, claim_token, result}` |
| POST `/finish` | `{context, claim_token, result}` | immutable `result` |

`payload` has `intent_id`, `v:2`, `tool`, `arguments`, `created_at_ms`,
`expires_at_ms`. `context` has authenticated `bot_id`, `group_id`, nullable
`session_id`, `run_id`, `tool_call_id`. It must be identical across both intake
paths. `result` has `status: applied|failed|unknown`, nullable `task_id`, and a
nullable bounded `error_code`. The token is returned only on the first successful
claim, never via GET or duplicate claims. `acquired:false` with no result is an
uncertain earlier execution and never grants execution permission.

Missing/expired/evicted payloads are unavailable (404; known expired metadata
410). Cache failures are 503, mismatched claims/receipts 409. Authentication and
schema errors reject the request. Claim and result records outlive payloads;
finish may complete after payload expiry, if the claim still exists.

## Consumption

Authenticate the source and exact native tool mapping, check the live run,
resolve and validate payload identity, version, tool, arguments and expiry, then
claim. Recheck the live run before execution. Existing task services still
validate roles, targets, sessions and pending workers. Record actual dispatch
`task_id` only on success. Execution errors can follow partial side effects and
therefore receive `unknown`; a run terminated before execution receives `failed`.

GET retries transient failures up to three attempts, each at most 3 seconds,
with 200/500 ms backoff bounded by the remaining run deadline. Claim is a single
attempt. An ambiguous acknowledgement causes a status read and an error, never
execution or claim reclamation. Finish alone may retry three times; it never
repeats dispatch. Applied duplicates succeed without execution, while missing,
failed or unknown receipts surface an error. Stream intake also emits a visible
notification when system messaging is configured.

## Configuration and rollout

Absent `coordination_resolver` disables reference consumption. Example BCS TOML
(the credential is injected through the named environment variable):

```toml
[coordination_resolver]
base_url = "http://127.0.0.1:8888"
token_env = "BCS_COORDINATION_TOKEN"
```

Deploy the authenticated resolver API first, with tools still returning v1.
Deploy BCS with the resolver configured, then enable producer v2. Rollback only
the producer to v1 and retain BCS v2 resolution until existing references expire.
The producer's distributed cache retains payloads for 24 hours and claim/result
records for 48 hours. Reads do not refresh TTLs. Atomic NX creates prevent
concurrent claims while records persist. Cache eviction/loss is not durable
exactly-once delivery; a lost tool-end event does not trigger redispatch.

## Validation

Protocol parsing, HTTP retry/uncertainty tests, and dual-intake contract tests
cover long Chinese text, pretty printing, native mapping, both arrival orders,
failed reads, immutable receipts, and no re-execution after receipt loss.
