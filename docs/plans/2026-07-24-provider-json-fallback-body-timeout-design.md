# Provider JSON Fallback Body Timeout Design

## Context

Protocol 2.0 `chat.send` requests that prefer SSE use a client without a total
or per-read timeout so that a healthy long-lived event stream is not cut off.
The same request may legally fall back to an `application/json` acknowledgement.
Once response headers arrive, the response-header timeout no longer applies, so
a provider that sends an incomplete JSON body and keeps the connection open can
leave `deliver()` pending indefinitely.

## Considered Approaches

1. Restore a client-wide total or read timeout. This would bound the JSON body,
   but would also terminate valid long-lived SSE responses, undoing the purpose
   of the dedicated SSE client.
2. Rely on reverse-proxy or upstream connection timeouts. This is deployment
   dependent and does not preserve the transport adapter's liveness contract.
3. Apply a timeout only while decoding the JSON fallback body. This preserves
   unlimited SSE lifetime while bounding the finite acknowledgement body.

## Decision

Use approach 3. Add a private JSON acknowledgement reader that wraps
`reqwest::Response::json::<ProviderAckResponse>()` in
`tokio::time::timeout`. Use the existing 125-second response-header timeout as
the JSON body timeout so the fallback remains bounded without making the
deadline stricter than the current request-header phase.

The helper distinguishes timeout from JSON decoding failure. `deliver()` logs
and returns a clear `ServiceError::InternalError` in both cases, preserving the
existing decode-error behavior while adding an explicit timeout path.

## Test Design

Add a focused unit test using a real local TCP response. The server sends a
successful `application/json` response header and only a prefix of the declared
body, then keeps the socket open. The test invokes the body reader with a short
duration and verifies that it returns the timeout variant rather than remaining
pending. Existing provider transport contract tests continue to cover complete
JSON fallback acknowledgements.

## Scope

Only `bcs-provider-http` changes. No protocol contract, public API, dependency,
or SSE stream behavior changes.
