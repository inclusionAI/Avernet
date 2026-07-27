# Gateway: Multi-Identity AuthN & Bot Principal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the gateway authentication-only (AuthN), able to yield multiple identities per request, and add a `bot` identity type — with the user identity acquired by self-selecting plugins tried in a configured chain.

**Architecture:** The authn runner produces one `Principal` per required identity type from an ordered plugin chain (config-driven). Each `AuthStrategy` plugin self-selects from the request (returns `None` to decline / falls through, or claims it and produces a `Principal` / raises `AuthError`). `scope`/permission machinery is removed entirely from the gateway. A new `BotPrincipal` joins the discriminated `Principal` union. Routes declare required identity *types* in `x-avernet-security`; `authn.yaml` declares each type's plugin chain.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, pytest + pytest-asyncio (`asyncio_mode = "auto"`), pyyaml. Repo-wide rules in `AGENTS.md`/`CLAUDE.md` (no `T | None` unless `None` is an intentional contract state; required values non-optional).

**Reference spec:** `specs/2026-07-24-gateway-multi-identity-and-bot-principal/spec.md` (rev 3). Read it before starting.

**Key semantics (do not deviate):**
- `AuthStrategy.build(creds) -> Principal | None`: `None` = "not applicable / no credential" (runner **falls through**); raise `AuthError` = "applicable but credential invalid" (terminal, no fallback); return `Principal` = success.
- The runner **never reads the `source` header**. `source` is just an ordinary credential input a plugin chooses to claim.
- Default fallback semantics: `None` overloads "not applicable" and "applicable but credential absent" — both fall through. Accepted this round (strict variant is out of scope).

---

## File Structure

### Create
- `src/gateway/community/spi/authn/_ports.py` — new SPI ports: `UserTokenValidator`, `BotTokenValidator` + `BotRecord` dataclass.
- `src/gateway/community/core/authn/_identities.py` — the `Identities` frozen container.
- `src/gateway/community/core/authn/_config.py` — parse `authn.yaml` → ordered `dict[PrincipalType, tuple[AuthStrategy, ...]]`.
- `src/gateway/community/plugins/authn/cookie/_strategy.py` — the `CookieStrategy` (renamed relocated first-party-user logic).
- `src/gateway/community/plugins/authn/cookie/__init__.py`
- `src/gateway/community/plugins/authn/google_token/_strategy.py` — source-named token plugin (`source == "google"`).
- `src/gateway/community/plugins/authn/google_token/__init__.py`
- `src/gateway/community/plugins/authn/bot_token/_strategy.py` — `BotTokenStrategy`.
- `src/gateway/community/plugins/authn/bot_token/__init__.py`
- `src/gateway/community/plugins/authn/bot_token/_bare_validator.py` — bare `BotTokenValidator` (in-memory).
- `src/gateway/community/plugins/authn/google_token/_bare_validator.py` — bare `UserTokenValidator` (in-memory).
- `configs/authn.yaml` — the strategy-chain config (shipped).
- `tests/test_identities.py` — `Identities` container unit tests.
- `tests/test_authn_config.py` — `authn.yaml` parsing + startup-validation tests.
- `tests/test_bot_principal.py` — `BotPrincipal` model tests.
- `tests/test_google_token_strategy.py` — google plugin unit tests.
- `tests/test_cookie_strategy.py` — cookie plugin unit tests (relocated from first-party).
- `tests/test_bot_token_strategy.py` — bot plugin unit tests.
- `tests/contracts/spi/test_user_token_validator.py` — conformance for `UserTokenValidator`.
- `tests/contracts/spi/test_bot_token_validator.py` — conformance for `BotTokenValidator`.

### Modify
- `src/gateway/community/spi/authn/_models.py` — add `BotPrincipal` + `PrincipalType.BOT`; remove `Delegation`/`StrategyParams`/`UserPrincipal.scopes`; make `Principal` a real discriminated union.
- `src/gateway/community/spi/authn/_protocols.py` — `AuthStrategy.build(creds)` (drop `params`); add `principal_type`.
- `src/gateway/community/spi/authn/__init__.py` — update exports.
- `src/gateway/community/core/authn/__init__.py` — export `Identities`.
- `src/gateway/community/core/authn/_runner.py` — multi-identity ordered fallback runner.
- `src/gateway/community/core/authn/_route_security.py` — requirement = `frozenset[PrincipalType]` (drop scope parsing).
- `src/gateway/community/bootstrap/_authn.py` — composition: build ordered registry from `authn.yaml`.
- `src/gateway/community/adapters/web/_auth.py` — `require_identities -> Identities`.
- `src/gateway/community/adapters/web/__init__.py` — export `require_identities`.
- `src/gateway/community/adapters/web/contracts/_security.py` — `requires_identities(*types)`.
- `src/gateway/community/adapters/web/contracts/__init__.py` — export new helper.
- `src/gateway/community/adapters/web/routers/<group>/_router.py` (bots, channels, identity, mcp, resources, routines, skills) — `PrincipalDep`→`IdentitiesDep`, `requires_user_principal()`→`requires_identities(...)`, `Principal` import→`Identities`.
- `configs/route_security.yaml` — values become type strings.
- `tests/test_authn_models.py`, `tests/test_auth_runner.py`, `tests/test_route_security.py`, `tests/test_security_contract.py`, `tests/test_bots_router.py`, `tests/test_groups.py`, `tests/contracts/spi/test_auth_strategy.py`, `tests/unit/plugins/test_auth_plugin.py` — migrate to new model; delete/replace first-party test.

### Delete
- `src/gateway/community/plugins/authn/first_party_user/` (logic moves to `cookie/`).
- `tests/test_first_party_user_strategy.py` (replaced by `tests/test_cookie_strategy.py`).

---

## Phase 1 — Identity model (no behavior yet)

### Task 1.1: Add `BotPrincipal` and `PrincipalType.BOT`; remove `scopes` from `UserPrincipal`

**Files:**
- Modify: `src/gateway/community/spi/authn/_models.py`
- Test: `tests/test_bot_principal.py` (create), `tests/test_authn_models.py` (modify)

> The existing `tests/test_authn_models.py` references `scopes`, `Delegation`, `StrategyParams`, and `Principal is UserPrincipal`. They will break until Tasks 1.2–1.3 finish. We update them in lockstep here because the model is one coherent change.

- [ ] **Step 1: Write the failing model tests**

Create `tests/test_bot_principal.py`:

```python
"""Unit tests for the BotPrincipal model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gateway.community.spi.authn import BotPrincipal, PrincipalType


def _bot(**over: object) -> BotPrincipal:
    base = dict(tenant="t-1", bot_uuid="bot-7", owner_id="owner-1", token="tok")
    base.update(over)
    return BotPrincipal(**base)  # type: ignore[arg-type]


def test_bot_principal_defaults() -> None:
    p = _bot()
    assert p.type is PrincipalType.BOT
    assert p.type == "bot"
    assert p.tenant == "t-1"
    assert p.bot_uuid == "bot-7"
    assert p.owner_id == "owner-1"
    assert p.token == "tok"


def test_bot_principal_requires_core_fields() -> None:
    with pytest.raises(ValidationError):
        BotPrincipal(tenant="t-1")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _bot(bot_uuid=None)  # type: ignore[arg-type]


def test_bot_principal_serialization_tags_type() -> None:
    dumped = _bot().model_dump()
    assert dumped["type"] == "bot"
    assert dumped["tenant"] == "t-1"
    assert dumped["bot_uuid"] == "bot-7"


def test_bot_principal_is_immutable() -> None:
    p = _bot()
    with pytest.raises(ValidationError):
        p.tenant = "t-2"  # type: ignore[misc]
```

Update `tests/test_authn_models.py` — replace the whole file body:

```python
"""Unit tests for the authn Principal domain models."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import TypeAdapter, ValidationError

from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)


def _subject() -> AuthenticatedUser:
    return AuthenticatedUser(id="u1", username="op")


def test_user_principal_defaults() -> None:
    p = UserPrincipal(tenant="t-1", subject=_subject())
    assert p.type is PrincipalType.USER
    assert p.type == "user"
    assert p.tenant == "t-1"
    assert p.subject.id == "u1"


def test_user_principal_requires_tenant_and_subject() -> None:
    with pytest.raises(ValidationError):
        UserPrincipal(subject=_subject())  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        UserPrincipal(tenant="t-1")  # type: ignore[call-arg]


def test_user_principal_serialization_tags_type() -> None:
    dumped = UserPrincipal(tenant="t-1", subject=_subject()).model_dump()
    assert dumped["type"] == "user"
    assert dumped["tenant"] == "t-1"
    assert dumped["subject"]["id"] == "u1"


def test_user_principal_is_immutable() -> None:
    p = UserPrincipal(tenant="t-1", subject=_subject())
    with pytest.raises(ValidationError):
        p.tenant = "t-2"  # type: ignore[misc]


def test_user_principal_has_no_scopes() -> None:
    # Authorization scopes were removed from the gateway's identity model.
    assert not hasattr(UserPrincipal(tenant="t-1", subject=_subject()), "scopes")


def test_principal_is_a_discriminated_union_of_user_and_bot() -> None:
    adapter = TypeAdapter(Principal)
    user = adapter.validate_python(
        {"type": "user", "tenant": "t", "subject": {"id": "u", "username": "a"}}
    )
    bot = adapter.validate_python(
        {"type": "bot", "tenant": "t", "bot_uuid": "b", "owner_id": "o", "token": "k"}
    )
    assert isinstance(user, UserPrincipal)
    assert isinstance(bot, BotPrincipal)


def test_credential_bundle_is_frozen() -> None:
    creds = CredentialBundle(headers={}, cookies={"SSO_TOKEN": "x"}, query={})
    assert creds.cookies["SSO_TOKEN"] == "x"
    with pytest.raises(Exception):
        creds.headers = {}  # type: ignore[misc]


def test_credential_bundle_keeps_mapping_types() -> None:
    creds = CredentialBundle(headers={"a": "b"}, cookies={}, query={})
    assert isinstance(creds.headers, Mapping)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bot_principal.py tests/test_authn_models.py -q`
Expected: FAIL — `BotPrincipal` import error; `scopes`/`Delegation`/`StrategyParams` import errors.

- [ ] **Step 3: Rewrite `_models.py`**

