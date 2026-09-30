# BCS Interaction Log Unredaction Design

## Goal

Make Provider 2.0 HITL interaction business payloads visible in BCS logs so
operators can diagnose failed or invalidated interaction resolutions without
guessing the submitted action or answers.

## Scope

- Log the complete structured `interaction.resolve` request parameters,
  including `action`, `answers`, and Provider extension fields.
- Log the complete Provider `interaction` SSE data, including `command`,
  `questions`, and `answers`.
- Keep existing credential and transport-secret protections unchanged,
  including authorization tokens and temporary attachment URLs.
- Do not add a configuration switch in this change.

## Design

The change stays in the `bcs-provider-http` delivery adapter, which already
owns Provider request and SSE detail serialization for logs.

`provider_body_log` will continue serializing the structured Provider request
and redacting temporary attachment URLs, but it will no longer replace
`interaction.resolve` business parameters with `<redacted>`.

`sse_data_log` will return Provider SSE data unchanged for `interaction`
events, matching its existing behavior for other SSE event types. This removes
the special interaction-only projection that currently drops business fields.

No wire protocol, Service API, Plugin API, persistence model, or runtime
routing behavior changes.

## Error Handling

Serialization failures keep the existing safe `serialize_error` fallback.
Temporary attachment URL redaction remains independent of interaction payload
logging. The change does not alter Provider request execution or SSE parsing.

## Testing

- Change the Provider request-body log test to require the original
  `action`, answer values, and Provider extension content.
- Change the SSE detail log test to require the original interaction command
  and answer values.
- Keep the attachment URL regression test unchanged to prove transport-secret
  redaction still applies.
- Run the focused `bcs-provider-http` tests, then the relevant BCS checks.

## Compatibility and Risk

The runtime protocol is unchanged. The operational risk is increased exposure
of user-provided HITL business content in BCS logs; this is intentional and
limited to interaction payloads. Authentication credentials, authorization
headers, and temporary attachment URLs remain protected by their existing
independent redaction paths.
