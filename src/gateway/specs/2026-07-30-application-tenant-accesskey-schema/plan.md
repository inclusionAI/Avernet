# Canonical application/tenant/access_key schema — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `avernet_apps` / `baas_access_key_token` with the richer canonical schema (`avernet_application`, `avernet_access_key_token`) plus a new `avernet_tenant` master table, migrating the app domain off the stored `app_id` column onto the surrogate bigint `id`, and aligning the bot domain's `app_id` reference to `int`.

**Architecture:** All tables stay SQLAlchemy ORM models on the shared `Base`, created by `DataSourcePlugin.create_all()` (the bare SQLite flavor already downgrades `BIGINT` PKs to `Integer`). The app's stable identity becomes the surrogate `id`; `AppPrincipal.app.app_id` and `BotPrincipal.bot.app_id` carry that `id` as `int`. Access-key SPI is unchanged (audit columns stay DB-side). `avernet_tenant` is ORM-only (no SPI/HTTP) until a consumer appears.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (ORM, `Mapped`/`mapped_column`), Pydantic 2 (principal models), FastAPI (admin routes), PyJWT (credential JWTs), pytest + pytest-asyncio, ruff, basedpyright (strict), mypy (strict).

**Spec:** `specs/2026-07-30-application-tenant-accesskey-schema/spec.md`

**Run commands (all from `src/gateway`):**
- Targeted test: `uv run pytest <path>::<test> -v`
- Lint/format: `uv run ruff check . --fix && uv run ruff format .`
- Type: `uv run basedpyright src && uv run mypy src tests`
- Full suite: `uv run pytest`

**Key invariants for implementers:**
- `build_database()` (bootstrap) caches one in-memory SQLite via `_db_plugin.get()`. The seeded `app-key` row is the first `avernet_application` insert → its surrogate `id = 1`. So DB-backed seed tests assert `id=1` / bot `app_id=1`. Registrar/HTTP-registered apps get later ids — assert `isinstance(id, int)` and `rec.id == issued.id`, never a hard-coded number.
- Bot domain's `app_id` is a *different* concept (the app a bot belongs to) but now also `int`, referencing `avernet_application.id` logically (no DB FK).
- `creator` / `modifier` are `str | None` — `None` is intentional (unauthenticated `/admin` has no caller). `config` is `dict[str, Any] | None` where `None` means "use empty config".

---

## File Structure

**Create:**
- `src/gateway/community/core/tenant/__init__.py` — package export (`TenantRow`).
- `src/gateway/community/core/tenant/_orm.py` — `TenantRow` ORM (`avernet_tenant`).
- `tests/unit/plugins/test_tenant_registry_db.py` — `avernet_tenant` create_all + round-trip smoke.

**Modify — app domain:**
- `src/gateway/community/spi/app/_ports.py` — `RegisteredApp`: drop `app_id`, add `id: int`.
- `src/gateway/community/core/app/_orm.py` — `AppRow` → `avernet_application` (new columns, no `app_id`).
- `src/gateway/community/core/app/_repository.py` — `store(...)` new fields, returns `int` (inserted id).
- `src/gateway/community/core/app/_registrar.py` — `register(...)` drops `app_id`; `IssuedApp` gains `id`; JWT `sub` = `app_name`.
- `src/gateway/community/spi/authn/_models.py` — `ThirdPartyApp.app_id: str → int`.
- `src/gateway/community/plugins/authn/app_token/_strategy.py` — `app_id=record.id`.
- `src/gateway/community/adapters/web/admin.py` — `AppRequest` drops `app_id`, adds optional `status/env/config`; response echoes `id`.
- `src/gateway/community/bootstrap/_authn.py` — seed `AppRow` (drop `app_id`), add `TenantRow` seed, docstring table names.

**Modify — access_key domain:**
- `src/gateway/community/core/access_key/_orm.py` — `AccessKeyRow` → `avernet_access_key_token` + audit columns.

**Modify — bot domain:**
- `src/gateway/community/core/bot/_orm.py` — `BotRow.app_id: str → int` (BigInteger).
- `src/gateway/community/spi/bot/_ports.py` — `RegisteredBot.app_id: str → int`.
- `src/gateway/community/spi/authn/_models.py` — `Bot.app_id: str → int` (same file as `ThirdPartyApp`).
- `src/gateway/community/bootstrap/_authn.py` — seed `BotRow(app_id=1)`.

**Modify — tests:** `test_app_ports.py`, `test_app_registry_db.py`, `test_app_token_strategy.py`, `test_app_registrar.py`, `test_admin_issuance.py`, `test_forward_signs_principal.py`, `test_authn_models.py`, `test_auth_runner.py`, `test_forward_seam.py`, `test_principal_signer.py`, `test_auth_strategy.py`, `test_access_key_registry_db.py`, `test_bot_token_strategy.py`, `test_bot_registry_db.py`.

---

## Task 1: `avernet_tenant` ORM model (new, isolated)