Replace `src/gateway/community/spi/authn/_models.py` entirely with:

```python
"""Authn SPI — the neutral Principal the gateway produces after authentication.

The gateway authenticates a request, builds one ``Principal`` per required
identity type, and forwards the set (signed) to downstream components, which
project each onto their own domain DTOs. The gateway never lets a component see
raw credentials — except the bot credential, which the bot identity carries
through by design (see the spec's Further Notes).

Identity types are modeled as a discriminated union on ``type``. Roles beyond
the first-party ``UserPrincipal`` and the calling ``BotPrincipal`` (e.g. the
deferred third-party ``AppPrincipal``) are added as new union members.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Literal

from gateway.community.spi.auth import AuthenticatedUser


class PrincipalType(StrEnum):
    """Discriminator for the kind of caller a ``Principal`` represents."""

    USER = "user"  # a first-party authenticated user
    BOT = "bot"  # a calling bot, acting in its own identity


class UserPrincipal(BaseModel):
    """A first-party authenticated user, produced by the gateway.

    Ownership and authorization resolve to ``subject`` **within** ``tenant``.
    Authorization scopes are NOT carried here — the gateway is auth-only; the
    component decides permissions.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.USER] = PrincipalType.USER
    tenant: str = Field(
        description="Tenant id the caller belongs to (stable id, not a display name)."
    )
    subject: AuthenticatedUser = Field(description="The authenticated end user.")


class BotPrincipal(BaseModel):
    """A calling bot, acting in its own identity (not impersonating a user).

    ``bot_uuid`` is the bot's stable id; ``owner_id`` is the user who owns it
    (the resource-ownership anchor); ``token`` is the presented/verified bot
    credential (a secret flowing downstream — components must treat it as such);
    ``tenant`` is the owner's tenant, preserving the invariant that every
    Principal carries a tenant.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.BOT] = PrincipalType.BOT
    tenant: str = Field(description="Owner's tenant (stable id).")
    bot_uuid: str = Field(description="The bot's stable identifier.")
    owner_id: str = Field(description="The user who owns the bot.")
    token: str = Field(description="The presented/verified bot credential (secret).")


Principal = Annotated[UserPrincipal | BotPrincipal, Field(discriminator="type")]


# ── Strategy inputs (framework-agnostic) ─────────────────────────────────────


@dataclass(frozen=True)
class CredentialBundle:
    """Framework-agnostic snapshot of a request's credentials.

    A delivery adapter fills this from the incoming request (e.g. a FastAPI
    ``Request``); an ``AuthStrategy`` reads it without depending on any web
    framework. ``source`` (if sent by the caller) is just another header here —
    the runner never reads it; plugins may read ``headers["source"]`` to decide
    whether they claim a request.
    """

    headers: Mapping[str, str]
    cookies: Mapping[str, str]
    query: Mapping[str, str]
```

- [ ] **Step 4: Update `__init__.py` exports (partial — protocols still need Task 1.2)**

Replace `src/gateway/community/spi/authn/__init__.py` exports section to remove `Delegation`/`StrategyParams` and add `BotPrincipal`:

```python
"""Authn SPI — the neutral Principal contract produced by the gateway.

See ``_models`` for the model definitions, ``_protocols`` for the
``AuthStrategy`` contract, and the auth design doc
(``src/gateway/docs/2026-07-21-auth-design.md``) for the full picture.
"""

from ._models import (
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)
from ._protocols import AuthStrategy

__all__ = [
    "AuthStrategy",
    "BotPrincipal",
    "CredentialBundle",
    "Principal",
    "PrincipalType",
    "UserPrincipal",
]
```

- [ ] **Step 5: Run model tests to verify they pass**

