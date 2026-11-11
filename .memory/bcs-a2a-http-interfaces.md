---
name: bcs-a2a-http-interfaces
description: BCS A2A interface URLs accept HTTP and HTTPS while retaining outbound network policy.
type: decision
---

## Why

Internal A2A services may use plain HTTP beyond loopback. AgentCard admission and route derivation must not impose an HTTPS-only restriction on those endpoints.

## How to apply

Keep `http_interface_address_allowed` shared by admission and route derivation, accepting HTTP/HTTPS with a host and rejecting userinfo, fragments and surrounding whitespace. Continue enforcing `OutboundUrlGuard`, DNS pinning and deployment-specific private-network policy separately; HTTP support does not authorize every internal IP. HTTP is unencrypted, including any configured credentials.

## Related

- [[../apps/bcs/crates/contracts/bcs-domain/CONTEXT.md]]
- [[../apps/bcs/crates/external-clients/bcs-a2a-http/CONTEXT.md]]