**Files:**
- Create: `src/gateway/community/core/tenant/_orm.py`
- Create: `src/gateway/community/core/tenant/__init__.py`
- Test: `tests/unit/plugins/test_tenant_registry_db.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/plugins/test_tenant_registry_db.py`:

```python
"""Smoke tests for the tenant master table (``avernet_tenant``) — ORM-only."""

from __future__ import annotations

from sqlalchemy import select

from gateway.community.bootstrap._authn import build_database
from gateway.community.core.tenant import TenantRow


async def test_tenant_row_table_creates_and_round_trips() -> None:
    db = build_database()
    with db.orm_session() as session:
        session.add(
            TenantRow(name="t-1", description="demo", owner="o-1", config={"k": "v"})
        )
    with db.orm_session() as session:
        row = session.scalar(select(TenantRow).where(TenantRow.name == "t-1"))
    assert row is not None
    assert row.owner == "o-1"
    assert row.config == {"k": "v"}
    assert row.gmt_create is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/plugins/test_tenant_registry_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gateway.community.core.tenant'`

- [ ] **Step 3: Create the ORM model**

Create `src/gateway/community/core/tenant/_orm.py`:

```python
"""ORM model for the tenant master table (``avernet_tenant``).

A standalone tenant registry. Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. ORM-only for now (no SPI/repository/HTTP) — add a registry
when a consumer appears.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.database import Base


class TenantRow(Base):  # type: ignore[misc]
    """A tenant master row (the ``avernet_tenant`` table)."""

    __tablename__ = "avernet_tenant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column()
    description: Mapped[str] = mapped_column(default="")
    owner: Mapped[str] = mapped_column(default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    creator: Mapped[str | None] = mapped_column(default=None)
    modifier: Mapped[str | None] = mapped_column(default=None)
    gmt_create: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )
```

- [ ] **Step 4: Create the package init**

Create `src/gateway/community/core/tenant/__init__.py`:

```python
"""Tenant domain — canonical master-table ORM (``avernet_tenant``).

Holds the ORM row (:class:`TenantRow`). ORM-only for now; the eventual
tenant registry SPI/repository will live here when a consumer appears.
"""

from ._orm import TenantRow

__all__ = [
    "TenantRow",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/plugins/test_tenant_registry_db.py -v`
Expected: PASS

- [ ] **Step 6: Lint + typecheck**

Run: `uv run ruff check . --fix && uv run ruff format . && uv run basedpyright src && uv run mypy src tests`
Expected: no errors (fix any import-ordering/type issues ruff/mypy reports)

- [ ] **Step 7: Commit**

```bash
git add src/gateway/community/core/tenant/__init__.py src/gateway/community/core/tenant/_orm.py tests/unit/plugins/test_tenant_registry_db.py
git commit -m "feat(gateway): add avernet_tenant master-table ORM"
```

---

## Task 2: Access-key domain — rename table + audit columns

The SPI (`RegisteredAccessKey`) and strategy are unchanged; audit columns stay DB-side. Only the ORM changes plus the test docstring.

**Files:**
- Modify: `src/gateway/community/core/access_key/_orm.py`
- Test: `tests/unit/plugins/test_access_key_registry_db.py`

- [ ] **Step 1: Rewrite the ORM model**

Replace the entire contents of `src/gateway/community/core/access_key/_orm.py` with:

```python
"""ORM model for the access-key registry (``avernet_access_key_token`` table) — canonical schema.

The canonical :class:`AccessKeyRepository` resolves a presented token to an
access-key row (surrogate ``id`` PK; ``token`` is the unique lookup key). Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. :meth:`AccessKeyRow.to_record` maps a row onto the SPI
:class:`~gateway.community.spi.access_key.RegisteredAccessKey` (core fields only;
audit columns stay DB-side, like ``bcs_bots``'s ``env``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.access_key import RegisteredAccessKey
from gateway.community.spi.database import Base


class AccessKeyRow(Base):  # type: ignore[misc]
    """An access key resolvable by token (the ``avernet_access_key_token`` table)."""

    __tablename__ = "avernet_access_key_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(unique=True)
    access_key: Mapped[str] = mapped_column()
    tenant: Mapped[str] = mapped_column()
    expire_at: Mapped[datetime] = mapped_column()
    creator: Mapped[str | None] = mapped_column(default=None)
    modifier: Mapped[str | None] = mapped_column(default=None)
    gmt_create: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )

    def to_record(self) -> RegisteredAccessKey:
        """Map this row onto the SPI :class:`RegisteredAccessKey` (core fields only)."""
        return RegisteredAccessKey(
            access_key=self.access_key,
            tenant=self.tenant,
            expire_at=self.expire_at,
        )
```

- [ ] **Step 2: Update the test docstring**

In `tests/unit/plugins/test_access_key_registry_db.py`, change the module docstring:

```python
"""DB-backed tests for ``AccessKeyRepository`` (seeded ``avernet_access_key_token`` table)."""
```