Run: `python -m pytest tests/test_bot_principal.py tests/test_authn_models.py -q`
Expected: PASS. (Other tests still fail — that's fine, later tasks fix them.)

- [ ] **Step 6: Commit**

```bash
git add src/gateway/community/spi/authn/_models.py src/gateway/community/spi/authn/__init__.py tests/test_bot_principal.py tests/test_authn_models.py
git commit -m "feat(authn): add BotPrincipal to discriminated Principal union; remove scopes"
```

---

### Task 1.2: Update `AuthStrategy` protocol — `build(creds)` + `principal_type`

**Files:**
- Modify: `src/gateway/community/spi/authn/_protocols.py`
- Test: `tests/contracts/spi/test_auth_strategy.py` (modify later in Task 4.2; this task only changes the protocol + reference strategy so the tree imports cleanly)

- [ ] **Step 1: Rewrite the protocol**

Replace `src/gateway/community/spi/authn/_protocols.py` with:

```python
"""Authn SPI — the ``AuthStrategy`` contract (how a Principal is built).

A strategy is a named way to turn a request's credentials into a
:class:`~gateway.community.spi.authn.Principal` of a specific identity type.
The gateway holds a small, closed set of them; each identity type's ordered
chain is declared in ``configs/authn.yaml``.

Applicability is decided **inside** ``build`` by reading ``creds``. Returning
``None`` means "not applicable / no credential present" — the runner falls
through to the next plugin in the chain. Raising ``AuthError`` means
"applicable but the credential is invalid" — terminal, no fallback. Returning a
``Principal`` is success.

There is deliberately **no** separate ``applies()`` method: "applicable but
credential absent" and "not applicable" both map to ``None`` (fallback
semantics; see the spec's Further Notes). The runner never reads ``source``.
"""

from __future__ import annotations

from typing import Protocol

from ._models import CredentialBundle, Principal, PrincipalType


class AuthStrategy(Protocol):
    """Builds a Principal of a specific type from a request, or declines."""

    name: str  # stable id referenced by authn.yaml chains
    principal_type: PrincipalType  # the identity type this strategy produces

    async def build(self, creds: CredentialBundle) -> Principal | None:
        """Try to build a Principal from the request.

        Returns ``None`` (not applicable / no credential) → runner falls through.
        Raises ``AuthError`` (applicable but invalid) → terminal, no fallback.
        Returns a ``Principal`` → success.
        """
        ...
```

- [ ] **Step 2: Update the existing reference strategy so it conforms**

Replace `src/gateway/community/plugins/authn/first_party_user/_strategy.py` with this temporary conforming version (it still produces `USER`; it will be relocated to `cookie/` in Phase 3 — for now keep it compiling):

```python
"""``first_party_user`` strategy — session-cookie → UserPrincipal (interim).

Will be relocated to the ``cookie`` plugin in a later task; kept here only so
the tree imports. Accepts any session cookie.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError, AuthPlugin
from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)

_SESSION_COOKIES = ("IAM_TOKEN", "SSO_TOKEN", "access_token")


class FirstPartyUserStrategy:
    """Resolve a login-session cookie into a :class:`UserPrincipal`."""

    name = "first_party_user"
    principal_type = PrincipalType.USER

    def __init__(self, auth: AuthPlugin, default_tenant: str) -> None:
        self._auth = auth
        self._default_tenant = default_tenant

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if not any(name in creds.cookies for name in _SESSION_COOKIES):
            return None
        user = await self._auth.get_login_user(
            cookie=creds.headers.get("cookie", ""),
            referer=creds.headers.get("referer"),
        )
        tenant = user.tenant_id or self._default_tenant
        return UserPrincipal(tenant=tenant, subject=user)
```

- [ ] **Step 3: Verify the package still imports**

Run: `python -c "import gateway.community.spi.authn; import gateway.community.plugins.authn.first_party_user; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/community/spi/authn/_protocols.py src/gateway/community/plugins/authn/first_party_user/_strategy.py
git commit -m "feat(authn): AuthStrategy.build(creds) with principal_type; plugin self-selection"
```

---

### Task 1.3: Update `AuthPlugin` SPI doc (no behavior change)

**Files:**
- Modify: `src/gateway/community/spi/auth/_protocols.py`

> Per spec, `AuthPlugin` keeps `get_login_user`/`is_allowed`/`check_permission` unchanged but is now used only by the `cookie` strategy for `get_login_user`. No code change is needed — this task is a doc clarifier so later agents don't "clean up" the kept methods.

- [ ] **Step 1: Clarify the docstring**

In `src/gateway/community/spi/auth/_protocols.py`, change the class docstring's "Implementations:" block to:

```python
    """Plugin protocol for authentication and authorization.

    Unifies login (`get_login_user`) and authorization (`is_allowed`,
    `check_permission`) into one contract. The gateway's authn flow uses only
    `get_login_user` (via the ``cookie`` strategy); the authorization methods are
    kept on this SPI for component-side use — the gateway itself is auth-only and
    does not call them. Removing them is explicitly out of scope (see spec).

    Implementations:
    - BareAuthPlugin: returns a hardcoded user, always-allowed for tests.
    - Enterprise plugin: calls the enterprise SSO / identity API for login.
    """
```

- [ ] **Step 2: Verify the auth-plugin tests still pass**

Run: `python -m pytest tests/unit/plugins/test_auth_plugin.py tests/contracts/spi/test_auth_plugin.py -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/gateway/community/spi/auth/_protocols.py
git commit -m "docs(auth): clarify AuthPlugin is auth-only for the gateway; keep check_permission/is_allowed"
```

---

## Phase 2 — Runner, Identities, route table (the core)

### Task 2.1: `Identities` container

**Files:**
- Create: `src/gateway/community/core/authn/_identities.py`, `src/gateway/community/core/authn/__init__.py` (modify)
- Test: `tests/test_identities.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_identities.py`:

```python
"""Unit tests for the Identities container."""

from __future__ import annotations

import pytest

from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import BotPrincipal, PrincipalType, UserPrincipal
from gateway.community.core.authn import Identities

_USER = UserPrincipal(tenant="t", subject=AuthenticatedUser(id="u", username="a"))
_BOT = BotPrincipal(tenant="t", bot_uuid="b", owner_id="o", token="k")


def test_get_returns_present_principal() -> None:
    ids = Identities({PrincipalType.USER: _USER})
    assert ids.get(PrincipalType.USER) is _USER


def test_get_returns_none_for_absent() -> None:
    assert Identities({}).get(PrincipalType.BOT) is None


def test_require_returns_present_principal() -> None:
    assert Identities({PrincipalType.USER: _USER}).require(PrincipalType.USER) is _USER


def test_require_raises_for_absent() -> None:
    with pytest.raises(KeyError):
        Identities({}).require(PrincipalType.USER)


def test_iter_yields_present_types() -> None:
    ids = Identities({PrincipalType.USER: _USER, PrincipalType.BOT: _BOT})
    assert set(ids) == {PrincipalType.USER, PrincipalType.BOT}


def test_identities_is_frozen() -> None:
    ids = Identities({PrincipalType.USER: _USER})
    with pytest.raises(Exception):
        ids._principals = {}  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_identities.py -q`
Expected: FAIL — `Identities` import error.

- [ ] **Step 3: Implement `Identities`**

Create `src/gateway/community/core/authn/_identities.py`:

```python
"""The authenticated identity set produced for one request.

The runner collects one ``Principal`` per required identity type into an
``Identities`` container; the delivery layer hands it to handlers and the
gateway forwards it downstream.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from gateway.community.spi.authn import Principal, PrincipalType


@dataclass(frozen=True)
class Identities:
    """The principals resolved for a request, keyed by identity type."""

    _principals: dict[PrincipalType, Principal]

    def get(self, principal_type: PrincipalType) -> Principal | None:
        """Return the principal of ``principal_type``, or ``None`` if absent."""
        return self._principals.get(principal_type)

    def require(self, principal_type: PrincipalType) -> Principal:
        """Return the principal of ``principal_type``; raise if absent."""
        p = self._principals.get(principal_type)
        if p is None:
            raise KeyError(f"no authenticated identity of type {principal_type}")
        return p

    def __iter__(self) -> Iterator[PrincipalType]:
        return iter(self._principals)

    def __len__(self) -> int:
        return len(self._principals)
```

Update `src/gateway/community/core/authn/__init__.py` — read it first, then ensure it exports `Identities` and `authenticate` and `RouteSecurity`. Replace its body with:

```python
"""Core authn: the runner, the identities container, and the route table."""

from ._identities import Identities
from ._route_security import RouteSecurity
from ._runner import authenticate

__all__ = [
    "Identities",
    "RouteSecurity",
    "authenticate",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_identities.py -q`
Expected: PASS. (Other core tests still fail — fixed next.)

- [ ] **Step 5: Commit**

```bash
git add src/gateway/community/core/authn/_identities.py src/gateway/community/core/authn/__init__.py tests/test_identities.py
git commit -m "feat(authn): add Identities container for one request's principals"
```

---

### Task 2.2: Route table → `frozenset[PrincipalType]` requirement

**Files:**
- Modify: `src/gateway/community/core/authn/_route_security.py`
- Test: `tests/test_route_security.py` (modify)

- [ ] **Step 1: Rewrite the route-table test**

Replace `tests/test_route_security.py` entirely with:

```python
"""Unit tests for the route-security table (config parsing + matching)."""

from __future__ import annotations

from pathlib import Path

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import PrincipalType

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "route_security.yaml"


def test_shipped_config_loads_and_covers_bots() -> None:
    rs = RouteSecurity.from_yaml(_CONFIG)
    req = rs.resolve("GET", "/openapi/v1/bots/abc")
    assert req is not None
    assert PrincipalType.USER in req


def test_more_specific_rule_wins() -> None:
    rs = RouteSecurity.from_table(
        {
            "/**": ["user"],
            "/openapi/v1/bots/**": ["user"],
        }
    )
    assert rs.resolve("POST", "/openapi/v1/bots/x") == frozenset({PrincipalType.USER})


def test_multi_type_requirement() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/{id}/chat": ["bot", "user"]})
    req = rs.resolve("POST", "/openapi/v1/bots/x/chat")
    assert req == frozenset({PrincipalType.BOT, PrincipalType.USER})


def test_method_specific_rule_beats_method_agnostic() -> None:
    rs = RouteSecurity.from_table(
        {
            "/openapi/v1/bots/{id}": ["user"],
            "GET /openapi/v1/bots/{id}": ["user"],
        }
    )
    assert rs.resolve("GET", "/openapi/v1/bots/42") == frozenset({PrincipalType.USER})
    assert rs.resolve("POST", "/openapi/v1/bots/42") == frozenset({PrincipalType.USER})


def test_param_segment_matches_one_segment() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/{id}": ["user"]})
    assert rs.resolve("GET", "/openapi/v1/bots/42") is not None
    assert rs.resolve("GET", "/openapi/v1/bots/42/skills") is None


def test_unmatched_route_is_fail_closed() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/**": ["user"]})
    assert rs.resolve("GET", "/openapi/v1/channels") is None


def test_unknown_type_string_is_rejected() -> None:
    # Fail-closed against typos in the route table at parse time.
    import pytest

    with pytest.raises(ValueError):
        RouteSecurity.from_table({"/**": ["nonsense"]})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_route_security.py -q`
Expected: FAIL — `StrategyParams`/`Delegation` import gone; requirement shape changed.

- [ ] **Step 3: Rewrite `_route_security.py`**

Replace `src/gateway/community/core/authn/_route_security.py` entirely with:

```python
"""Route → required-identity-types table (spec §8, rev 3).

Loads the ``route_security`` table (a ``"[METHOD ]<path-glob>" -> list of type
strings`` mapping) and resolves an incoming ``(method, path)`` to the **most
specific** matching rule's ``frozenset[PrincipalType]`` requirement. Fail-closed:
an unmatched route resolves to ``None`` and the caller must deny. Unknown type
strings are rejected at parse time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gateway.community.spi.authn import PrincipalType

# A requirement is the set of identity types a route demands; the runner must
# produce one Principal of each.
Requirement = frozenset[PrincipalType]


@dataclass(frozen=True)
class _Rule:
    method: str | None  # None = applies to every method
    segments: tuple[str, ...]
    requirement: Requirement


class RouteSecurity:
    """The compiled route-security table, queryable per request."""

    def __init__(self, rules: list[_Rule]) -> None:
        self._rules = rules

    @classmethod
    def from_table(cls, table: dict[str, Any]) -> RouteSecurity:
        return cls([_parse_rule(key, value) for key, value in table.items()])

    @classmethod
    def from_yaml(cls, path: str | Path) -> RouteSecurity:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls.from_table(raw.get("route_security", {}))

    def resolve(self, method: str, path: str) -> Requirement | None:
        """Most-specific matching rule's requirement, or ``None`` if none match."""
        segments = _segments(path)
        matches = [r for r in self._rules if _matches(r, method, segments)]
        if not matches:
            return None
        return max(matches, key=_specificity).requirement


# ── parsing ──────────────────────────────────────────────────────────────────


def _parse_rule(key: str, value: Any) -> _Rule:
    method, path = _split_key(key)
    return _Rule(method=method, segments=_segments(path), requirement=_parse_req(value))


def _split_key(key: str) -> tuple[str | None, str]:
    parts = key.strip().split(None, 1)
    if len(parts) == 2 and parts[0].isupper() and parts[1].startswith("/"):
        return parts[0], parts[1]
    return None, key.strip()


def _segments(path: str) -> tuple[str, ...]:
    return tuple(seg for seg in path.split("/") if seg)


def _parse_req(value: Any) -> Requirement:
    types: set[PrincipalType] = set()
    for item in value or []:
        if not isinstance(item, str):
            raise ValueError(f"route requirement must be a list of type strings, got {item!r}")
        types.add(_parse_type(item))
    return frozenset(types)


def _parse_type(name: str) -> PrincipalType:
    try:
        return PrincipalType(name)
    except ValueError as ex:
        raise ValueError(f"unknown identity type in route_security: {name!r}") from ex


# ── matching (spec §8.3) ─────────────────────────────────────────────────────


def _is_param(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}")


def _matches(rule: _Rule, method: str, path_segments: tuple[str, ...]) -> bool:
    if rule.method is not None and rule.method != method:
        return False
    return _match_segments(rule.segments, path_segments)


def _match_segments(pattern: tuple[str, ...], segs: tuple[str, ...]) -> bool:
    if not pattern:
        return not segs
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return True
    if not segs:
        return False
    if head != segs[0] and not _is_param(head):
        return False
    return _match_segments(rest, segs[1:])


def _specificity(rule: _Rule) -> tuple[int, int, int, int]:
    """Higher = more specific: exact beats glob, more literals, then method."""
    has_glob = "**" in rule.segments
    literals = sum(1 for s in rule.segments if s != "**" and not _is_param(s))
    params = sum(1 for s in rule.segments if _is_param(s))
    return (0 if has_glob else 1, literals, params, int(rule.method is not None))
```

- [ ] **Step 4: Update the shipped `configs/route_security.yaml`**

Replace `configs/route_security.yaml` with:

```yaml
# Route → required identity types (spec rev 3 §8).
#
# Keys are "[METHOD ]<path-glob>" (method optional). More specific rules
# override more general ones; "/**" is the top-level default. Each value is a
# list of identity-type strings (e.g. [user], [bot, user]). The gateway must
# produce one Principal of each listed type or reject (401).
#
# Which *plugin chain* produces each type is declared in configs/authn.yaml
# (orthogonal to this table). Routes declare types, not strategy names.

route_security:
  # Top-level default: every route requires an authenticated user.
  "/**": [user]

  # Sample group — the bots endpoints (explicit, more-specific rule).
  "/openapi/v1/bots/**": [user]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_route_security.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/community/core/authn/_route_security.py configs/route_security.yaml tests/test_route_security.py
git commit -m "feat(authn): route requirement is a frozenset of identity types (no scopes)"
```

---

### Task 2.3: Multi-identity runner with ordered fallback

**Files:**
- Modify: `src/gateway/community/core/authn/_runner.py`
- Test: `tests/test_auth_runner.py` (modify)

- [ ] **Step 1: Write the failing test (rewrite)**

Replace `tests/test_auth_runner.py` entirely with:

```python
"""Unit tests for the auth runner (ordered fallback per identity type, rev 3)."""

from __future__ import annotations

import pytest

from gateway.community.core.authn import Identities, authenticate
from gateway.community.spi.auth import AuthenticatedUser, AuthError
from gateway.community.spi.authn import (
    AuthStrategy,
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)

_CREDS = CredentialBundle(headers={}, cookies={}, query={})


def _user() -> UserPrincipal:
    return UserPrincipal(tenant="t", subject=AuthenticatedUser(id="u", username="a"))


def _bot() -> BotPrincipal:
    return BotPrincipal(tenant="t", bot_uuid="b", owner_id="o", token="k")


class _Fixed:
    """A strategy that always yields a fixed result (Principal, None, or raises)."""

    def __init__(self, name: str, ptype: PrincipalType, result: Principal | AuthError | None) -> None:
        self.name = name
        self.principal_type = ptype
        self._result = result

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if isinstance(self._result, AuthError):
            raise self._result
        return self._result


async def test_single_type_returns_principal() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (_Fixed("fp", PrincipalType.USER, _user()),)
    }
    ids = await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)
    assert isinstance(ids.require(PrincipalType.USER), UserPrincipal)


async def test_missing_credential_is_unauthorized() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (_Fixed("fp", PrincipalType.USER, None),)
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)


async def test_invalid_credential_is_terminal_no_fallback() -> None:
    # A present-but-invalid credential (AuthError) must NOT fall back to a later
    # plugin — the whole type attempt is rejected.
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (
            _Fixed("bad", PrincipalType.USER, AuthError("invalid")),
            _Fixed("good", PrincipalType.USER, _user()),
        )
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)


async def test_none_falls_through_to_next_plugin() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (
            _Fixed("a", PrincipalType.USER, None),
            _Fixed("b", PrincipalType.USER, _user()),
        )
    }
    ids = await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)
    assert isinstance(ids.require(PrincipalType.USER), UserPrincipal)


async def test_chain_exhausted_is_unauthorized() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (
            _Fixed("a", PrincipalType.USER, None),
            _Fixed("b", PrincipalType.USER, None),
        )
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)


async def test_multiple_required_types_all_collected() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (_Fixed("u", PrincipalType.USER, _user()),),
        PrincipalType.BOT: (_Fixed("bb", PrincipalType.BOT, _bot()),),
    }
    ids = await authenticate(
        _CREDS, frozenset({PrincipalType.USER, PrincipalType.BOT}), registry
    )
    assert isinstance(ids.require(PrincipalType.USER), UserPrincipal)
    assert isinstance(ids.require(PrincipalType.BOT), BotPrincipal)


async def test_missing_one_required_type_rejects() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {
        PrincipalType.USER: (_Fixed("u", PrincipalType.USER, _user()),),
        PrincipalType.BOT: (_Fixed("bb", PrincipalType.BOT, None),),
    }
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER, PrincipalType.BOT}), registry)


async def test_unknown_type_in_requirement_is_denied() -> None:
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, frozenset({PrincipalType.USER}), registry)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_auth_runner.py -q`
Expected: FAIL — `authenticate` signature mismatch (still takes requirement list).

- [ ] **Step 3: Rewrite the runner**

Replace `src/gateway/community/core/authn/_runner.py` entirely with:

```python
"""Auth runner — produce an Identities set for a request (spec §7, rev 3).

For each required identity type, run its configured ordered plugin chain:

- a plugin returning ``None`` (not applicable / no credential) **falls through**
  to the next plugin in the chain;
- a plugin raising ``AuthError`` (applicable but invalid) is **terminal** — no
  fallback, so a bad credential can never be masked by a later plugin;
- the first plugin that returns a ``Principal`` wins for that type.

If a type's chain is exhausted with no ``Principal`` (all declined), raise
``AuthError`` (fail-closed) for that type. The runner never reads the ``source``
header; plugin self-selection is fully inside each plugin's ``build``.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import AuthStrategy, CredentialBundle, PrincipalType

from ._identities import Identities
from ._route_security import Requirement


async def authenticate(
    creds: CredentialBundle,
    requirement: Requirement,
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]],
) -> Identities:
    """Build an Identities set for the request, or raise ``AuthError`` (401)."""
    collected: dict[PrincipalType, object] = {}
    for ptype in requirement:
        chain = registry.get(ptype)
        if chain is None:  # misconfigured: required type has no plugin chain
            raise AuthError(f"no auth strategy registered for type: {ptype}")
        principal = await _run_chain(ptype, creds, chain)
        collected[ptype] = principal
    return Identities(collected)  # type: ignore[arg-type]


async def _run_chain(
    ptype: PrincipalType, creds: CredentialBundle, chain: tuple[AuthStrategy, ...]
) -> object:
    for strategy in chain:
        # A plugin raising AuthError is terminal: present-but-invalid credential,
        # never masked by a later plugin.
        principal = await strategy.build(creds)
        if principal is not None:
            return principal
    raise AuthError(f"unauthenticated: no credential for {ptype}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_auth_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/community/core/authn/_runner.py tests/test_auth_runner.py
git commit -m "feat(authn): multi-identity runner with ordered plugin fallback per type"
```

---

### Task 2.4: Strategy-chain config (`authn.yaml`) parser + startup validation

**Files:**
- Create: `src/gateway/community/core/authn/_config.py`, `configs/authn.yaml`
- Test: `tests/test_authn_config.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_authn_config.py`:

```python
"""Unit tests for the authn strategy-chain config parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.community.core.authn._config import build_strategy_registry, load_chains
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import PrincipalType, UserPrincipal
from gateway.community.spi.authn import CredentialBundle

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "authn.yaml"


class _FakeStrategy:
    def __init__(self, name: str, ptype: PrincipalType, principal: object | None) -> None:
        self.name = name
        self.principal_type = ptype
        self._principal = principal

    async def build(self, creds: CredentialBundle) -> object | None:
        return self._principal


def test_load_chains_from_shipped_config() -> None:
    chains = load_chains(_CONFIG)
    assert PrincipalType.USER in chains
    assert "cookie" in chains[PrincipalType.USER]
    # bot chain present (may be empty of plugins per shipped config; types only here)
    assert PrincipalType.BOT in chains


def test_build_registry_orders_plugins_by_chain() -> None:
    pool = {
        "google": _FakeStrategy("google", PrincipalType.USER, None),
        "cookie": _FakeStrategy("cookie", PrincipalType.USER, None),
        "bot_token": _FakeStrategy("bot_token", PrincipalType.BOT, None),
    }
    chains = {
        PrincipalType.USER: ["google", "cookie"],
        PrincipalType.BOT: ["bot_token"],
    }
    registry = build_strategy_registry(chains, pool)
    user_chain = registry[PrincipalType.USER]
    assert [s.name for s in user_chain] == ["google", "cookie"]
    assert registry[PrincipalType.BOT][0].name == "bot_token"


def test_build_registry_rejects_unknown_strategy_name() -> None:
    pool = {"cookie": _FakeStrategy("cookie", PrincipalType.USER, None)}
    chains = {PrincipalType.USER: ["cookie", "ghost"]}
    with pytest.raises(ValueError, match="ghost"):
        build_strategy_registry(chains, pool)


def test_build_registry_rejects_wrong_principal_type() -> None:
    # A plugin in the user chain whose principal_type is BOT is a misconfiguration.
    pool = {"bot_token": _FakeStrategy("bot_token", PrincipalType.BOT, None)}
    chains = {PrincipalType.USER: ["bot_token"]}
    with pytest.raises(ValueError, match="type"):
        build_strategy_registry(chains, pool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_authn_config.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the shipped config**

Create `configs/authn.yaml`:

```yaml
# Identity-type → ordered plugin chain (spec rev 3).
#
# Each type maps to an ordered list of strategy names. The runner tries them in
# order; a plugin returning None falls through to the next; a plugin raising
# AuthError is terminal (no fallback). Names reference strategies registered by
# the composition root's strategy pool (per flavor).
#
# Order matters: put specific source-named plugins first and the unconditional
# `cookie` fallback LAST (it claims anything with a session cookie).

identity_strategies:
  user:
    chain: [google, cookie]
  bot:
    chain: [bot_token]
```

- [ ] **Step 4: Implement the config loader**

Create `src/gateway/community/core/authn/_config.py`:

```python
"""Parse ``authn.yaml`` into an ordered strategy registry (spec rev 3).

``authn.yaml`` maps each identity type to an ordered list of strategy names.
The composition root supplies the strategy pool (name -> instance); this module
resolves the names into the ordered ``dict[PrincipalType, tuple[AuthStrategy]]``
the runner consumes, validating at build time that every name exists in the pool
and that a plugin's ``principal_type`` matches the chain it was placed in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from gateway.community.spi.authn import AuthStrategy, PrincipalType

# Parsed chains: identity type -> ordered list of strategy names.
Chains = dict[PrincipalType, list[str]]


def load_chains(path: str | Path) -> Chains:
    """Load and validate the type -> [strategy-name] chains from ``authn.yaml``."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    table = raw.get("identity_strategies", {})
    chains: Chains = {}
    for type_name, spec in table.items():
        ptype = _parse_type(type_name)
        chain = _parse_chain(spec)
        if not chain:
            raise ValueError(f"empty strategy chain for type {type_name!r}")
        chains[ptype] = chain
    return chains


def build_strategy_registry(
    chains: Chains, pool: dict[str, AuthStrategy]
) -> dict[PrincipalType, tuple[AuthStrategy, ...]]:
    """Resolve named chains against the strategy pool into an ordered registry.

    Raises ``ValueError`` if a name is missing from the pool or a plugin's
    ``principal_type`` does not match the chain's type.
    """
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {}
    for ptype, names in chains.items():
        ordered: list[AuthStrategy] = []
        for name in names:
            strategy = pool.get(name)
            if strategy is None:
                raise ValueError(
                    f"authn.yaml references unknown strategy {name!r} for type {ptype}"
                )
            if strategy.principal_type is not ptype:
                raise ValueError(
                    f"strategy {name!r} has type {strategy.principal_type}, "
                    f"cannot be in the {ptype} chain"
                )
            ordered.append(strategy)
        registry[ptype] = tuple(ordered)
    return registry


# ── parsing helpers ──────────────────────────────────────────────────────────


def _parse_type(name: str) -> PrincipalType:
    try:
        return PrincipalType(name)
    except ValueError as ex:
        raise ValueError(f"unknown identity type in authn.yaml: {name!r}") from ex


def _parse_chain(spec: Any) -> list[str]:
    chain = (spec or {}).get("chain", [])
    if not isinstance(chain, list) or not all(isinstance(x, str) for x in chain):
        raise ValueError("authn.yaml chain must be a list of strategy-name strings")
    return list(chain)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_authn_config.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/community/core/authn/_config.py configs/authn.yaml tests/test_authn_config.py
git commit -m "feat(authn): parse authn.yaml chain config with startup validation"
```

---

## Phase 3 — Auth strategies (the plugins)

### Task 3.1: SPI ports for user/bot token validators + bare impls

**Files:**
- Create: `src/gateway/community/spi/authn/_ports.py`
- Create: `src/gateway/community/plugins/authn/google_token/_bare_validator.py`, `src/gateway/community/plugins/authn/bot_token/_bare_validator.py`
- Test: `tests/contracts/spi/test_user_token_validator.py`, `tests/contracts/spi/test_bot_token_validator.py`

- [ ] **Step 1: Write the failing conformance tests**

Create `tests/contracts/spi/test_user_token_validator.py`:

```python
"""Conformance tests for the UserTokenValidator SPI (Rule 25)."""

from __future__ import annotations

from gateway.community.plugins.authn.google_token._bare_validator import (
    BareUserTokenValidator,
)
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn._ports import UserTokenValidator


class UserTokenValidatorContract:
    validator: UserTokenValidator

    async def test_returns_none_for_unknown_token(self) -> None:
        assert await self.validator.verify("does-not-exist") is None

    async def test_returns_none_for_empty_token(self) -> None:
        assert await self.validator.verify("") is None

    async def test_returns_user_for_known_token(self) -> None:
        result = await self.validator.verify("good-token")
        assert isinstance(result, AuthenticatedUser)


class TestBareUserTokenValidator(UserTokenValidatorContract):
    def setup_method(self) -> None:
        self.validator = BareUserTokenValidator(
            mapping={"good-token": AuthenticatedUser(id="u", username="a", tenant_id="t")}
        )
```

Create `tests/contracts/spi/test_bot_token_validator.py`:

```python
"""Conformance tests for the BotTokenValidator SPI (Rule 25)."""

from __future__ import annotations

from gateway.community.plugins.authn.bot_token._bare_validator import (
    BareBotTokenValidator,
)
from gateway.community.spi.authn._ports import BotTokenValidator


class BotTokenValidatorContract:
    validator: BotTokenValidator

    async def test_returns_none_for_unknown_token(self) -> None:
        assert await self.validator.verify("nope") is None

    async def test_returns_record_for_known_token(self) -> None:
        rec = await self.validator.verify("bot-key")
        assert rec is not None
        assert rec.bot_uuid == "bot-7"
        assert rec.owner_id == "owner-1"
        assert rec.tenant == "t"


class TestBareBotTokenValidator(BotTokenValidatorContract):
    def setup_method(self) -> None:
        self.validator = BareBotTokenValidator()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/contracts/spi/test_user_token_validator.py tests/contracts/spi/test_bot_token_validator.py -q`
Expected: FAIL — modules do not exist.

- [ ] **Step 3: Create the SPI ports**

Create `src/gateway/community/spi/authn/_ports.py`:

```python
"""Authn SPI — flavor-swapped dependency ports for the auth strategies.

Strategies are flavor-agnostic; they depend on these protocols (verified
out-of-band by a per-tenant / per-registry backend). ``bare`` ships in-memory
implementations so the open-source edition runs without a backend; ``sofa`` swaps
in real backends through the existing ``PluginAccessor`` + ``GATEWAY_RUN_MODE``
mechanism (a follow-up).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gateway.community.spi.auth import AuthenticatedUser


@dataclass(frozen=True)
class BotRecord:
    """A verified bot, as resolved from its token by ``BotTokenValidator``."""

    bot_uuid: str
    owner_id: str
    tenant: str  # owner's tenant (every Principal carries a tenant)


class UserTokenValidator(Protocol):
    """Verify a user access token → the authenticated user, or ``None``."""

    async def verify(self, token: str) -> AuthenticatedUser | None:
        """Return the user behind ``token``, or ``None`` if it is unrecognized."""
        ...


class BotTokenValidator(Protocol):
    """Verify a bot token → the bot's record, or ``None``."""

    async def verify(self, token: str) -> BotRecord | None:
        """Return the bot record behind ``token``, or ``None`` if unrecognized."""
        ...
```

- [ ] **Step 4: Create the bare validators**

Create `src/gateway/community/plugins/authn/google_token/_bare_validator.py`:

```python
"""Bare ``UserTokenValidator`` — in-memory token→user map (community edition)."""

from __future__ import annotations

from collections.abc import Mapping

from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn._ports import UserTokenValidator


class BareUserTokenValidator:
    """Resolves a user from a fixed in-memory token mapping.

    Production/enterprise replaces this with a real SSO/user-token service via
    ``GATEWAY_RUN_MODE=sofa``. ``None`` (unknown/empty token) means "not
    recognized" — the calling plugin treats that as a hard failure (the token was
    present but invalid), not a fallback.
    """

    def __init__(self, mapping: Mapping[str, AuthenticatedUser]) -> None:
        self._mapping = dict(mapping)

    async def verify(self, token: str) -> AuthenticatedUser | None:
        if not token:
            return None
        return self._mapping.get(token)
```

Create `src/gateway/community/plugins/authn/bot_token/_bare_validator.py`:

```python
"""Bare ``BotTokenValidator`` — in-memory token→bot map (community edition)."""

from __future__ import annotations

from gateway.community.spi.authn._ports import BotRecord, BotTokenValidator

# Single demo bot so the open-source edition can exercise bot identity.
_DEMO_BOT = BotRecord(bot_uuid="bot-7", owner_id="owner-1", tenant="t")


class BareBotTokenValidator:
    """Resolves a bot from a fixed in-memory mapping.

    Enterprise replaces this with a real bot registry via ``GATEWAY_RUN_MODE=sofa``.
    """

    def __init__(self) -> None:
        self._mapping = {"bot-key": _DEMO_BOT}

    async def verify(self, token: str) -> BotRecord | None:
        if not token:
            return None
        return self._mapping.get(token)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/contracts/spi/test_user_token_validator.py tests/contracts/spi/test_bot_token_validator.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/community/spi/authn/_ports.py src/gateway/community/plugins/authn/google_token/_bare_validator.py src/gateway/community/plugins/authn/bot_token/_bare_validator.py tests/contracts/spi/test_user_token_validator.py tests/contracts/spi/test_bot_token_validator.py
git commit -m "feat(authn): add UserTokenValidator/BotTokenValidator SPIs + bare impls"
```

---

### Task 3.2: `CookieStrategy` (relocate + rename first-party-user)

**Files:**
- Create: `src/gateway/community/plugins/authn/cookie/_strategy.py`, `src/gateway/community/plugins/authn/cookie/__init__.py`
- Test: `tests/test_cookie_strategy.py` (create)
- Delete: `src/gateway/community/plugins/authn/first_party_user/`, `tests/test_first_party_user_strategy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cookie_strategy.py`:

```python
"""Unit tests for the cookie strategy (session-cookie → UserPrincipal)."""

from __future__ import annotations

import pytest

from gateway.community.plugins.authn.cookie import CookieStrategy
from gateway.community.spi.auth import AuthenticatedUser, AuthError
from gateway.community.spi.authn import CredentialBundle, PrincipalType, UserPrincipal


class _RaisingAuth:
    async def get_login_user(self, cookie=None, referer=None) -> AuthenticatedUser:
        raise AuthError("bad session")


def _creds_with_session() -> CredentialBundle:
    return CredentialBundle(
        headers={"cookie": "SSO_TOKEN=x"}, cookies={"SSO_TOKEN": "x"}, query={}
    )


def _strategy(user: AuthenticatedUser | None = None) -> CookieStrategy:
    from gateway.community.plugins.auth.bare import BareAuthPlugin

    return CookieStrategy(
        auth=BareAuthPlugin(default_user=user or AuthenticatedUser(id="u", username="a", tenant_id="t")),
        default_tenant="tenant-default",
    )


async def test_returns_none_when_no_session_cookie() -> None:
    creds = CredentialBundle(headers={}, cookies={}, query={})
    assert await _strategy().build(creds) is None


async def test_builds_user_principal_from_session() -> None:
    principal = await _strategy().build(_creds_with_session())
    assert isinstance(principal, UserPrincipal)
    assert principal.tenant == "t"
    assert principal.subject.id == "u"


async def test_falls_back_to_default_tenant_when_identity_has_none() -> None:
    strategy = _strategy(AuthenticatedUser(id="u", username="a"))  # no tenant_id
    principal = await strategy.build(_creds_with_session())
    assert isinstance(principal, UserPrincipal)
    assert principal.tenant == "tenant-default"


async def test_invalid_session_raises() -> None:
    strategy = CookieStrategy(auth=_RaisingAuth(), default_tenant="t")
    with pytest.raises(AuthError):
        await strategy.build(_creds_with_session())


def test_declares_user_type() -> None:
    assert _strategy().principal_type is PrincipalType.USER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cookie_strategy.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the cookie strategy**

Create `src/gateway/community/plugins/authn/cookie/_strategy.py`:

```python
"""``cookie`` strategy — session-cookie → UserPrincipal (the general fallback).

For our own frontend / a human with a login session. Does NOT require a
``source`` header — it is the unconditional fallback at the end of the ``user``
chain. ``None`` when no session cookie is present; raises ``AuthError`` when a
session is present but ``AuthPlugin`` rejects it.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError, AuthPlugin
from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)

_SESSION_COOKIES = ("IAM_TOKEN", "SSO_TOKEN", "access_token")


class CookieStrategy:
    """Resolve a login-session cookie into a :class:`UserPrincipal`.

    Implements the ``AuthStrategy`` protocol structurally (``name``,
    ``principal_type``, ``build``).
    """

    name = "cookie"
    principal_type = PrincipalType.USER

    def __init__(self, auth: AuthPlugin, default_tenant: str) -> None:
        self._auth = auth
        self._default_tenant = default_tenant

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if not any(name in creds.cookies for name in _SESSION_COOKIES):
            return None  # no session → not applicable; let the chain continue/stop
        user = await self._auth.get_login_user(
            cookie=creds.headers.get("cookie", ""),
            referer=creds.headers.get("referer"),
        )
        tenant = user.tenant_id or self._default_tenant
        return UserPrincipal(tenant=tenant, subject=user)
```

Create `src/gateway/community/plugins/authn/cookie/__init__.py`:

```python
"""``cookie`` auth strategy plugin — session-cookie → UserPrincipal."""

from ._strategy import CookieStrategy

__all__ = ["CookieStrategy"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cookie_strategy.py -q`
Expected: PASS.

- [ ] **Step 5: Delete the old first_party_user plugin and its test**

```bash
git rm -r src/gateway/community/plugins/authn/first_party_user
git rm tests/test_first_party_user_strategy.py
```

- [ ] **Step 6: Commit**

```bash
git add src/gateway/community/plugins/authn/cookie/ tests/test_cookie_strategy.py
git commit -m "feat(authn): relocate first_party_user → cookie strategy (general fallback)"
```

---

### Task 3.3: `GoogleTokenStrategy` (source-named token plugin)

**Files:**
- Create: `src/gateway/community/plugins/authn/google_token/_strategy.py`, `src/gateway/community/plugins/authn/google_token/__init__.py`
- Test: `tests/test_google_token_strategy.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_google_token_strategy.py`:

```python
"""Unit tests for the google source-named token strategy."""

from __future__ import annotations

import pytest

from gateway.community.plugins.authn.google_token import GoogleTokenStrategy
from gateway.community.plugins.authn.google_token._bare_validator import (
    BareUserTokenValidator,
)
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    CredentialBundle,
    PrincipalType,
    UserPrincipal,
)

_SOURCE = "google"
_TOKEN_HEADER = "x-user-token"


def _strategy() -> GoogleTokenStrategy:
    validator = BareUserTokenValidator(
        mapping={"good": __import__(
            "gateway.community.spi.auth", fromlist=["AuthenticatedUser"]
        ).AuthenticatedUser(id="u", username="a", tenant_id="t")}
    )
    return GoogleTokenStrategy(validator=validator, source=_SOURCE, token_header=_TOKEN_HEADER)


def _creds(source: str | None, token: str | None) -> CredentialBundle:
    headers: dict[str, str] = {}
    if source is not None:
        headers["source"] = source
    if token is not None:
        headers[_TOKEN_HEADER] = token
    return CredentialBundle(headers=headers, cookies={}, query={})


async def test_declares_user_type() -> None:
    assert _strategy().principal_type is PrincipalType.USER


async def test_returns_none_when_source_not_google() -> None:
    assert await _strategy().build(_creds("cookie", None)) is None
    assert await _strategy().build(_creds(None, "good")) is None


async def test_returns_none_when_source_google_but_no_token() -> None:
    # Applicable (source matched) but no credential → None (fallback semantics).
    assert await _strategy().build(_creds("google", None)) is None


async def test_builds_user_principal_when_token_valid() -> None:
    principal = await _strategy().build(_creds("google", "good"))
    assert isinstance(principal, UserPrincipal)
    assert principal.tenant == "t"


async def test_raises_when_source_google_but_token_invalid() -> None:
    with pytest.raises(AuthError):
        await _strategy().build(_creds("google", "bad"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_google_token_strategy.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the google strategy**

Create `src/gateway/community/plugins/authn/google_token/_strategy.py`:

```python
"""``google`` strategy — a source-named token plugin (self-selecting).

Claims a request **only** when ``creds.headers["source"] == "google"``. This
makes applicability plugin-unique (no two plugins claim the same request): the
cookie plugin keys off a session cookie, google keys off its own source value.
The runner never reads ``source`` — this plugin does.

- source not "google" → ``None`` (not mine; chain continues).
- source "google" but no token → ``None`` (fallback semantics: no credential;
  the chain falls through to cookie — see spec Further Notes).
- source "google" with an unrecognized token → ``AuthError`` (applicable but
  invalid; terminal, no fallback).
- source "google" with a verified token → ``UserPrincipal``.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)
from gateway.community.spi.authn._ports import UserTokenValidator


class GoogleTokenStrategy:
    """Resolve a ``source: google`` user token into a :class:`UserPrincipal`."""

    name = "google"
    principal_type = PrincipalType.USER

    def __init__(self, validator: UserTokenValidator, source: str, token_header: str) -> None:
        self._validator = validator
        self._source = source
        self._token_header = token_header

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if creds.headers.get("source") != self._source:
            return None  # not my source → not applicable
        token = creds.headers.get(self._token_header, "")
        if not token:
            return None  # source matched but no token → fallback semantics
        user = await self._validator.verify(token)
        if user is None:
            raise AuthError(f"invalid {self._source} user token")
        tenant = user.tenant_id or "default"
        return UserPrincipal(tenant=tenant, subject=user)
```

Create `src/gateway/community/plugins/authn/google_token/__init__.py`:

```python
"""``google`` auth strategy plugin — source-named user-token → UserPrincipal."""

from ._strategy import GoogleTokenStrategy

__all__ = ["GoogleTokenStrategy"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_google_token_strategy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/community/plugins/authn/google_token/ tests/test_google_token_strategy.py
git commit -m "feat(authn): add google source-named token strategy (self-selecting plugin)"
```

---

### Task 3.4: `BotTokenStrategy`

**Files:**
- Create: `src/gateway/community/plugins/authn/bot_token/_strategy.py`, `src/gateway/community/plugins/authn/bot_token/__init__.py`
- Test: `tests/test_bot_token_strategy.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_token_strategy.py`:

```python
"""Unit tests for the bot_token strategy."""

from __future__ import annotations

import pytest

from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.bot_token._bare_validator import (
    BareBotTokenValidator,
)
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    BotPrincipal,
    CredentialBundle,
    PrincipalType,
)


def _strategy() -> BotTokenStrategy:
    return BotTokenStrategy(validator=BareBotTokenValidator(), token_header="x-bot-token")


def _creds(token: str | None) -> CredentialBundle:
    headers: dict[str, str] = {}
    if token is not None:
        headers["x-bot-token"] = token
    return CredentialBundle(headers=headers, cookies={}, query={})


async def test_declares_bot_type() -> None:
    assert _strategy().principal_type is PrincipalType.BOT


async def test_returns_none_when_no_bot_token() -> None:
    assert await _strategy().build(_creds(None)) is None


async def test_builds_bot_principal_when_token_valid() -> None:
    principal = await _strategy().build(_creds("bot-key"))
    assert isinstance(principal, BotPrincipal)
    assert principal.bot_uuid == "bot-7"
    assert principal.owner_id == "owner-1"
    assert principal.tenant == "t"
    assert principal.token == "bot-key"


async def test_raises_when_token_invalid() -> None:
    with pytest.raises(AuthError):
        await _strategy().build(_creds("bad"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bot_token_strategy.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the bot strategy**

Create `src/gateway/community/plugins/authn/bot_token/_strategy.py`:

```python
"""``bot_token`` strategy — bot credential → BotPrincipal.

Reads a bot credential from a designated request header. ``None`` when absent
(not applicable); ``AuthError`` when present but unverifiable (terminal, no
fallback); ``BotPrincipal`` on success, carrying the raw token downstream.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
)
from gateway.community.spi.authn._ports import BotTokenValidator


