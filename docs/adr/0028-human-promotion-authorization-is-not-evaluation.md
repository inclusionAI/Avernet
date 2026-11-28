# Human promotion authorization is not an evaluation verdict

Status: accepted (2026-11-26). Owners: Evolution control plane and Backend Genome.
Amends the accept-only promotion condition in [ADR 0020](0020-promotion-is-platform-owned.md)
only for explicit, audited human testing/intervention; platform ownership is unchanged.

## Context

Normal automated RSI requires sandbox evaluation using platform-owned regression,
safety and business/public benchmarks before promotion. A human needs a separate
intervention path to test candidate delivery without claiming that an unevaluated
candidate passed those benchmarks. Risk-review approval alone is not delivery.

## Decision

- Record an immutable `basis=human` authorization for one Bot, Run, candidate,
  revision and frozen parent, with operator, timestamp, reason and check facts.
  Preserve the original verdict, gate and review; never synthesize `accept`.
- Permit pending/inconclusive evaluation only as this explicit human substitute.
  Reject explicit rejection, known newly failing cases, failed recorded/static
  floors, rejected/superseded reviews, incidents, kill switches, drift and stale
  parents. A missing scanner is recorded as `not_run`, not a passing check.
- Authorization alone changes no Bot content or refs. A separate Apply requires
  the original operator's current ADMIN permission and rechecks eligibility.
- The control plane uses a purpose-bound signed callback. Strategies and Workers
  receive no delivery capability. Backend verifies exact immutable strategy-run
  provenance and delivers through Genome Promotion and the existing Apply Engine.
- Reuse the authorization ID for delivery idempotency. Recover an existing receipt
  before rechecking drift changed by successful delivery. Settle active/previous
  only at successful/partial Apply completion with frozen-parent CAS. Partial is
  not full functional success; failed delivery does not advance refs.
- The ordinary automated path continues to require real platform evaluation and
  its normal promotion gate. This decision does not implement benchmark rollout.

## Alternatives and consequences

Treating a human click as `accept` would falsify evidence. Making risk-review
approval implicitly Apply would conflate review and delivery. Both are rejected.
The optional delivery adapter must be explicitly assembled; absence leaves the
manual entrypoint unavailable. Existing databases require the nullable Genome
promotion authorization column; normal promotions retain NULL authorization.
Human authorization is a recorded intervention, not a claim of quality improvement.

## Evidence and ownership

- [Evolution authorization service](../../apps/evolution/src/avernet_evolution/core/evolution_promote/manual.py)
  owns eligibility, audit and atomic authorization idempotency.
- [Backend promotion service](../../apps/backend/src/agentclaw/community/core/bot_genome/promotion/service.py)
  and [settler](../../apps/backend/src/agentclaw/community/core/bot_genome/promotion/settle.py)
  own delivery and refs.
- [Signed end-to-end tests](../../apps/backend/tests/community/adapters/http/openapi_v1/test_evolution_manual_promotion.py)
  cover separation, safety blocks, callbacks, concurrency and audit rollback.
- [Genome delivery tests](../../apps/backend/tests/community/core/bot_genome/test_manual_promotion.py)
  cover exact delivery, terminal statuses, provenance, drift and CAS.
- [Frontend tests](../../apps/frontend-nextgen/test/components/rsi/RsiManualPromotion.test.tsx)
  cover explicit actions, lost-response recovery and scope isolation.

Source contracts and hermetic tests establish behavior; they do not establish
live Bot acceptance or public/business benchmark quality.
