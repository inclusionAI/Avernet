# AgentPass Resolve JWT Response Design

## Goal

Extend `POST /providers/agentpass/resolve` with a diagnostic representation of
the presented JWT while preserving the endpoint's existing AgentPass resolution,
provider-binding lookup, and Bot lookup behavior.

## Response shape

The existing `agent_code`, `provider_bot_binding`, and `bot` fields remain
unchanged. The response gains a `jwt` object:

```json
{
  "jwt": {
    "header": {},
    "payload": {},
    "signature": {
      "present": true,
      "byte_length": 64
    },
    "agentpass_resolved": true
  }
}
```

`header` and `payload` preserve every JSON field and value from the JWT. This is
diagnostic data supplied by the caller and must not be used as an authorization
decision. `agentpass_resolved` reports whether the existing AgentPass resolver
successfully produced an `agent_code`.

The response never includes the raw JWT or raw signature bytes. Signature
presence and decoded length are sufficient to diagnose malformed token shape
without returning reusable credential material.

## Parsing and failures

The HTTP adapter decodes the JWT header and payload with Base64URL decoding and
JSON parsing. This formatting step does not verify the signature. Authentication
continues to use `BotRuntimeTokenResolverPort::resolve_agentpass_agent_code`.

Malformed tokens do not panic and do not change the endpoint's existing HTTP
status behavior. The `jwt` object contains null header/payload values and a
bounded parse-error category, while the existing resolution fields follow their
current behavior.

## Data protection

No JWT header, claim, token, signature, or formatted response is written to
application logs. The endpoint response adds `Cache-Control: no-store` because
the payload may contain staff identifiers, account names, tenant identifiers,
email addresses, phone numbers, and other personal data.

## Tests

Contract tests cover:

- preservation of arbitrary scalar, array, object, and null JWT claims;
- decoded header output and signature metadata without signature contents;
- `agentpass_resolved` for resolved and unresolved tokens;
- malformed Base64URL or JSON without panic;
- absence of the raw token and raw signature from the serialized response;
- unchanged provider-binding and Bot lookup fields;
- `Cache-Control: no-store` on every endpoint response.
