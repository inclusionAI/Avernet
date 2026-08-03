# Plan: Path-specific gateway domain routing, and the bot socket under `bots`

## Approach

A gateway domain stops *being* its leading path segment and instead **declares**
a path pattern plus the planes it answers. Resolution collects every domain whose
pattern matches the path **and** which answers the requesting plane, then picks
the most specific of those — match first, rank second, with the plane filtering
the candidate set rather than vetoing a winner.

The pattern grammar and its specificity ranking are lifted into one shared core
module so the routing plane and the auth plane rank identically by construction.
The socket domain then moves to `/openapi/v1/bots/messages/**` as configuration,
and the backend swaps one prefix constant.

Every domain that declares no pattern gets the implicit pattern
`{base_path}/{name}/**`, which is exactly the leading-segment match it has
today — so the four shipped HTTP domains are byte-identical after the change.

## Affected Components

- `src/gateway/src/gateway/community/core/paths/` — **new.** The shared path
  pattern: parse, match, rank, literal prefix.
- `src/gateway/src/gateway/community/core/forwarding/_domains.py` — pattern-based
  `Domain`; plane-filtered resolution; boot-time validation of over-broad
  patterns and colliding declarations.
- `src/gateway/src/gateway/community/core/authn/_route_security.py` — reuses the
  shared pattern instead of its private matcher/ranker. No behaviour change.
- `src/gateway/src/gateway/community/adapters/web/_forward.py` — asks the map for
  the *HTTP* domain rather than resolving then checking.
- `src/gateway/src/gateway/community/adapters/web/_relay_ws.py` — same on the
  socket plane; mount paths and the raw-prefix guard derive from the pattern.
- `src/gateway/src/gateway/community/adapters/web/app.py` — mounts socket routes
  from patterns.
- `src/gateway/configs/application.yaml` — the socket domain moves; the
  route-security exemption moves with it.
- `src/backend/src/agentclaw/community/core/engine_runtime/connection.py` — the
  published prefix constant.
- `src/backend/.../openapi_v1/engine_runtime/connection/schemas.py` — the two
  example URLs.
- `src/gateway/configs/schemas/bots.openapi.json` — regenerated build output.
- `src/backend/docs/openapi-v1/{README,engine-surface}.md` + `.zh-CN.md` — the
  reserved-name record and the quoted socket address.

## Data Model Changes

None.

## API / Interface Changes

### New: the shared path pattern (public module, so both planes may import it)

```python
# src/gateway/src/gateway/community/core/paths/_pattern.py  (new)
@dataclass(frozen=True)
class PathPattern:
    segments: tuple[str, ...]           # ("openapi", "v1", "bots", "**")

    @classmethod
    def parse(cls, pattern: str) -> PathPattern: ...
    def matches(self, path_segments: tuple[str, ...]) -> bool: ...
    @property
    def specificity(self) -> tuple[int, int, int]: ...   # (exact?, literals, params)
    @property
    def literal_prefix(self) -> str: ...                 # "/openapi/v1/bots/messages"

def split_segments(path: str) -> tuple[str, ...]: ...
```

`specificity` is today's `_route_security._specificity` minus its method
tie-break, which stays in route security — the domain map has no methods.

```python
# src/gateway/src/gateway/community/core/paths/__init__.py  (new)
from ._pattern import PathPattern, split_segments
__all__ = ["PathPattern", "split_segments"]
```

A public package because `tests/architecture/test_no_private_imports.py` forbids
`from gateway.community.core.authn._route_security import _specificity`.

### Changed: domain resolution takes a plane

```diff
# src/gateway/src/gateway/community/core/forwarding/_domains.py:289-303
-    def resolve(self, path: str) -> Server | None:
-    def domain_for(self, path: str) -> Domain | None:
-        ...
-        return self.domains.get(rest[0])
+    def domain_for(self, path: str, protocol: str) -> Domain | None:
+        """Every domain matching *path* on *protocol*, most specific one wins."""
+        segments = split_segments(path)
+        candidates = [
+            domain
+            for domain in self.domains.values()
+            if protocol in domain.protocols and domain.pattern.matches(segments)
+        ]
+        return max(candidates, key=_rank, default=None)
+
+    def http_domain_for(self, path: str) -> Domain | None: ...
+    def websocket_domain_for(self, path: str) -> Domain | None: ...
```

The two named wrappers exist because the delivery adapters are the callers and
may not import core (layer rule) — the same reason `serves_http` /
`serves_websocket` are predicates rather than a protocol argument today
(`_domains.py:245-259`). No protocol constant crosses that boundary.

`resolve(path)` is dropped rather than given a default plane: a default would
silently pick one, which is the failure mode this change exists to remove. Its
only callers are tests.

### Changed: config grammar — one new optional key