(No assertion changes — `RegisteredAccessKey` SPI is unchanged and the seeded row still resolves.)

- [ ] **Step 3: Run the access-key tests**

Run: `uv run pytest tests/unit/plugins/test_access_key_registry_db.py tests/unit/plugins/test_access_key_issuer.py tests/unit/plugins/test_access_key_token_strategy.py tests/integration/test_admin_issuance.py::test_issue_access_key_via_http -v`
Expected: PASS (issuer/strategy untouched; admin access-key path still green)

- [ ] **Step 4: Lint + typecheck**

Run: `uv run ruff check . --fix && uv run ruff format . && uv run basedpyright src && uv run mypy src tests`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add src/gateway/community/core/access_key/_orm.py tests/unit/plugins/test_access_key_registry_db.py
git commit -m "feat(gateway): rename access-key table to avernet_access_key_token + audit columns"
```

---

## Task 3: App domain migration (the big one)

Migrate the whole app domain in one commit so the suite stays internally consistent (changing `RegisteredApp` cascades to repository, strategy, admin, and tests simultaneously).

**Files:**
- Modify: `src/gateway/community/spi/app/_ports.py`
- Modify: `src/gateway/community/core/app/_orm.py`
- Modify: `src/gateway/community/core/app/_repository.py`
- Modify: `src/gateway/community/core/app/_registrar.py`
- Modify: `src/gateway/community/spi/authn/_models.py` (`ThirdPartyApp` only — `Bot` is Task 4)
- Modify: `src/gateway/community/plugins/authn/app_token/_strategy.py`
- Modify: `src/gateway/community/adapters/web/admin.py`
- Modify: `src/gateway/community/bootstrap/_authn.py` (`AppRow` seed + `TenantRow` seed + docstring)
- Tests: `test_app_ports.py`, `test_app_registry_db.py`, `test_app_token_strategy.py`, `test_app_registrar.py`, `test_admin_issuance.py`, `test_forward_signs_principal.py`, `test_authn_models.py` (app part), `test_auth_runner.py`, `test_forward_seam.py`, `test_principal_signer.py`, `test_auth_strategy.py` (app part)

- [ ] **Step 1: `RegisteredApp` SPI — drop `app_id`, add `id`**

Replace `src/gateway/community/spi/app/_ports.py` with:

```python
"""App-domain SPI — the ``AppRegistry`` contract.

A third-party app is resolved from a presented token by an :class:`AppRegistry`
implementation (the canonical ORM impl lives in ``core/app``). The authn
``app_token`` strategy depends on this interface, not on the impl.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RegisteredApp:
    """A third-party app a registry resolves a token to (registry record).

    ``id`` is the app's surrogate bigint id (``avernet_application.id``) — its
    stable identity. ``tenant`` is the tenant the app belongs to — the
    authoritative tenant for the resulting
    :class:`~gateway.community.spi.authn.AppPrincipal` (read from the app-token
    record, no longer cross-checked against a tenant header).
    """

    id: int
    app_name: str
    owners: str
    app_type: str
    tenant: str


class AppRegistry(Protocol):
    """Read-only third-party-app store keyed by token (resolved by the
    ``app_token`` strategy).

    ``find_app_by_token`` returns ``None`` for an unknown token (soft miss —
    not applicable), never raising on a bad token.
    """

    async def find_app_by_token(self, token: str) -> RegisteredApp | None: ...
```

- [ ] **Step 2: `AppRow` ORM — `avernet_application` schema**

Replace `src/gateway/community/core/app/_orm.py` with:

```python
"""ORM model for the third-party-app registry (``avernet_application`` table).

The canonical :class:`AppRepository` resolves a presented app token to an app
row (surrogate ``id`` PK; ``token`` is the unique lookup key). Registered on the shared
:class:`~gateway.community.spi.database.Base` so ``DataSourcePlugin.create_all()``
creates the table. :meth:`AppRow.to_record` maps a row onto the SPI
:class:`~gateway.community.spi.app.RegisteredApp` (core fields only; ``status`` /
``env`` / ``config`` / audit columns stay DB-side).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from gateway.community.spi.app import RegisteredApp
from gateway.community.spi.database import Base


class AppRow(Base):  # type: ignore[misc]
    """A third-party app resolvable by token (the ``avernet_application`` table)."""

    __tablename__ = "avernet_application"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_name: Mapped[str] = mapped_column(index=True)
    app_type: Mapped[str] = mapped_column()
    token: Mapped[str] = mapped_column(unique=True)
    owners: Mapped[str] = mapped_column()
    tenant: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="ACTIVE")
    env: Mapped[str] = mapped_column(default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    creator: Mapped[str | None] = mapped_column(default=None)
    modifier: Mapped[str | None] = mapped_column(default=None)
    gmt_create: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    gmt_modified: Mapped[datetime] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP")
    )

    def to_record(self) -> RegisteredApp:
        """Map this row onto the SPI :class:`RegisteredApp` (core fields only)."""
        return RegisteredApp(
            id=self.id,
            app_name=self.app_name,
            owners=self.owners,
            app_type=self.app_type,
            tenant=self.tenant,
        )
