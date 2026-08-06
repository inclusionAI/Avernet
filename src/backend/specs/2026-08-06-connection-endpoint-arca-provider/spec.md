# Connection Endpoint — Serving Bots on the ARCA Provider

## Summary

The public connection endpoint hands a tenant a finished chat-socket URL for one
of their bots. It works for bots whose device is owned by the BaaS provider and
fails for every bot whose device is owned by the ARCA provider — not
occasionally, but on every request, because the endpoint was built against one
provider's way of describing a connection and ARCA describes it a different way.

The two providers agree on the two facts that matter — *which device to route to*
and *what credential opens it* — and disagree only on who assembles those into a
URL. BaaS assembles one server-side and hands it over finished. ARCA hands over
the parts and expects the caller to assemble them, which is what every other
caller in the platform already does. This feature teaches the endpoint the second
shape, so that the socket it publishes is the same socket regardless of which
provider owns the device behind it.

## Motivation

**Half the fleet cannot use the endpoint at all.** Which provider owns a bot's
device is decided when the bot is created, by a rollout policy that is being
migrated provider by provider. A tenant cannot see that decision, cannot
influence it, and has no way to tell from the outside why the same API call
succeeds for one of their bots and fails for another. What they observe is an
upstream error on a bot that is running and healthy.

**The failure is silent about its cause.** The endpoint refuses to publish an
address it cannot re-address onto the gateway, which is the right instinct — but
the resulting message describes the *symptom* (there was no relay URL to work
with) rather than the situation (this provider does not issue one, by design).
Nothing in the response, and nothing short of reading the provider's source,
tells an operator that the bot is on a provider the endpoint never learned to
serve.

**The missing piece is already the platform's normal case.** Assembling
`/proxypass/{target}{path}` from a routing target is not a new behaviour to
invent. The internal console frontend does it, the shared connection helper does
it, and the BaaS layer itself does it server-side. The endpoint is the odd one
out for expecting the URL pre-built, and the gateway that fronts it already
routes exactly the targets ARCA produces.

**The two shapes converge anyway.** In production today, a BaaS-provider device
is very often an ARCA sandbox underneath: the URL BaaS returns carries an
`ARCA_…`-prefixed routing target, and after the endpoint re-addresses that URL
onto the gateway, the published result is the same grammar the ARCA provider
would have produced from its own target. The endpoint is already publishing ARCA
targets successfully; it just cannot do so when ARCA is the provider rather than
BaaS's backend.

## Worked examples

Every example below uses one bot: `bot_id=b_01k2f9`, owner `staff-9931`, active
engine `openclaw`, device on port `20003`, sandbox `ARCA-SANDBOX-abc123` with
tenant-alt suffix `@0`.

### What the provider gives the endpoint

**BaaS provider — works today.** BaaS builds the URL server-side and returns it
whole:

| field | value |
| --- | --- |
| `type` | `baas` |
| `target` | `ARCA_ARCA-SANDBOX-abc123@0:20003` |
| `token` | `eyJhbGciOiJIUzI1NiIs…` |
| `url` | `wss://agentclawproxy-prod.example.com/proxypass/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws` |
| `engine_type` | `openclaw` |
| `expires_at` | `2026-08-06T12:02:00Z` |

**ARCA provider — fails today.** ARCA returns the parts and no URL:

| field | value |
| --- | --- |
| `type` | `proxy` |
| `target` | `ARCA_ARCA-SANDBOX-abc123@0:20003` |
| `token` | `eyJhbGciOiJIUzI1NiIs…` |
| `url` | *(empty)* |
| `engine_type` | `openclaw` |
| `expires_at` | *(empty)* |

**Local provider — works today.** A device on the caller's own machine, reached
directly with no credential:

| field | value |
| --- | --- |
| `type` | `local` |
| `target` | `127.0.0.1:20003` |
| `token` | *(empty)* |
| `url` | *(not consulted)* |

### What the endpoint must publish

For the BaaS row, today and unchanged:

```
wss://gateway.example.com/openapi/v1/bots/messages/ws/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws?x-proxypass-token=eyJhbGciOiJIUzI1NiIs…
```

For the ARCA row, the same string — this feature's whole outcome:

```
wss://gateway.example.com/openapi/v1/bots/messages/ws/ARCA_ARCA-SANDBOX-abc123@0:20003/api/openclaw/ws?x-proxypass-token=eyJhbGciOiJIUzI1NiIs…
```

For the local row, today and unchanged:

```
ws://127.0.0.1:20003/api/openclaw/ws
```

The BaaS and ARCA results being identical is the point: the same bot, moved
between providers, must present the tenant with the same socket.

### The same bot on a `claude_code` engine

The engine decides the in-device path, so on ARCA the published URL becomes:

```
wss://gateway.example.com/openapi/v1/bots/messages/ws/ARCA_ARCA-SANDBOX-abc123@0:20003/api/claude_code/ws?x-proxypass-token=eyJhbGciOiJIUzI1NiIs…
```

This matters because the engine closes a socket whose pinned engine is not the
active one. On the BaaS path the provider is told which path to bake in; on the
ARCA path it is not told, and cannot be, so the path has to be applied where the
URL is assembled.

## User Stories

- As an external tenant, I want the connection endpoint to answer for every bot I
  own, so that which internal provider happens to host a bot is not something I
  can observe or have to work around.
- As an external tenant, I want the socket URL for a given bot to be the same
  shape whichever provider hosts it, so that my integration does not branch on a
  fact we never published to it.
- As an external tenant on a non-default engine, I want the URL I am given to
  address my bot's own engine socket, so that the connection is not closed
  immediately after it opens.