```yaml
# src/gateway/configs/application.yaml — user_config.upstreams.domains
bots:
  server: backend                       # implicit match: /openapi/v1/bots/**

bots-messages-ws:
  match: /openapi/v1/bots/messages/**   # explicit; the domain name is now just a name
  server: engine_proxy
  protocols: [websocket]
  rewrite:
    from: /openapi/v1/bots/messages
    to: /proxypass
```

```diff
# src/gateway/src/gateway/community/core/forwarding/_domains.py:329
- _DOMAIN_KEYS = frozenset({"server", "schema", "protocols", "rewrite"})
+ _DOMAIN_KEYS = frozenset({"match", "server", "schema", "protocols", "rewrite"})
```

### BREAKING (public surface): the socket address

```diff
- wss://<gw>/openapi/v1/engine/<target>/api/openclaw/ws?x-proxypass-token=…
+ wss://<gw>/openapi/v1/bots/messages/<target>/api/openclaw/ws?x-proxypass-token=…
```

No alias. `GET /openapi/v1/bots/connection/{bot_id}` — its path, name, and
response shape — is unchanged, so the endpoint coverage baseline needs no edit
(`coverage_baseline.txt:181` keys on that path).

## Key Files & Functions

### Pattern validation — the security-critical part

```python
# src/gateway/src/gateway/community/core/forwarding/_domains.py (new helper)
def _parse_pattern(name: str, raw: Any, base_path: str) -> PathPattern:
    """`match`, or the implicit `{base_path}/{name}/**`.

    Refused: a pattern that does not pin the version base plus at least one
    literal segment. Today an unknown leading segment resolves to None and the
    gateway denies — "never an open proxy" is one line and the mistake cannot be
    typed. With patterns, `/**` IS an open proxy.
    """
```

Accepted shape is `<literal segments>/**` only — no parameters, no interior
`**`, and the literal prefix must extend `base_path` by ≥1 segment:

| pattern | verdict |
|---|---|
| `/openapi/v1/bots/**` | ok |
| `/openapi/v1/bots/messages/**` | ok |
| `/**`, `/openapi/**`, `/openapi/v1/**` | refused — over-broad |
| `/openapi/v1/{x}/**` | refused — a parameter cannot pin a prefix |
| `/openapi/v1/bots` | refused — write the `/**` |

Note `**` matches **zero** segments (`_match_segments`, `_route_security.py:108`),
so `/openapi/v1/bots/**` still resolves the bare `/openapi/v1/bots` — preserving
`test_domain_at_root_resolves`.

Why domains accept a narrower grammar than route security, while sharing its
ranking: three separate mechanisms — the socket mount path, the raw-prefix guard,
and the rewrite anchor — each need a concrete literal prefix. Making that
derivable by construction beats partially supporting patterns that cannot
produce one.

### Boot-time collision check

```python
# _domains.py — after domains are parsed
def _reject_ambiguous(domains: dict[str, Domain]) -> None:
    """Two domains answering one path on one plane have no defined winner."""
```

Only identical patterns can collide: two *different* literal prefixes of equal
length differ at some segment, so no path matches both.

### Rewrite anchor re-derived from the pattern

```diff
# src/gateway/src/gateway/community/core/forwarding/_domains.py:385-391
-        domain_prefix = f"{base_path.rstrip('/')}/{name}"
+        pattern = _parse_pattern(name, spec.get("match"), base_path)
+        domain_prefix = pattern.literal_prefix
```

The `"can never match"` check at `:476-482` is otherwise unchanged — it catches
real config mistakes and the segment-boundary reasoning in its docstring still
holds, now against a longer prefix.

### Socket mount paths from the pattern

```diff
# src/gateway/src/gateway/community/adapters/web/_relay_ws.py:53-69
- def relay_routes(base_path: str, domain: str) -> tuple[str, ...]:
-     prefix = f"{base_path.rstrip('/')}/{domain}"
+ def relay_routes(prefix: str) -> tuple[str, ...]:
      return (prefix, f"{prefix}/{{full_path:path}}")
```

```diff
# src/gateway/src/gateway/community/adapters/web/app.py:139-141
- for name in domain_map.websocket_domains():
-     for route in relay_routes(domain_map.base_path, name):
+ for domain in domain_map.websocket_domains():
+     for route in relay_routes(domain.mount_prefix):
          app.add_api_websocket_route(route, forward_websocket)
```

`websocket_domains()` returns the domains themselves rather than a name→domain
mapping; `Domain.mount_prefix` is `self.pattern.literal_prefix`.

### The raw-path evasion guard — same guard, pattern-derived prefix

```diff
# src/gateway/src/gateway/community/adapters/web/_relay_ws.py:206-235
  def _required_raw_prefix(domain_map: Any, domain: Any) -> str:
      if domain.rewrite is not None:
          return str(domain.rewrite.from_prefix)
-     return f"{domain_map.base_path.rstrip('/')}/{domain.name}"
+     return str(domain.mount_prefix)
```

Unchanged in substance and **must not be weakened**: it is what defeats
`/openapi/v1/%62ots/messages/...`. Its docstring's worked example needs
re-writing against the new prefix. `_has_dot_segment` is untouched.

```diff
# src/gateway/src/gateway/community/adapters/web/_relay_ws.py:116
- domain = state.domain_map.domain_for(path)
- if domain is None or not domain.serves_websocket:
+ domain = state.domain_map.websocket_domain_for(path)
+ if domain is None:
```

```diff
# src/gateway/src/gateway/community/adapters/web/_forward.py:113-118
- domain = request.app.state.domain_map.domain_for(path)
- if domain is None or not domain.serves_http:
+ domain = request.app.state.domain_map.http_domain_for(path)
+ if domain is None:
      return _error(404, 1, "no route for path")
```

This is the whole trap in two lines: today `domain_for` picks `bots-messages-ws`
for an HTTP request and `serves_http` then 404s it. After the change that domain
is not a candidate, so `/openapi/v1/bots/**` wins and backend serves it.

### Route security

```diff
# src/gateway/configs/application.yaml:117
-    "/openapi/v1/engine/**": {}
+    "/openapi/v1/bots/messages/**": {}
```

The existing comment (credential is in the handshake query; a browser's
WebSocket API can attach no headers; stated explicitly because an omitted rule
falls through to `/**` and fails closed) carries over verbatim. Precedence needs
no change: `_specificity` already ranks 4 literals above 3.

### Backend

```diff
# src/backend/src/agentclaw/community/core/engine_runtime/connection.py:62
- _ENGINE_PREFIX = "/openapi/v1/engine"
+ _ENGINE_PREFIX = "/openapi/v1/bots/messages"
```

`_readdress_onto_gateway` (`:342-406`) already substitutes around this constant;
no logic changes. Module docstring (`:10`), the constant's own comment
(`:58-61`), and the docstrings at `:346` and `:437` name the old address and
need updating.

## Dependencies

None.

## Risks & Mitigations

- **Risk:** the generalisation makes an open proxy typable, where today it is
  structurally impossible.
  **Mitigation:** `_parse_pattern` refuses anything not pinning the version base
  plus one literal segment, at boot, naming the domain. Tested directly.

- **Risk:** the raw-prefix guard silently weakens at the longer prefix, letting
  `/openapi/v1/%62ots/messages/...` authenticate as one resource and dial as
  another.
  **Mitigation:** guard logic is untouched — only its source of truth moves from
  `base_path + name` to the pattern's literal prefix. The existing encoded-prefix
  test cases move to the new prefix rather than being dropped.

- **Risk:** someone later "simplifies" resolution back to rank-then-check-plane,
  quietly making the future HTTP `messages` endpoint unreachable.
  **Mitigation:** a test asserting an HTTP request to `/openapi/v1/bots/messages/x`
  reaches **backend** — the regression is then a red test, not a discovery at
  integration time.

- **Risk:** the docs' reserved-name list and the routes drift.
  **Mitigation:** see below — the existing equality interlock is preserved, not
  relaxed.

## Alternatives Considered

- **Rank first, then check the plane, with fallback on mismatch.** Rejected in
  the spec: a request refused by the winning domain getting a second attempt at
  another is a smuggling hole. Filtering the candidate set reaches the same
  outcome for today's traffic without the retry.
- **Full route-security pattern grammar for domains** (parameters, interior
  globs). Rejected: no requirement needs it, and three mechanisms need a concrete
  literal prefix that such patterns cannot yield.
- **Keying the plane on URL scheme** (`wss` vs `https`). Rejected in the spec —
  scheme encodes TLS, not routing; one upstream is already addressable on both
  planes via `Server.http_base_url` / `websocket_base_url`.
- **Duplicating the specificity ranking in `_domains.py`.** Rejected: two rankers
  that must agree will eventually not.
- **Keeping `domain_for(path)` with a default plane.** Rejected: a default
  silently picks a plane.

## Rollout

Single deploy, but **ordered** — the gateway must accept the new address before
the backend publishes it:

```bash
# 1. gateway (routing mechanism + config): serves BOTH old and new? No —
#    the config moves the domain, so old stops and new starts atomically.
# 2. backend: publishes the new address.
```

Between (1) and (2) the backend publishes an address the gateway no longer
serves, so **in-flight sockets survive** (already established) but new connection
requests fail until (2) lands. Acceptable for the same reason no alias is
provided: the surface has no reachable external caller — its route-security rule
requires a Google-resolved user identity a tenant with an access key cannot
satisfy. Single-box deploys both together.

Rollback: revert the config block and the backend constant. The routing mechanism
is inert without a domain that declares `match`.

## Test Strategy

### Reserved names — correcting the handoff's assumption

`test_the_docs_reserved_names_match_the_routes` asserts documented **==**
route-published components, so adding `messages` to that block *fails* the test.
Rather than weaken it to `>=`, add a second, explicitly-labelled record:

```diff
# src/backend/docs/openapi-v1/README.md:572
  <!-- reserved-component-names -->
  ```text
  approvals  ceiling  check-name  connection  engine  identity
  mcp  models  resources  routines  sessions  skills
  ```
+
+ <!-- reserved-component-names-unrouted -->
+ ```text
+ messages
+ ```
```

```python
# src/backend/tests/community/adapters/http/openapi_v1/test_path_convention.py
def test_the_docs_reserved_names_match_the_routes(): ...      # unchanged, still ==
def test_names_reserved_ahead_of_their_routes_are_not_routed():
    """A reserved name that gains a route must move to the routed block."""
    assert _documented_unrouted_names().isdisjoint(_components())
```

The routed block must stay *above* the new anchor: the existing parser takes the
first fenced block after its anchor.

Accuracy note for the prose: `messages` is **not** unreachable today the way the
other twelve are — an HTTP `GET /openapi/v1/bots/messages` still reaches backend
and resolves as `bot_id="messages"`. It is reserved because the gateway claims
the prefix on the socket plane and a `messages` component is planned there. The
README text will say that rather than being appended to a list whose stated
reason does not apply to it.

### Gateway

```python
# src/gateway/tests/test_domain_map.py
def test_a_domain_without_a_match_keeps_its_leading_segment_pattern(): ...
def test_the_most_specific_matching_pattern_wins(): ...
def test_a_pattern_is_only_a_candidate_on_the_plane_it_declares(): ...
def test_an_http_request_under_a_socket_prefix_falls_to_the_broader_domain(): ...
@pytest.mark.parametrize("pattern", ["/**", "/openapi/**", "/openapi/v1/**"])
def test_an_over_broad_pattern_is_refused_at_boot(pattern): ...
def test_two_domains_on_one_pattern_and_plane_are_refused_at_boot(): ...
def test_the_rewrite_anchor_follows_the_declared_pattern(): ...
```

```python
# src/gateway/tests/integration/test_relay_ws_route.py
#   existing cases re-pointed at /openapi/v1/bots/messages/…, plus:
def test_the_old_engine_prefix_no_longer_relays(): ...
def test_an_http_request_to_the_socket_prefix_reaches_the_backend(): ...
#   the encoded-prefix matrix (:522-524, :441) moves to the new prefix — the
#   %62ots / %6dessages spellings, not dropped.
```

```python
# src/gateway/tests/test_route_security.py:23
def test_shipped_config_exempts_the_bot_socket_prefix(): ...   # renamed + re-pointed
def test_the_socket_exemption_beats_the_bots_user_requirement(): ...  # new, pins precedence
```

Also touched: `tests/test_log_redaction.py` (sample paths, cosmetic) and
`tests/architecture/*` (must stay green — the new `core/paths` package needs
`__all__`, per `test_all_exports_valid.py`).

### Backend

```python
# src/backend/tests/community/core/engine_runtime/test_connection.py
#   the published-URL assertions at :273-439 move to the new prefix.
# src/backend/tests/community/adapters/http/openapi_v1/engine_runtime/test_connection.py:50
```

### Build artifact

```bash
# published paths do not change, but the socket URL appears as an *example*
# at bots.openapi.json:351 and :3519, sourced from connection/schemas.py
python src/backend/scripts/dump_openapi.py
python src/gateway/scripts/gate_and_publish_openapi.py
```

Do **not** regenerate `src/gateway/tests/fixtures/bots.openapi.json` — that is a
hand-written fixture, not a copy.

### Gates that mislead if not watched

- `tests/community/framework/test_coverage_gate.py` ERRORs on a missing fixture
  in isolation and only does real work in a full-suite run — grep for `ERROR` as
  well as `FAILED` when diffing before/after, or it drops out of both sides.
- Pre-existing sandbox failures, **not** caused by this change: legacy
  `/api/bots` admin routes 403 (empty community allow-list);
  `tests/community/endpoints/*` and `tests/community/e2e/*` missing a SQLite
  fixture; gateway `tests/e2e/*` need a live server; `ruff format --check` flags
  code blocks in gateway `docs/*.md` under a newer ruff than the repo pins.
- Dependency install: the pinned index is unreachable here. `uv sync --frozen`
  fails; `uv pip install --index-url https://pypi.org/simple -e .` works, plus
  the `dev` group and `pytestarch` for the gateway.
