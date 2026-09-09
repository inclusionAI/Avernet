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

## Shared cache contract v2

Bootstrap injects `CoordinationIntentPort` implemented by `bcs-coordination-store`
using the already selected `CachePlugin`. MCP and BCS access the same physical
cache. Environment isolation is owned by deployment and plugin selection. No
resolver endpoint, service token, or coordination environment setting is used.
The cache plugin contract is unchanged; vendor SDKs stay outside public BCS.

Keys are `bcs:coordination:v2:{intent_id}:{suffix}` with no environment prefix.
Values at the CachePlugin boundary are UTF-8 JSON bytes. The producer writes
raw UTF-8 JSON; plugins must read that format. Plugin-specific encoding of
consumer-owned claim/result records stays inside the plugin:

| Suffix | Writer | JSON value | TTL |
| --- | --- | --- | --- |
| `payload` | MCP | `{intent_id,v:2,tool,arguments,created_at_ms,expires_at_ms}` | 86400 seconds |
| `claim` | BCS | `{context,claim_token,claimed_at_ms}` | 172800 seconds |
| `result` | BCS | `{status,task_id,error_code}` | 172800 seconds |

All writes use atomic insert-only semantics with TTL (`SET NX EX`). Reads do not
refresh TTL. The producer confirms payload creation before returning a reference.
Payload arguments are limited to 1 MiB; consumer JSON reads are limited to 2 MiB.

`context` has authenticated `bot_id`, `group_id`, nullable `session_id`, `run_id`
and `tool_call_id`, identical across both intake paths. `claim_token` is a random
32-character ownership token, local to the claim protocol, not a deployment
credential. `result.status` is `applied|failed|unknown`; `task_id` and `error_code`
are nullable. Only an acknowledged successful insert grants execution. A duplicate
claim without a receipt represents uncertain execution and never grants a lease.
Conflicting context or receipt values fail. Finish can run after payload expiry
while its claim still exists. Missing/expired payloads and cache failures fail
without falling back to inline arguments.

## Consumption

Authenticate the source and exact native tool mapping, check the live run,
resolve and validate payload identity, version, tool, arguments and expiry, then
claim. Recheck the live run before execution. Existing task services still
validate roles, targets, sessions and pending workers. Record actual dispatch
`task_id` only on success. Execution errors can follow partial side effects and
therefore receive `unknown`; a run terminated before execution receives `failed`.

Cache reads retry backend failures up to three attempts, each at most 3 seconds,
with 200/500 ms backoff bounded by the run deadline. Claim is a single attempt;
an ambiguous acknowledgement causes an error, never execution or reclamation.
Finish may retry three times and only repeats the immutable receipt write.
Applied duplicates succeed without execution; missing, failed or unknown receipts
surface an error. Stream intake emits a visible notification when configured.

## Deployment and rollout

Select the existing environment cache plugin so its physical cache, key mapping
and byte encoding match the MCP producer. No new BCS configuration is required.
Storage-backed startup passes the selected cache instance into the coordination
store; memory-only development/test constructors use a local in-memory cache.

Deploy the BCS cache consumer before enabling producer v2. The producer ships
with v2 enabled. Rollback the producer to v1 and retain consumer v2 support until
existing references expire. Old HTTP resolver configuration must be removed when
upgrading from the earlier resolver implementation.

Cache eviction/loss is not durable exactly-once delivery. A lost tool-end event
does not trigger redispatch, and an uncertain claim is never reclaimed.

## Validation

Protocol parsing and dual-intake compatibility tests preserve v1 and v2 behavior.
Cache-store conformance tests cover long Chinese text, concurrent consumers,
shared keys, immutable receipts, transient failures and lost acknowledgements.
Deployment-specific cache plugins must pass the existing CachePlugin contract,
including atomic insert-only writes with TTL. Real cross-process connectivity
and vendor byte encoding require deployment integration verification.