- As an operator, I want a bot on a provider this endpoint cannot serve to fail
  with a message naming that situation, so that I am not left reading provider
  source to explain an upstream error on a healthy bot.
- As the team operating the platform, I want the credential and routing target to
  reach the published URL exactly as the provider issued them, so that the proxy's
  check that the two agree cannot fail on a value we reshaped in transit.

## Acceptance Criteria

- [x] A bot whose device is owned by the ARCA provider gets a published socket
      URL instead of an upstream error.
- [x] For the same routing target, engine path and credential, the URL published
      for an ARCA-provider bot is byte-for-byte the URL published for a
      BaaS-provider bot.
- [x] The published URL addresses the gateway. It does not name the engine proxy,
      and does not carry the proxy's own routing prefix.
- [x] The routing target reaches the published URL unchanged, including the `@`
      and `:` its format uses, so that the credential's claim over that target
      still matches.
- [x] The credential is carried as a query parameter under the parameter name the
      proxy already accepts, and is percent-encoded.
- [x] The published URL addresses the active engine's own socket path.
- [x] The published expiry bounds the credential the response actually carries.
- [x] Where a provider supplies a URL of its own, that URL is used — composing
      happens only where none was supplied.
- [x] A provider shape the endpoint still cannot serve fails with a named error
      that says the provider's connection kind was not recognised, rather than
      describing a missing URL.
- [x] The BaaS and local paths are unchanged: same published URL, same errors, for
      every input that reaches them today.
- [x] A device the provider reports as unavailable, a bot of an unsupported type,
      and a shared bot are all refused exactly as they are today, on every
      provider.

## In Scope

- The socket URL the public connection endpoint publishes for a bot whose device
  is owned by the ARCA provider.
- The error raised for a provider connection shape the endpoint does not
  recognise.

## Out of Scope

- **The ARCA provider itself.** It already returns everything needed. It gains no
  awareness of this endpoint, no relay mode, and no URL-building duty. The
  provider lives outside this repository and this feature must not require a
  change there.
- **The BaaS and local paths.** Both work; neither changes behaviour.
- **The gateway's routing.** The gateway already serves these targets under the
  published prefix. No gateway configuration changes.
- **The credential.** Its issuer, signature, lifetime, and the fact that it is
  checked once at handshake are all unchanged.
- **Device liveness on the ARCA path.** ARCA does not report whether the sandbox
  is reachable, so a URL for a stopped device will fail at handshake rather than
  being refused up front. That is today's behaviour for every other ARCA caller
  and is not repaired here.
- **The internal console, and every other caller that assembles its own proxy
  URL.** Untouched.
- **The multi-instance, service-bot, and shared-bot surfaces.** This endpoint
  serves private personal bots only, and that gate is unchanged.

## Resolved Questions

1. **Should the fix live in the ARCA provider instead — give it a relay mode that
   issues a finished URL like BaaS does?** *Resolved 2026-08-06: no.* Three
   reasons.

   *A relay mode is not one contract.* BaaS already issues two different relay
   shapes — a routing-target URL for a sandbox-backed device, and a
   session-keyed one for its LOCAL platform, which cannot be re-addressed onto
   the gateway at all. Teaching a second provider to issue relay URLs adds a
   third producer of a shape this endpoint has to guard anyway. It moves the
   branching rather than removing it.

   *The information would travel backwards.* The provider would look up the
   engine proxy's address and format a URL around it; this endpoint would then
   discard that origin, strip the proxy's routing prefix, and recover the
   routing target and in-device path it already held. Formatting a string purely
   so the other side can take it apart again is not a contract, it is a round
   trip.

   *It would land where this endpoint cannot be tested.* The provider is in a
   separate repository. The fix would be verified by hand in a deployed
   environment rather than by the tests that cover this endpoint, and bots stay
   unserved until a second repository ships.

   *Withdrawn from this reasoning:* an earlier draft argued the provider could
   not receive the in-device path without a change to a shared base method's
   signature. That is avoidable — the provider could override the connection
   method outright, as the BaaS provider already does, and receive the path
   directly. The objection does not hold and is not part of this decision.

   *Not mutually exclusive.* See Resolved Question 4: the ordering chosen means a
   relay mode added later needs no change here.

2. **Should the endpoint identify the ARCA shape by the connection kind the
   provider declares, or by noticing that no URL was returned?** *Resolved
   2026-08-06: by the declared kind.* Inferring from an absent URL would silently
   assemble something for any provider that fails to fill the field, including one
   that was supposed to and did not — turning a bug into a plausible-looking URL
   that fails at handshake. Naming the shape keeps the unrecognised case loud.

3. **Does this reopen the earlier decision that a provider URL the endpoint cannot
   re-address is refused?** *Resolved 2026-08-06: no — it amends its scope.* The
   2026-07-31 spec's Resolved Question 3 asked whether a provider might return a
   *differently shaped URL*, and the answer — refuse it — still stands. This
   feature covers a case that question did not consider: a provider that returns
   **no URL at all**, by design, because assembling one was never its job. The
   guard on wrong-shaped URLs is unchanged.

4. **If a provider of the bare-target kind ever does supply a URL, which wins?**
   *Resolved 2026-08-06: the provider's URL.* A URL the provider took the trouble
   to issue describes a routing decision it made and we did not; composing our own
   over the top would override that decision silently. Deciding it now, rather
   than when it happens, is what makes Resolved Question 1 reversible at no cost:
   should the ARCA provider grow a relay mode later, its URL is preferred
   automatically and the composed path simply stops being reached — no change to
   this endpoint, and no window in which both could apply.

## Open Questions

None. The provider's return shape is known, the gateway route already exists, and
the target grammar is already published through the BaaS path.