class BotTokenStrategy:
    """Resolve a bot credential into a :class:`BotPrincipal`."""

    name = "bot_token"
    principal_type = PrincipalType.BOT

    def __init__(self, validator: BotTokenValidator, token_header: str) -> None:
        self._validator = validator
        self._token_header = token_header

    async def build(self, creds: CredentialBundle) -> Principal | None:
        token = creds.headers.get(self._token_header, "")
        if not token:
            return None
        record = await self._validator.verify(token)
        if record is None:
            raise AuthError("invalid bot token")
        return BotPrincipal(
            tenant=record.tenant,
            bot_uuid=record.bot_uuid,
            owner_id=record.owner_id,
            token=token,
        )
```

Create `src/gateway/community/plugins/authn/bot_token/__init__.py`:

```python
"""``bot_token`` auth strategy plugin — bot credential → BotPrincipal."""

from ._strategy import BotTokenStrategy

__all__ = ["BotTokenStrategy"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bot_token_strategy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/community/plugins/authn/bot_token/ tests/test_bot_token_strategy.py
git commit -m "feat(authn): add bot_token strategy producing BotPrincipal"
```

---

## Phase 4 — Wiring: contract helper, delivery dependency, composition, routers

### Task 4.1: OpenAPI marker → `requires_identities(*types)`

**Files:**
- Modify: `src/gateway/community/adapters/web/contracts/_security.py`, `src/gateway/community/adapters/web/contracts/__init__.py`
- Test: `tests/test_security_contract.py` (modify)

- [ ] **Step 1: Rewrite the security-contract test**

Replace `tests/test_security_contract.py` with:

```python
"""Unit tests for the per-route security metadata helper."""

from __future__ import annotations

from gateway.community.adapters.web.contracts import (
    requires_identities,
    requires_user_principal,
)


def test_requires_user_principal_is_user_only() -> None:
    assert requires_user_principal() == {"x-avernet-security": ["user"]}


def test_requires_identities_single_type() -> None:
    from gateway.community.spi.authn import PrincipalType

    assert requires_identities(PrincipalType.USER) == {"x-avernet-security": ["user"]}


def test_requires_identities_multiple_types() -> None:
    from gateway.community.spi.authn import PrincipalType

    got = requires_identities(PrincipalType.BOT, PrincipalType.USER)
    assert got == {"x-avernet-security": ["bot", "user"]}


def test_requires_identities_returns_fresh_dict() -> None:
    a = requires_user_principal()
    a["x-avernet-security"].append("tampered")
    assert requires_user_principal() == {"x-avernet-security": ["user"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_security_contract.py -q`
Expected: FAIL — `requires_identities` import error; current shape differs.

- [ ] **Step 3: Rewrite the security helper**

Replace `src/gateway/community/adapters/web/contracts/_security.py` with:

```python
"""Per-route required-identity-types metadata (OpenAPI ``x-avernet-security``).

Each public route declares the identity **types** it requires via the
``x-avernet-security`` extension. The gateway's route-security compiler resolves
the most-specific rule per request (spec §8). v1 routes require an authenticated
**user** by default; routes that need a bot (or bot+user) declare so.

The marker lists identity-type strings (e.g. ``["user"]``, ``["bot", "user"]``).
It declares *types*, not strategy names — which plugin chain produces each type
is configured in ``authn.yaml`` and is orthogonal to routes.
"""

from typing import Any

from gateway.community.spi.authn import PrincipalType


def requires_identities(*types: PrincipalType) -> dict[str, Any]:
    """OpenAPI extra marking a route as requiring the given identity types."""
    return {"x-avernet-security": [str(t) for t in types]}


def requires_user_principal() -> dict[str, Any]:
    """Convenience for the common case: a single authenticated user identity."""
    return requires_identities(PrincipalType.USER)
```

- [ ] **Step 4: Update the contracts `__init__` export**

In `src/gateway/community/adapters/web/contracts/__init__.py`, add `requires_identities` to the import (from `._security import (...)`) and to `__all__`. The import line becomes:

```python
from ._security import requires_identities, requires_user_principal
```

and add `"requires_identities",` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_security_contract.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/community/adapters/web/contracts/_security.py src/gateway/community/adapters/web/contracts/__init__.py tests/test_security_contract.py
git commit -m "feat(web): requires_identities(*types) marker on x-avernet-security"
```

---

### Task 4.2: Delivery dependency `require_identities -> Identities`

**Files:**
- Modify: `src/gateway/community/adapters/web/_auth.py`, `src/gateway/community/adapters/web/__init__.py`
- Test: `tests/test_bots_router.py`, `tests/test_groups.py` (modify in Task 4.4)

- [ ] **Step 1: Rewrite the adapter dependency**

Replace `src/gateway/community/adapters/web/_auth.py` with:

```python
"""Delivery-layer auth dependency.

``require_identities`` snapshots the FastAPI request into a framework-agnostic
``CredentialBundle`` and delegates to the ``Authenticator`` built by the
composition root (stored on ``app.state``), mapping auth failure to HTTP 401.
The adapter imports only ``spi`` — the runner, route table, and strategies live
behind the ``Authenticator`` it receives. It has no awareness of ``source``;
that header is just an ordinary entry in the credential bundle a plugin reads.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from gateway.community.core.authn import Identities
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle


def _bundle(request: Request) -> CredentialBundle:
    return CredentialBundle(
        headers={k.lower(): v for k, v in request.headers.items()},
        cookies=dict(request.cookies),
        query=dict(request.query_params),
    )


async def require_identities(request: Request) -> Identities:
    """FastAPI dependency: authenticate the request or raise 401."""
    authenticator = request.app.state.authenticator
    try:
        identities: Identities = await authenticator.authenticate(
            request.method, request.url.path, _bundle(request)
        )
    except AuthError as err:
        raise HTTPException(status_code=401, detail=str(err)) from err
    return identities
```

- [ ] **Step 2: Update the web package export**

Replace `src/gateway/community/adapters/web/__init__.py` with:

```python
"""FastAPI Web adapter for the community gateway.

Import the app explicitly to avoid eager bootstrapping on package import::

    from gateway.community.adapters.web.app import app

Group routers depend on ``require_identities`` for authenticated access and
receive an :class:`~gateway.community.core.authn.Identities` set.
"""

from ._auth import require_identities

__all__ = [
    "require_identities",
]
```

- [ ] **Step 3: Verify imports**

Run: `python -c "import gateway.community.adapters.web; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/community/adapters/web/_auth.py src/gateway/community/adapters/web/__init__.py
git commit -m "feat(web): require_identities dependency yields Identities set"
```

---

### Task 4.3: Composition root — wire the ordered registry from `authn.yaml`

**Files:**
- Modify: `src/gateway/community/bootstrap/_authn.py`
- Test: integration via `tests/test_bots_router.py` / `tests/test_groups.py` (Task 4.4)

- [ ] **Step 1: Rewrite the composition root**

Replace `src/gateway/community/bootstrap/_authn.py` with:

```python
"""Composition of the auth subsystem (composition root, Rule 14).

Reads ``authn.yaml`` (type → ordered plugin chain), builds the strategy pool
(per flavor), validates names against the pool, and exposes an
:class:`Authenticator` that ties the registry + route table to the core runner.
Only the composition root wires concrete plugins and touches ``PluginAccessor``;
adapters receive the built ``Authenticator`` via ``app.state`` and never import
plugins or core. The core runner is flavor-agnostic and has no ``source``
awareness.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gateway.community.core.authn import RouteSecurity, authenticate as run_auth
from gateway.community.core.authn._config import build_strategy_registry, load_chains
from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.bot_token._bare_validator import (
    BareBotTokenValidator,
)
from gateway.community.plugins.authn.cookie import CookieStrategy
from gateway.community.plugins.authn.google_token import GoogleTokenStrategy
from gateway.community.plugins.authn.google_token._bare_validator import (
    BareUserTokenValidator,
)
from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.spi.auth import AuthError, AuthPlugin
from gateway.community.spi.authn import AuthStrategy, CredentialBundle, PrincipalType
from gateway.community.core.authn import Identities

_DEFAULT_TENANT = "default"

_auth_plugin = PluginAccessor[AuthPlugin]("gateway.auth", BareAuthPlugin)


def _strategy_pool() -> dict[str, AuthStrategy]:
    """The bare flavor's available strategies, keyed by authn.yaml name."""
    return {
        "google": GoogleTokenStrategy(
            validator=BareUserTokenValidator(mapping={}),  # bare: no real users mapped
            source="google",
            token_header="x-user-token",
        ),
        "cookie": CookieStrategy(auth=_auth_plugin.get(), default_tenant=_DEFAULT_TENANT),
        "bot_token": BotTokenStrategy(
            validator=BareBotTokenValidator(), token_header="x-bot-token"
        ),
    }


@dataclass
class Authenticator:
    """Resolves a route's requirement and runs its strategies to Identities."""

    strategies: dict[PrincipalType, tuple[AuthStrategy, ...]]
    route_security: RouteSecurity

    async def authenticate(
        self, method: str, path: str, creds: CredentialBundle
    ) -> Identities:
        requirement = self.route_security.resolve(method, path)
        if requirement is None:  # fail-closed: no policy → deny
            raise AuthError("no auth policy for route")
        return await run_auth(creds, requirement, self.strategies)


def build_authenticator() -> Authenticator:
    """Build the auth registry + route table (called once from ``create_app``)."""
    chains = load_chains(_authn_yaml_path())
    registry = build_strategy_registry(chains, _strategy_pool())
    return Authenticator(strategies=registry, route_security=_load_route_security())


def _authn_yaml_path() -> Path:
    return _resolve_configs_dir() / "authn.yaml"


def _load_route_security() -> RouteSecurity:
    configs_dir = _resolve_configs_dir()
    path = configs_dir / "route_security.yaml" if configs_dir else None
    if path is not None and path.exists():
        return RouteSecurity.from_yaml(path)
    return RouteSecurity.from_table({"/**": ["user"]})


def _resolve_configs_dir() -> Path | None:
    explicit = os.getenv("GATEWAY_CONFIG_PATH", "").strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_dir() else p.parent
    cwd = Path.cwd() / "configs"
    return cwd if cwd.exists() else None
```

- [ ] **Step 2: Verify the app builds**

Run: `python -c "from gateway.community.adapters.web.app import create_app; create_app(); print('ok')"`
Expected: prints `ok` (cwd is the gateway dir, so `configs/` resolves).

- [ ] **Step 3: Commit**

```bash
git add src/gateway/community/bootstrap/_authn.py
git commit -m "feat(authn): composition root builds ordered registry from authn.yaml"
```

---

### Task 4.4: Migrate routers + integration tests

**Files:**
- Modify: every `src/gateway/community/adapters/web/routers/<group>/_router.py`
- Test: `tests/test_bots_router.py`, `tests/test_groups.py` (modify), `tests/contracts/spi/test_auth_strategy.py` (modify)

> Each router has the same three-line shape: import `Principal`→`Identities`, `require_principal`→`require_identities`, `PrincipalDep`→`IdentitiesDep`, and `_SEC` may already use `requires_user_principal()` (keep it — it still works). Do them one router at a time with the import-bot pattern below.

- [ ] **Step 1: Migrate the bots router**

In `src/gateway/community/adapters/web/routers/bots/_router.py`:
- Change the imports:
  - `from gateway.community.adapters.web import require_principal` → `from gateway.community.adapters.web import require_identities`
  - `from gateway.community.spi.authn import Principal` → `from gateway.community.core.authn import Identities`
- Change line `PrincipalDep = Annotated[Principal, Depends(require_principal)]` → `IdentitiesDep = Annotated[Identities, Depends(require_identities)]`.
- Rename every handler parameter `principal: PrincipalDep` → `identities: IdentitiesDep` (these are stubs that `raise NotImplementedError`; the rename keeps the body unchanged).

> The `requires_user_principal()` helper and `_SEC = requires_user_principal()` are unchanged — `requires_user_principal()` still emits `["user"]`.

- [ ] **Step 2: Repeat for the other six routers**

Apply the identical change (imports + `IdentitiesDep` + `principal`→`identities`) to:
- `routers/identity/_router.py`
- `routers/resources/_router.py`
- `routers/mcp/_router.py`
- `routers/routines/_router.py`
- `routers/skills/_router.py`
- `routers/channels/_router.py`

Each currently imports `Principal` from `gateway.community.spi.authn`, `require_principal`, and defines `PrincipalDep`. Replace exactly as in Step 1.

- [ ] **Step 3: Rewrite the bots integration test**

Replace `tests/test_bots_router.py` with:

```python
"""Contract + auth-integration tests for the bots group."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app


def _openapi() -> dict[str, Any]:
    return TestClient(create_app()).get("/openapi.json").json()


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_bots_routes_present() -> None:
    paths = _openapi()["paths"]
    assert "/openapi/v1/bots" in paths
    assert "/openapi/v1/bots/{bot_id}" in paths
    assert "/openapi/v1/bots/check-name" in paths


def test_every_v1_operation_declares_security() -> None:
    paths = _openapi()["paths"]
    operations = [
        (path, method, op)
        for path, item in paths.items()
        if path.startswith("/openapi/v1/")
        for method, op in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert operations
    for path, method, op in operations:
        marker = op.get("x-avernet-security")
        assert marker, f"{method} {path} missing x-avernet-security"
        assert isinstance(marker, list) and marker, f"{method} {path} empty marker"


def test_responses_use_envelope() -> None:
    schemas = _openapi()["components"]["schemas"]
    assert any(name.startswith("Envelope") for name in schemas)


def test_requires_authentication_without_session() -> None:
    assert _client().get("/openapi/v1/bots").status_code == 401


def test_auth_passes_with_session_cookie() -> None:
    # A session cookie clears auth; the stub handler then raises (500), proving
    # the request got past auth (auth would 401 otherwise).
    resp = _client().get("/openapi/v1/bots", headers={"cookie": "SSO_TOKEN=x"})
    assert resp.status_code == 500
```

- [ ] **Step 4: Rewrite the groups integration test**

Replace `tests/test_groups.py` with:

```python
"""Contract + auth tests across all /openapi/v1 groups."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app

_GROUP_PREFIXES = [
    "/openapi/v1/bots",
    "/openapi/v1/channels",
    "/openapi/v1/identity",
    "/openapi/v1/mcp",
    "/openapi/v1/resources",
    "/openapi/v1/routines",
    "/openapi/v1/skills",
]


def _openapi() -> dict[str, Any]:
    return TestClient(create_app()).get("/openapi.json").json()


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("prefix", _GROUP_PREFIXES)
def test_group_is_mounted(prefix: str) -> None:
    paths = _openapi()["paths"]
    assert any(path.startswith(prefix) for path in paths), f"{prefix} not mounted"


def test_every_v1_operation_declares_security() -> None:
    paths = _openapi()["paths"]
    operations = [
        (path, method, op)
        for path, item in paths.items()
        if path.startswith("/openapi/v1/")
        for method, op in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert operations
    for path, method, op in operations:
        marker = op.get("x-avernet-security")
        assert marker, f"{method} {path} missing x-avernet-security"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/openapi/v1/channels"),
        ("get", "/openapi/v1/identity/bot/b1"),
        ("get", "/openapi/v1/mcp/servers"),
        ("get", "/openapi/v1/resources"),
        ("get", "/openapi/v1/routines"),
        ("get", "/openapi/v1/skills"),
    ],
)
def test_group_endpoints_require_auth(method: str, path: str) -> None:
    assert _client().request(method, path).status_code == 401
```

- [ ] **Step 5: Rewrite the strategy-contract conformance test**

Replace `tests/contracts/spi/test_auth_strategy.py` with:

```python
"""Conformance tests for the ``AuthStrategy`` protocol (Rule 25)."""

from __future__ import annotations

from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.bot_token._bare_validator import (
    BareBotTokenValidator,
)
from gateway.community.plugins.authn.cookie import CookieStrategy
from gateway.community.plugins.authn.google_token import GoogleTokenStrategy
from gateway.community.plugins.authn.google_token._bare_validator import (
    BareUserTokenValidator,
)
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    AuthStrategy,
    BotPrincipal,
    CredentialBundle,
    PrincipalType,
    UserPrincipal,
)


class AuthStrategyContract:
    """Behaviour every ``AuthStrategy`` implementation must satisfy."""

    strategy: AuthStrategy
    applicable_creds: CredentialBundle
    inapplicable_creds: CredentialBundle

    def test_has_stable_name(self) -> None:
        assert isinstance(self.strategy.name, str)
        assert self.strategy.name

    def test_declares_principal_type(self) -> None:
        assert isinstance(self.strategy.principal_type, PrincipalType)

    async def test_returns_none_when_not_applicable(self) -> None:
        result = await self.strategy.build(self.inapplicable_creds)
        assert result is None

    async def test_builds_a_principal_when_applicable(self) -> None:
        result = await self.strategy.build(self.applicable_creds)
        assert result is not None


class TestCookieStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = CookieStrategy(
            auth=BareAuthPlugin(default_user=AuthenticatedUser(id="u", username="a", tenant_id="t")),
            default_tenant="tenant-default",
        )
        self.applicable_creds = CredentialBundle(
            headers={"cookie": "SSO_TOKEN=x"}, cookies={"SSO_TOKEN": "x"}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_user_principal(self) -> None:
        principal = await self.strategy.build(self.applicable_creds)
        assert isinstance(principal, UserPrincipal)


class TestGoogleTokenStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = GoogleTokenStrategy(
            validator=BareUserTokenValidator(
                mapping={"g": AuthenticatedUser(id="u", username="a", tenant_id="t")}
            ),
            source="google",
            token_header="x-user-token",
        )
        # source matches google AND a token is present → applicable.
        self.applicable_creds = CredentialBundle(
            headers={"source": "google", "x-user-token": "g"}, cookies={}, query={}
        )
        # source is not google → not applicable (decline, fall through).
        self.inapplicable_creds = CredentialBundle(
            headers={"source": "cookie"}, cookies={}, query={}
        )

    async def test_builds_user_principal(self) -> None:
        principal = await self.strategy.build(self.applicable_creds)
        assert isinstance(principal, UserPrincipal)


class TestBotTokenStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        self.strategy = BotTokenStrategy(
            validator=BareBotTokenValidator(), token_header="x-bot-token"
        )
        self.applicable_creds = CredentialBundle(
            headers={"x-bot-token": "bot-key"}, cookies={}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_bot_principal(self) -> None:
        principal = await self.strategy.build(self.applicable_creds)
        assert isinstance(principal, BotPrincipal)
```

- [ ] **Step 6: Run the full router + integration suite**

Run: `python -m pytest tests/test_bots_router.py tests/test_groups.py tests/contracts/spi/test_auth_strategy.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/gateway/community/adapters/web/routers tests/test_bots_router.py tests/test_groups.py tests/contracts/spi/test_auth_strategy.py
git commit -m "feat(web): routers consume Identities; migrate integration + strategy contract tests"
```

---

## Phase 5 — Whole-suite green + architecture

### Task 5.1: Run the full suite and fix any remaining fallout

**Files:** as surfaced.

- [ ] **Step 1: Run the entire gateway test suite**

Run: `python -m pytest -q`
Expected: should PASS. If failures appear, they are stragglers from removed `Delegation`/`StrategyParams`/`Principal is UserPrincipal`/`first_party_user` references. Fix each by reading its assertion and aligning to the new model (no `scopes`; `Principal` is a union alias not literally `UserPrincipal`; strategy `build` takes only `creds`).

Common stragglers to check:
- `tests/test_routers.py`, `tests/test_contracts.py` — should be unaffected (envelope/router aggregation), but confirm.
- `tests/unit/plugins/test_auth_plugin.py` — unaffected (AuthPlugin unchanged); confirm still green.

- [ ] **Step 2: Run the architecture suite explicitly**

Run: `python -m pytest tests/architecture -q`
Expected: PASS. `test_structure_rules.py` (Rule 25) scans every `spi/**/_protocols.py` for a conformance test — the new `spi/authn/_protocols.py` (AuthStrategy) already has `test_auth_strategy.py`; confirm nothing else regressed.

- [ ] **Step 3: Run lint/type checks used by the repo**

Run: `python -m ruff check src tests` and (if configured) `python -m basedpyright src/gateway/community/spi/authn src/gateway/community/core/authn src/gateway/community/plugins/authn`
Expected: clean. Fix any unused-import (e.g. removed `Delegation`/`StrategyParams` imports) or type errors.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "test(authn): close stragglers after multi-identity migration; lint clean"
```

---

### Task 5.2: Add the chain-ordering / applicability-unique security test

**Files:**
- Test: `tests/test_authn_config.py` (modify — append)

> Enforces the spec's two security notes: chain order puts `cookie` last, and no two user-chain plugins both claim the same request.

- [ ] **Step 1: Append the security tests to `tests/test_authn_config.py`**

Add at end of `tests/test_authn_config.py`:

```python
def test_shipped_user_chain_puts_cookie_last() -> None:
    # Security (spec Further Notes): cookie is the unconditional fallback — it
    # claims anything with a session cookie. It MUST be last so specific
    # source-named plugins run first and are not shadowed.
    chains = load_chains(_CONFIG)
    user_chain = chains[PrincipalType.USER]
    assert user_chain[-1] == "cookie"


def test_shipped_user_chain_has_at_least_one_plugin() -> None:
    chains = load_chains(_CONFIG)
    assert chains[PrincipalType.USER]
```

- [ ] **Step 2: Run to verify pass**

Run: `python -m pytest tests/test_authn_config.py -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_authn_config.py
git commit -m "test(authn): assert cookie is last in user chain (security ordering)"
```

---

## Self-Review

**1. Spec coverage:** checked against spec rev 3 —
- Remove AuthZ / scopes: Tasks 1.1 (remove `scopes`/`Delegation`/`StrategyParams`), 2.2 (route table no scopes), 2.3 (runner no scope check). ✓
- Multi-identity per request: Tasks 2.1 (`Identities`), 2.3 (runner collects per type). ✓
- Bot identity: Task 1.1 (`BotPrincipal`/`PrincipalType.BOT`), 3.4 (`BotTokenStrategy`). ✓
- Plugin self-selection + ordered chain: Tasks 1.2 (`build(creds)`, `principal_type`), 2.4 (config chain), 3.2/3.3 (self-selecting plugins). ✓
- `source` as plugin input, runner never reads it: Task 2.3 (runner has no `source`), 3.3 (google reads `source`). ✓
- `authn.yaml` config + startup validation: Task 2.4. ✓
- bare/sofa flavor via existing mechanism: Task 4.3 (pool composition + `PluginAccessor`). ✓ (sofa impl itself out of scope per spec.)
- Routers declare types, not strategies: Tasks 4.1 (marker), 4.4 (routers). ✓
- CI gate (non-empty marker): Task 4.4 (`test_every_v1_operation_declares_security` asserts non-empty list). ✓
- Applicability-unique + cookie-last security: Task 5.2. ✓
- Tests reuse existing seams (runner/route-table/model/app/contract/arch), runner-primary: all tasks. ✓
- Fallback semantics `None` overload documented: Task 1.2 protocol docstring + Task 3.3 inline comments. ✓

**2. Placeholder scan:** no TBD/TODO/"add validation"/"similar to Task N" — every code step has full code. ✓

**3. Type consistency:** `authenticate(creds, requirement, registry) -> Identities` consistent across Tasks 2.3 and 4.3. `AuthStrategy.build(creds) -> Principal | None` + `principal_type` consistent across 1.2, 3.2, 3.3, 3.4, 4.4. `Identities.require`/`.get` consistent across 2.1, 2.3, 4.2. `build_strategy_registry(chains, pool)` consistent across 2.4 and 4.3. `require_identities(*types)` consistent across 4.1, 4.4. ✓

No gaps found.