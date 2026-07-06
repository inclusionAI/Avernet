# Protocol Contract Tests (Rule 25)

Every Plugin Protocol under `agentclaw.plugins` has a conformance test
suite under `src/backend/tests/contracts/test_<plugin>.py`. The arch
test `tests/architecture/test_protocol_contracts.py` enforces the
mapping with a shrinking `EXEMPT_PROTOCOLS` set.

**Quality gate:** the arch test only enforces that a suite *exists*. It
cannot verify the suite is meaningful — that's `OWNERS`' job. The
`tests/contracts/OWNERS` file lists the gatekeeper reviewers (same set
as `src/agentclaw/plugins/OWNERS`); they approve every change under
`tests/contracts/` to keep the suites honest.

## What conformance means here

Plugins exist because prod impls can't run locally (network, VPN,
external services). Runtime impl-to-impl parity is therefore not
machine-checkable in CI. What we *can* pin down is:

- The **consumer's** assumption about the Protocol — that the
  upper-layer service which depends on the plugin behaves correctly
  when wired against a known impl.
- The **local impl's** behaviour — used as the executable spec of
  "what we believe prod does."

So "conformance" here is **consumer ↔ Protocol**, with the local impl
as the spec. Prod-vs-local parity remains a human discipline at
impl-authoring time and code review.

## Suite shape

1. Use the `world` fixture (from `tests/framework/fixtures.py`).
2. `world.get(SomeConsumer)` returns the upper-layer service / domain
   function under test, wired against a per-test injector with local
   plugin impls.
3. Exercise the consumer through its public API — at least one happy
   path and one failure path.
4. Assert observable outcomes from the consumer.
5. **Assert the plugin was actually invoked** — inspect the mock's
   recorded calls. Without this, a consumer could silently bypass the
   plugin and the test would still pass.

If the local impl in the wired graph can't record calls, upgrade it to
a mock-flavored impl (`@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.MOCK)`)
or add a sibling Mock and swap the binding.

## When to write one

- Adding a new Plugin Protocol: add the suite in the same PR. The
  arch test rejects merges that introduce a Protocol without one.
- Modifying an existing Protocol's behaviour: update the suite to
  reflect the new contract. The consumer test is your spec.

## Worked example

`tests/contracts/test_cache.py` is the canonical reference:

```python
from agentclaw.core.skill_center.services.skill_cache import MarketCache
from agentclaw.plugins.cache import CachePlugin


def test_marketcache_set_then_get_round_trips_through_cache_plugin(world) -> None:
    mc = world.get(MarketCache)
    payload = {"skills": [{"id": "s1", "name": "Test Skill"}]}

    mc.set("market:test", payload)

    # Consumer-level assertion: reading via the consumer returns the payload.
    assert mc.get("market:test") == payload

    # Plugin-hit assertion: the consumer's key (after its prefix logic)
    # must be present in CachePlugin's backing store.
    cache = world.get(CachePlugin)
    full_key = mc._build_key("market:test")
    assert full_key in cache._store
```

Copy this shape for new plugins: pick a real consumer, exercise it
through `world`, then assert both (a) the consumer's observable
outcome and (b) some observable state on the plugin that proves the
consumer actually routed through it. Note: contract suites use the
`world` fixture (re-exported via `tests/contracts/conftest.py`); async
tests need `@pytest.mark.asyncio` since pytest-asyncio runs in strict
mode.

## Out of scope (per spec)

- Service API Protocols under `agentclaw.api`.
- Prod impl execution in CI.
- Concurrency / performance / fuzz testing.

## See also

- `docs/arch/arch.rules.md` § Rule 25.
- `docs/arch/arch-diagnosis-backend.md` § 2.25 / § 4.5.
- `specs/2026-05-24-rule25-protocol-contract-tests/` — SDD artifacts.
