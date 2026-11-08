# Gateway chat authentication

The `/openapi/v1/chat` message and session routes authenticate the
`x-avernet-principal` JWT before reading resource-key authorization.

| Failure | HTTP status | `detail.code` | Behavior |
| --- | --- | --- | --- |
| Missing principal | 401 | 40101 | Reject before reading the signing secret. |
| Malformed, invalid or expired JWT | 401 | 40103 | Reject after verifying with the configured secret. |
| Signing-secret lookup fails or returns an empty value | 503 | 50301 | Fail closed with `Authentication service unavailable`; do not expose secret-store exception details or query resource-key authorization. |

Signing-secret failures are server dependency/configuration failures, not
evidence that the caller's token is invalid. This applies to both community
stub stores and enterprise secret-store implementations. Successful JWT
validation and resource-key authorization retain their existing behavior.
