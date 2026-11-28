# Task Guard API logging

Task Guard is the workflow workspace (`/workflows/workspace`). This policy owns
its workflow/runtime API logs; it is not a new policy for every ClawWeb module.

## Severity and exception evidence

| Event | Severity |
| --- | --- |
| Successful, redirected, or pending requests | Existing INFO behavior |
| Expected HTTP 4xx business failures, including 401, 403, 404, 409, and 429 | WARN |
| HTTP 5xx system failures and caught or unhandled system exceptions | ERROR, with a stack |

`internal-logger.ts` writes the same severity to the console and the existing
internal-API file. HTTP status metadata takes precedence over a domain run
status. A route that catches an exception passes its original stack through
logging-only diagnostics instead of reducing it to a message. The response's error message,
status, and body are not changed. File-write failures remain system errors.

A failure response without a thrown exception can only provide a stack at the
failure observation/logging site, not an original throw site. Do not interpret
that diagnostic stack as evidence of an underlying exception. Original exception
stacks take precedence when available and permitted by the existing privacy
boundary. AI draft and protected Pack operations must retain their fixed-metadata
logging restrictions: an exception message/stack can contain credentials, prompts,
or complete workflow content. In those paths, any added ERROR diagnostic uses
fixed safe metadata and an explicitly identified observation stack, never the
original sensitive exception text. Ordinary stack logging must not inspect
Error extension properties such as HTTP headers, cookies, or request config.

Multi-argument console diagnostics use a literal format string, with IDs and
messages passed as `%s` data arguments and the original stack kept separate.
Request-derived `%s`, `%d`, `%j`, and `%%` text must remain literal; it must not
consume, reinterpret, or truncate the stack or approved structured metadata.

## Completion and stale-node reconciliation (PUT)

The public internal logger records one outcome per handled closeout, not a
request preview. Its only request-specific field is the numeric HTTP status;
method and path are `PUT` and one of two fixed templates:

- `/runs/:flowId/completion`
- `/node-executions/:flowId/reconcile-stale-running`

These routes do not enumerate body keys or capture request/response bodies,
headers, IDs, query strings, domain status, or exception details for logging.
On 5xx the file, console summary and HTTP diagnostic handoff use a fixed
observation, **not a call stack**. If the completion handler's terminal observer
rejects, the route wrapper emits only a fixed warning. The separate
`fail-if-running` caller keeps its bounded, redacted ID, message and stack.
Responses, persistence and observer delivery are unchanged.

**Boundary:** metadata-only applies to these handlers' request logs, not to all
logs for the request. Repository diagnostics and independent business observers
have their own logging policies and can still record IDs. Host HTTP middleware
is also separate. In particular, the default standalone public host's JSON
parser can reject a request before either handler runs: **audit its existing
parser-error logging separately**. This change does not alter the parser, host
error handling, or background workflows. See [shared diagnostic redaction](../../../../shared/server/logging/README.md).


## Scope and compatibility

The enterprise HTTP middleware applies this policy to an explicit set of Task
Guard routes, including workflow/run APIs and their internal write/read
counterparts. It must not broadly match `/api/internal`, nor change Insight,
Evolve, Bench, or unrelated repair API logging. Existing redaction, fixed log
paths, and retention behavior remain in force.

The shared `httpLogLevel` default remains unchanged for other consumers; merely
supporting WARN in the text formatter does not opt another API into this policy.
This change does not fix missing resources, authorization failures, database
behavior, retries, or polling frequency.

## Verification

- `__tests__/internal-logger.test.ts`: console/file severity, original stacks,
  and best-effort logging behavior.
- `__tests__/console-format-safety.test.ts`: literal format-token handling on
  route failures, exact original stacks, and unchanged response/metadata contracts.
- Task Guard route regression tests: response compatibility and caught exception
  evidence.
- Enterprise middleware tests: route boundaries, warning/error channels, response
  compatibility, and payload redaction.

Public and enterprise sources are delivered separately. Local test success is
not evidence of integration, a deployed version, or changed monitoring counts.

## API contracts

- [Execution record GET](../../docs/execution-record-contract.md):
  `GET /api/runs/:flowId/execution-record` returns the persisted execution snapshot;
  optional `includeNodeOutput=true` includes stored node output (omitted by default).
  This route retains host identity requirements but does not enforce workflow/run
  view grants; reader-enabled mounts still reject fanout payloads from stored state.
  Other run read routes keep their existing authorization.
