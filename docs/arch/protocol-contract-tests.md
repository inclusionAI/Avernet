# Protocol Contract Tests (Rule 25)

Plugin capability contracts live under `agentclaw.community.plugin_api`.
Their consumer-level suites live under `apps/backend/tests/community/contracts/`;
enterprise-specific suites live under the host's `apps/backend/tests/corp/contracts/`.
The current mapping, named exemptions and corp-resident handling are enforced by
`apps/backend/tests/community/architecture/test_protocol_contracts.py`.
In an enterprise checkout, that public gate is in `submodules/avernet/`.

**Quality gate:** the mapping check verifies suite presence and test functions;
it cannot establish meaningful consumer coverage. Follow the relevant suite's
`OWNERS` and review expectations. The runtime/behavioral assertions below remain
necessary even if discovery passes.

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

1. Use the `world` fixture (from `tests/community/framework/fixtures.py`).
2. `world.get(SomeConsumer)` returns the upper-layer service / domain
   function under test, wired against a per-test injector with local
   plugin impls.
3. Exercise the consumer through its public API — at least one happy
   path and one failure path.
4. Assert observable outcomes from the consumer.
5. **Assert the plugin was actually invoked** — inspect the mock's
   recorded calls. Without this, a consumer could silently bypass the
   plugin and the test would still pass.

If the selected implementation cannot expose calls, bind a recording test
double through the test injector. Keep profile selection at the composition
root; a test must not add environment branches to the consumer.

## When to write one

- Adding a new Plugin Protocol: add the suite in the same PR. The
  arch test rejects merges that introduce a Protocol without one.
- Modifying an existing Protocol's behaviour: update the suite to
  reflect the new contract. The consumer test is your spec.

## Worked example

`tests/community/contracts/test_cache.py` is the canonical reference:

```python
from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
from agentclaw.community.plugin_api.cache import CachePlugin


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
`world` fixture (re-exported via `tests/community/contracts/conftest.py`); async
tests need `@pytest.mark.asyncio` since pytest-asyncio runs in strict
mode.

## Out of scope (per spec)

- Service API Protocols under `agentclaw.community.api`.
- Prod impl execution in CI.
- Concurrency / performance / fuzz testing.

## See also

- `docs/arch/arch.rules.md` § Rule 25.
- `docs/arch/arch-diagnosis-backend.md` — historical diagnosis, not current path authority.
- Historical Rule 25 migration specs explain the original rollout; use the
  current gate and suites above for paths and exceptions.