```

- [ ] **Step 3: `AppRepository.store` — new fields, return inserted id**

Replace `src/gateway/community/core/app/_repository.py` with:

```python
"""``AppRepository`` — canonical ORM third-party-app registry.

One ORM implementation behind the
:class:`~gateway.community.spi.app.AppRegistry` SPI port. Resolves a presented
app token via the ``avernet_application`` table through the
:data:`~gateway.community.spi.database.DataSourcePlugin`'s sync ``orm_session``,
mapping the row to the SPI :class:`~gateway.community.spi.app.RegisteredApp` via
:meth:`AppRow.to_record`. Flavor-neutral — the ``DataSourcePlugin`` (bare
in-memory SQLite or a sofa real DB) is injected by the composition root, so this
single body runs unchanged across runtimes (mirrors ``BotRepository`` /
``AccessKeyRepository``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from gateway.community.spi.app import AppRegistry, RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

from ._orm import AppRow


class AppRepository(AppRegistry):
    """App table access (read + write) for ``avernet_application``.

    Resolves a presented token (read) and persists a freshly registered app
    (write) — all DB touch lives here, never in the registrar.
    """

    Model: type[AppRow] = AppRow

    def __init__(self, db: DataSourcePlugin) -> None:
        self._db = db

    async def find_app_by_token(self, token: str) -> RegisteredApp | None:
        with self._db.orm_session() as session:
            row = session.scalar(select(self.Model).where(self.Model.token == token))
            return None if row is None else row.to_record()

    async def store(
        self,
        *,
        token: str,
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
        status: str = "ACTIVE",
        env: str = "",
        config: dict[str, Any] | None = None,
        creator: str | None = None,
        modifier: str | None = None,
    ) -> int:
        """Persist a freshly registered app; return its inserted surrogate ``id``.

        ``token`` is the app's JWT (the unique lookup key). Optional ``status`` /
        ``env`` / ``config`` default to ``ACTIVE`` / ``""`` / ``{}``; ``creator`` /
        ``modifier`` default to ``None`` (the unauthenticated admin has no caller).
        """
        with self._db.orm_session() as session:
            row = AppRow(
                token=token,
                app_name=app_name,
                app_type=app_type,
                owners=owners,
                tenant=tenant,
                status=status,
                env=env,
                config={} if config is None else config,
                creator=creator,
                modifier=modifier,
            )
            session.add(row)
            session.flush()
            return row.id
```

- [ ] **Step 4: `AppRegistrar.register` — drop `app_id`, add `id`; JWT `sub` = `app_name`**

Replace `src/gateway/community/core/app/_registrar.py` with:

```python
"""AppRegistrar — register an app: mint a JWT, delegate persistence, return the record.

The issued JWT is stored as the ``token`` of ``avernet_application`` via
:class:`AppRepository.store` (all DB touch lives in the repository); the authn
``app_token`` strategy resolves it by opaque DB lookup (``find_app_by_token``).
App tokens do NOT expire (the table has no ``expire_at``). Shares the gateway
HMAC key via :class:`PrincipalSigner.sign_token`. The app's stable identity is
its surrogate ``id`` (returned by ``store``); the JWT ``sub`` is the human-readable
``app_name`` (the surrogate ``id`` is not known until after insert).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gateway.community.spi.principal_signer import PrincipalSigner

from ._repository import AppRepository

_ISSUER = "gateway"


@dataclass(frozen=True)
class IssuedApp:
    """An app just registered: its record fields plus the freshly minted token."""

    id: int
    app_name: str
    owners: str
    app_type: str
    tenant: str
    token: str


