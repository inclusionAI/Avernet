# Temporary preproduction caller-token diagnostic

When an HTTP Provider cannot parse a caller IAM credential, inspect the value
BCS submits before investigating downstream decoding or authentication.

The BCS composition roots use the existing environment resolver
(`SERVER_ENV` > `REAL_SERVER_ENV` > `ALIPAY_APP_ENV`) to enable a temporary
diagnostic only for `pre`/`prepub`. Production, gray, development, local,
empty and unknown values leave it disabled. Direct users of
`HttpProviderTransport` default to disabled unless they explicitly supply a
preproduction environment through `with_caller_token_diagnostic_environment`.

At the ordinary HTTP Provider send boundary, after URL-policy validation and
request construction and immediately before HTTP execution,
`TEMP_PREPUB_CALLER_TOKEN` records the complete `iam-token` value for
`chat.send`, together with canonical run ID, session ID, Provider IDs,
per-attempt HTTP message ID and UTF-8 byte length. Both legacy callback and
protocol-2.0 delivery use this boundary. The normal request-body log remains
redacted; other credential types and request methods do not emit this diagnostic.

`caller_token_json` is a JSON string: decode it once to recover the original
token, including whitespace and control characters. JSON quoting prevents
embedded newlines from creating additional log records. This is the value BCS
submits; it does not prove downstream receipt, parsing or successful execution.
No token is modified, exchanged or re-signed by this diagnostic, and it adds
no database/cache calls.

This temporary diagnostic deliberately exposes a live credential in
preproduction logs. Use it only for an authorized investigation with restricted
log access, never copy real tokens into issues or PRs, and remove the diagnostic
and its composition wiring after the investigation. No receiver-side changes
are needed. A BCS deployment containing this change is required before the log
can appear; source or PR status alone does not establish deployment.