class AppRegistrar:
    """Mint a JWT, persist it via the repository, return the record + token."""

    def __init__(
        self,
        repository: AppRepository,
        signer: PrincipalSigner,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._signer = signer
        self._clock = clock

    async def register(
        self,
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
        *,
        status: str = "ACTIVE",
        env: str = "",
        config: dict[str, Any] | None = None,
    ) -> IssuedApp:
        claims = {
            "iss": _ISSUER,
            "typ": "app",
            "sub": app_name,
            "tenant": tenant,
            "iat": int(self._clock()),
            "jti": uuid.uuid4().hex,
        }
        token = await self._signer.sign_token(claims)
        app_id = await self._repository.store(
            token=token,
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
            status=status,
            env=env,
            config=config,
        )
        return IssuedApp(
            id=app_id,
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
            token=token,
        )
```

- [ ] **Step 5: `ThirdPartyApp` model — `app_id: str → int`**

In `src/gateway/community/spi/authn/_models.py`, edit the `ThirdPartyApp` class (leave `Bot` for Task 4):

```python
class ThirdPartyApp(BaseModel):
    """A registered third-party developer application — the calling program itself."""

    model_config = ConfigDict(frozen=True)

    app_id: int  # the app's surrogate bigint id (avernet_application.id)
    app_name: str  # human-facing app name
    owners: str  # owning developer/org; resource-ownership fallback subject
    tenant: str  # tenant the app belongs to (from the resolved app record)
    app_type: str = "UNKNOWN"  # from the app-token record
```

- [ ] **Step 6: `AppTokenStrategy` — `app_id=record.id`**

In `src/gateway/community/plugins/authn/app_token/_strategy.py`, change the `AppPrincipal(...)` construction:

```python
        return AppPrincipal(
            tenant=record.tenant,
            app=ThirdPartyApp(
                app_id=record.id,
                app_name=record.app_name,
                owners=record.owners,
                tenant=record.tenant,
                app_type=record.app_type,
            ),
        )
```

- [ ] **Step 7: `/admin/apps` — drop `app_id`, add optional `status/env/config`, echo `id`**

Replace `src/gateway/community/adapters/web/admin.py` with:

```python
"""Admin endpoints — issue access keys and register apps.

NOT FOR PRODUCTION: these endpoints are **unauthenticated** (single-box / dev
convenience). A production deployment must gate them behind an admin credential
(not in scope for this workstream).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gateway.community.logger import get_logger

logger = get_logger("admin")

router = APIRouter(tags=["admin"])


class AccessKeyRequest(BaseModel):
    access_key: str
    tenant: str
    expire_at: datetime


class AppRequest(BaseModel):
    app_name: str
    owners: str
    app_type: str = "UNKNOWN"
    tenant: str
    status: str = "ACTIVE"
    env: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


def _error(status: int, subcode: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": status * 1000 + subcode, "message": message, "data": None},
    )


@router.post("/access-keys", status_code=201)
async def issue_access_key(payload: AccessKeyRequest, request: Request) -> JSONResponse:
    issuer = request.app.state.access_key_issuer
    try:
        issued = await issuer.issue(
            payload.access_key, payload.tenant, payload.expire_at
        )
    except Exception:
        logger.exception("access key issuance failed")
        return _error(500, 1, "access key issuance failed")
    return JSONResponse(
        status_code=201,
        content={
            "access_key": issued.access_key,
            "tenant": issued.tenant,
            "expire_at": issued.expire_at.isoformat(),
            "token": issued.token,
        },
    )


@router.post("/apps", status_code=201)
async def register_app(payload: AppRequest, request: Request) -> JSONResponse:
    registrar = request.app.state.app_registrar
    try:
        issued = await registrar.register(
            payload.app_name,
            payload.owners,
            payload.app_type,
            payload.tenant,
            status=payload.status,
            env=payload.env,
            config=payload.config,
        )
    except Exception:
        logger.exception("app registration failed")
        return _error(500, 1, "app registration failed")
    return JSONResponse(
        status_code=201,
        content={
            "id": issued.id,
            "app_name": issued.app_name,
            "owners": issued.owners,
            "app_type": issued.app_type,
            "tenant": issued.tenant,
            "status": payload.status,
            "env": payload.env,
            "token": issued.token,
        },
    )
```

- [ ] **Step 8: bootstrap seed — `AppRow` (drop `app_id`), add `TenantRow` seed, fix docstring**

In `src/gateway/community/bootstrap/_authn.py`:

(a) Add the import near the other core imports (after the `app` import):

```python
from gateway.community.core.app import AppRepository, AppRow
from gateway.community.core.tenant import TenantRow
```

(b) Update the `build_database` docstring — change the table list line to:

```python
    Importing the ``_orm`` modules (top of this file) registers the ``bcs_bots`` /
    ``avernet_access_key_token`` / ``avernet_application`` / ``avernet_tenant`` tables on
    ``Base.metadata`` so ``init_database``'s ``create_all`` creates them. The seeded
```

(c) In `_seed_authn`, replace the `AccessKeyRow` and `AppRow` blocks and add a `TenantRow` block, so the body becomes:

```python
    with db.orm_session() as session:
        if (
            session.scalar(select(BotRow).where(BotRow.session_token == "bot-key"))
            is None
        ):
            session.add(
                BotRow(
                    session_token="bot-key",
                    bot_uuid="bot-7",
                    env="dev",
                    created_by="owner-1",
                    agent_code="agent-1",
                    app_id="app-1",
                    tenant="t",
                )
            )
        if (
            session.scalar(select(TenantRow).where(TenantRow.name == "t")) is None
        ):
            session.add(TenantRow(name="t", description="demo", owner="org-1"))
        if (
            session.scalar(select(AccessKeyRow).where(AccessKeyRow.token == "ak-token"))
            is None
        ):
            session.add(
                AccessKeyRow(
                    token="ak-token",
                    access_key="ak-1",
                    tenant="t",
                    expire_at=datetime(2027, 1, 1, 0, 0, 0),
                )
            )
        if session.scalar(select(AppRow).where(AppRow.token == "app-key")) is None:
            session.add(
                AppRow(
                    token="app-key",
                    app_name="Demo App",
                    owners="org-1",
                    app_type="assistant",
                    tenant="t",
                )
            )
```

(Leave `BotRow(app_id="app-1", ...)` as-is here — the bot `app_id` becomes `int` in Task 4.)

- [ ] **Step 9: Update app-domain tests**

`tests/contracts/spi/test_app_ports.py` — replace the `test_record_has_required_fields` body:

```python
def test_record_has_required_fields() -> None:
    rec = RegisteredApp(
        id=1,
        app_name="Demo App",
        owners="org-1",
        app_type="assistant",
        tenant="t-1",
    )
    assert rec.id == 1
    assert rec.tenant == "t-1"
```

`tests/unit/plugins/test_app_registry_db.py` — change the docstring and the equality assertion:

```python
"""DB-backed tests for ``AppRepository`` (queries the seeded ``avernet_application`` table)."""
```
```python
    assert app == RegisteredApp(
        id=1,
        app_name="Demo App",
        owners="org-1",
        app_type="assistant",
        tenant="t",
    )
```

`tests/unit/plugins/test_app_token_strategy.py` — change the `_APP` construction and the three `app_id` assertions:

```python
    _APP = RegisteredApp(
        id=1,
        app_name="Demo App",
        owners="org-1",
        app_type="assistant",
        tenant="t",
    )
```
```python
    assert result.app.app_id == 1
```
(apply to all three occurrences: `test_dedicated_header_resolves`, `test_bearer_fallback_resolves`, `test_dedicated_header_wins_over_bearer`).

`tests/integration/test_admin_issuance.py` — replace `test_register_app_via_http`:

```python
async def test_register_app_via_http() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps",
            json={
                "app_name": "Http App",
                "owners": "org-1",
                "app_type": "assistant",
                "tenant": "t",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body["id"], int)
    assert body["app_name"] == "Http App"
    assert body["tenant"] == "t"
    assert body["status"] == "ACTIVE"
    token = body["token"]

    decoded = jwt.decode(token, _DEV_FALLBACK_KEY, algorithms=["HS256"])
    assert decoded["typ"] == "app"
    assert decoded["sub"] == "Http App"
    assert "exp" not in decoded

    rec = await AppRepository(build_database()).find_app_by_token(token)
    assert rec is not None
    assert rec.app_name == "Http App"
    assert rec.id == body["id"]
```

`tests/unit/plugins/test_app_registrar.py` — replace both test bodies:

```python
async def test_register_persists_row_and_returns_record(
    registrar: AppRegistrar,
) -> None:
    issued = await registrar.register("X App", "org-1", "assistant", "t1")
    assert isinstance(issued, IssuedApp)
    assert isinstance(issued.id, int) and issued.id >= 1
    assert issued.app_name == "X App"
    assert issued.owners == "org-1"
    assert issued.app_type == "assistant"
    assert issued.tenant == "t1"
    assert issued.token

    rec = await AppRepository(build_database()).find_app_by_token(issued.token)
    assert rec is not None
    assert rec.id == issued.id
    assert rec.app_name == "X App"
    assert rec.tenant == "t1"


async def test_register_token_has_expected_claims_and_no_exp(
    registrar: AppRegistrar,
) -> None:
    issued = await registrar.register("X App", "org-1", "assistant", "t1")
    decoded = jwt.decode(issued.token, "k", algorithms=["HS256"])
    assert decoded["typ"] == "app"
    assert decoded["sub"] == "X App"
    assert decoded["tenant"] == "t1"
    assert decoded["iat"] == _FIXED_NOW
    assert "exp" not in decoded
    assert isinstance(decoded["jti"], str) and decoded["jti"]
```

`tests/integration/test_forward_signs_principal.py` — change the `ThirdPartyApp(...)` inside `_StubAuthenticator.authenticate`:

```python
            return {
                PrincipalType.APP: AppPrincipal(
                    tenant="t",
                    app=ThirdPartyApp(app_id=1, app_name="A", owners="o", tenant="t"),
                )
            }
```

`tests/test_authn_models.py` — in `test_app_and_bot_principal_types`, change the `ThirdPartyApp` construction and its assertion (leave the `Bot(...)` part for Task 4):

```python
        app=ThirdPartyApp(
            app_id=1,
            app_name="Cid App",
            owners="org-1",
            tenant="t-app",
            app_type="assistant",
        ),
```
```python
    assert app.app.app_id == 1
```

`tests/test_auth_runner.py` — change `_app_p()`:

```python
def _app_p() -> AppPrincipal:
    return AppPrincipal(
        tenant="t",
        app=ThirdPartyApp(app_id=1, app_name="C App", owners="o", tenant="t"),
    )
```

`tests/test_forward_seam.py` — change `_app()`:

```python
def _app() -> AppPrincipal:
    return AppPrincipal(
        tenant="t",
        app=ThirdPartyApp(app_id=1, app_name="A", owners="o", tenant="t"),
    )
```

`tests/unit/plugins/test_principal_signer.py` — change `_app_principal()`:

```python
def _app_principal() -> AppPrincipal:
    return AppPrincipal(
        tenant="t",
        app=ThirdPartyApp(app_id=1, app_name="Demo", owners="org-1", tenant="t"),
    )
```

`tests/contracts/spi/test_auth_strategy.py` — change `_FakeAppRegistry._APP` only (leave `_FakeBotRegistry._BOT` for Task 4):

```python
    _APP = RegisteredApp(
        id=1,
        app_name="Demo App",
        owners="org-1",
        app_type="assistant",
        tenant="t",
    )
```

- [ ] **Step 10: Run all touched app-domain tests**

Run: `uv run pytest tests/unit/plugins/test_app_ports.py tests/unit/plugins/test_app_registry_db.py tests/unit/plugins/test_app_token_strategy.py tests/unit/plugins/test_app_registrar.py tests/unit/plugins/test_principal_signer.py tests/contracts/spi/test_auth_strategy.py tests/integration/test_admin_issuance.py tests/integration/test_forward_signs_principal.py tests/test_authn_models.py tests/test_auth_runner.py tests/test_forward_seam.py tests/integration/test_identity_pipeline.py -v`
Expected: PASS (the `Bot(...)` constructions in `test_authn_models.py` / `test_auth_strategy.py` still use `str` `app_id` — intentionally left for Task 4)

- [ ] **Step 11: Lint + typecheck**

Run: `uv run ruff check . --fix && uv run ruff format . && uv run basedpyright src && uv run mypy src tests`
Expected: no errors. Note: `Bot.app_id` is still `str` until Task 4, so bot tests referencing `app_id="..."` are consistent — do not change them here.

- [ ] **Step 12: Commit**

```bash
git add src/gateway/community/spi/app/_ports.py src/gateway/community/core/app/ src/gateway/community/spi/authn/_models.py src/gateway/community/plugins/authn/app_token/_strategy.py src/gateway/community/adapters/web/admin.py src/gateway/community/bootstrap/_authn.py tests/contracts/spi/test_app_ports.py tests/contracts/spi/test_auth_strategy.py tests/unit/plugins/test_app_registrar.py tests/unit/plugins/test_app_registry_db.py tests/unit/plugins/test_app_token_strategy.py tests/unit/plugins/test_principal_signer.py tests/integration/test_admin_issuance.py tests/integration/test_forward_signs_principal.py tests/test_authn_models.py tests/test_auth_runner.py tests/test_forward_seam.py
git commit -m "feat(gateway): migrate app registry to avernet_application (surrogate id, no app_id column)"
```

---

## Task 4: Bot domain — `app_id` str → int

Align the bot's "app it belongs to" reference to `avernet_application.id` (int).

**Files:**
- Modify: `src/gateway/community/core/bot/_orm.py`
- Modify: `src/gateway/community/spi/bot/_ports.py`
- Modify: `src/gateway/community/spi/authn/_models.py` (`Bot` only)
- Modify: `src/gateway/community/bootstrap/_authn.py` (`BotRow` seed)
- Tests: `test_bot_token_strategy.py`, `test_bot_registry_db.py`, `test_authn_models.py` (bot part), `test_auth_strategy.py` (bot part)

- [ ] **Step 1: `BotRow.app_id` column → int**

In `src/gateway/community/core/bot/_orm.py`, change the `app_id` column line:

```python
from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column
```
```python
    app_id: Mapped[int] = mapped_column(BigInteger)
```

(Add the `BigInteger` import to the existing `sqlalchemy` import line. The `bcs_bots` table resides in gateway; this column logically references `avernet_application.id` with no DB-level FK.)

- [ ] **Step 2: `RegisteredBot.app_id` → int**

In `src/gateway/community/spi/bot/_ports.py`, change the field and its docstring:

```python
@dataclass(frozen=True)
class RegisteredBot:
    """A bot a registry resolves a session token to (registry record).

    ``owner_id`` is the bot's creator/owner (resource-ownership anchor, from the
    DB ``created_by`` column); ``app_id`` is the app the bot belongs to (the
    surrogate ``id`` of ``avernet_application``); ``tenant`` is its tenant.
    ``env`` / ``agent_code`` are DB-side only.
    """

    bot_uuid: str
    owner_id: str
    app_id: int
    tenant: str
```

- [ ] **Step 3: `Bot.app_id` → int**

In `src/gateway/community/spi/authn/_models.py`, change the `Bot` class field:

```python
    app_id: int  # the app the bot belongs to (avernet_application.id)
```

(The `bot_token` strategy already does `app_id=bot.app_id` — no code change there; the type flows through.)

- [ ] **Step 4: bootstrap `BotRow` seed → `app_id=1`**

In `src/gateway/community/bootstrap/_authn.py`, inside `_seed_authn`, change the `BotRow(...)` construction:

```python
                BotRow(
                    session_token="bot-key",
                    bot_uuid="bot-7",
                    env="dev",
                    created_by="owner-1",
                    agent_code="agent-1",
                    app_id=1,
                    tenant="t",
                )
```

(`1` references the seeded app's surrogate `id=1` — both seeded in the same `build_database()` pass.)

- [ ] **Step 5: Update bot-domain tests**

`tests/unit/plugins/test_bot_token_strategy.py` — change `_BOT` and the assertion:

```python
    _BOT = RegisteredBot(
        bot_uuid="bot-7", owner_id="owner-1", app_id=1, tenant="t"
    )
```
```python
    assert result.bot.app_id == 1
```

`tests/unit/plugins/test_bot_registry_db.py` — change the equality assertion:

```python
    assert bot == RegisteredBot(
        bot_uuid="bot-7", owner_id="owner-1", app_id=1, tenant="t"
    )
```

`tests/test_authn_models.py` — in `test_app_and_bot_principal_types`, change the `Bot(...)` construction (the `app_id="app-x"` line):

```python
        bot=Bot(
            bot_uuid="b-1",
            owner_id="org-1",
            token="tok",
            app_id=1,
            tenant="t-bot",
        ),
```

`tests/contracts/spi/test_auth_strategy.py` — change `_FakeBotRegistry._BOT`:

```python
    _BOT = RegisteredBot(
        bot_uuid="bot-7", owner_id="owner-1", app_id=1, tenant="t"
    )
```

- [ ] **Step 6: Run bot + identity tests**

Run: `uv run pytest tests/unit/plugins/test_bot_token_strategy.py tests/unit/plugins/test_bot_registry_db.py tests/contracts/spi/test_auth_strategy.py tests/test_authn_models.py tests/integration/test_identity_pipeline.py -v`
Expected: PASS

- [ ] **Step 7: Lint + typecheck**

Run: `uv run ruff check . --fix && uv run ruff format . && uv run basedpyright src && uv run mypy src tests`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add src/gateway/community/core/bot/_orm.py src/gateway/community/spi/bot/_ports.py src/gateway/community/spi/authn/_models.py src/gateway/community/bootstrap/_authn.py tests/unit/plugins/test_bot_token_strategy.py tests/unit/plugins/test_bot_registry_db.py tests/contracts/spi/test_auth_strategy.py tests/test_authn_models.py
git commit -m "feat(gateway): align bot-domain app_id to int (avernet_application.id)"
```

---

## Task 5: Full gate — lint, typecheck, whole suite

Catch any straggler references and confirm green across the board.

- [ ] **Step 1: Search for any remaining `app_id="..."` (str) in app/bot context**

Run: `grep -rnE "app_id=\"[a-zA-Z]" src tests --include="*.py" | grep -v __pycache__`
Expected: no matches (any str-literal `app_id` is a regression). Remaining bot-seed/string references should all be gone.

- [ ] **Step 2: Lint + format**

Run: `uv run ruff check . --fix && uv run ruff format .`
Expected: clean (no leftover changes)

- [ ] **Step 3: Typecheck**

Run: `uv run basedpyright src && uv run mypy src tests`
Expected: clean. Common issues to fix: `Mapped[dict[str, Any]]` JSON columns, `str | None` audit columns, the ` BigInteger` import in `core/bot/_orm.py`.

- [ ] **Step 4: Full test suite**

Run: `uv run pytest`
Expected: all green. If architecture/contract tests fail on the renamed tables or the `app_id` type, re-check Task 3/4 test edits.

- [ ] **Step 5: Commit any fixes (if any)**

```bash
git add -A
git commit -m "chore(gateway): fix lint/type fallout from canonical schema migration"
```
(Skip if nothing changed.)

---

## Self-Review notes (done by plan author)

- **Spec coverage:** every spec section maps to a task — §2.1 → Task 3, §2.2 → Task 1, §2.3 → Task 2, §3 (app/bot SPI) → Tasks 3/4, §4 (admin/bootstrap/strategy) → Tasks 3/4, §5 JWT/principal flow → Task 3 (`sub=app_name`, `app_id=record.id`), §6 tests → Tasks 1–4, §9 risks (downstream contract str→int, no-FK) are inherent and documented.
- **Type consistency:** `RegisteredApp.id: int` (Task 3) consumed as `record.id` → `ThirdPartyApp.app_id: int` (Task 3) → asserted as `== 1` in tests. `RegisteredBot.app_id: int` (Task 4) → `Bot.app_id: int` (Task 4) → seeded `app_id=1`. `AppRepository.store(...) -> int` (Task 3) consumed by `AppRegistrar` as `app_id = await store(...)` → `IssuedApp.id: int`.
- **No placeholders:** every code step shows the actual code or exact old→new edit.
- **Green-commit ordering:** Task 1 (isolated) → Task 2 (isolated, SPI unchanged) → Task 3 (app only; bot still `str`, consistent) → Task 4 (bot only). Each task leaves the full suite green.
