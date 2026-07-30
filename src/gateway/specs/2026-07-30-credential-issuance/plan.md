# 凭证签发与注册（access_key / app）— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 网关能签发 access_key（JWT+入库+返回记录）与注册 app，暴露为 core 服务方法 + `/admin/*` HTTP 接口。

**Architecture:** 复用 `PrincipalSigner`（加通用 `sign_token(claims)`）签凭证 JWT；`AccessKeyIssuer`/`AppRegistrar`（core）写 `baas_access_key_token`/`avernet_apps` 行；`adapters/web/admin.py` APIRouter 暴露无鉴权 `/admin` 接口（not-for-prod）。

**Tech Stack:** Python 3.12+ / FastAPI APIRouter / SQLAlchemy ORM（`orm_session` 自动 commit）/ pyjwt / pydantic / pytest(asyncio_mode=auto)。

**Spec:** `src/gateway/specs/2026-07-30-credential-issuance/spec.md`

---

## File Structure

- Modify `src/gateway/community/spi/principal_signer/_ports.py` — Protocol 加 `sign_token`
- Modify `src/gateway/community/plugins/principal_signer/bare/_plugin.py` — `BarePrincipalSigner.sign_token` + `sign` 重构委托
- Modify `tests/unit/plugins/test_principal_signer.py` — `sign_token` 测试
- Create `src/gateway/community/core/access_key/_issuer.py` — `AccessKeyIssuer` + `IssuedAccessKey`
- Modify `src/gateway/community/core/access_key/__init__.py` — 导出 `AccessKeyIssuer`/`IssuedAccessKey`
- Create `tests/unit/plugins/test_access_key_issuer.py`
- Create `src/gateway/community/core/app/_registrar.py` — `AppRegistrar` + `IssuedApp`
- Modify `src/gateway/community/core/app/__init__.py` — 导出 `AppRegistrar`/`IssuedApp`
- Create `tests/unit/plugins/test_app_registrar.py`
- Create `src/gateway/community/bootstrap/_credential_issuance.py` — `build_access_key_issuer` / `build_app_registrar`
- Modify `src/gateway/community/bootstrap/__init__.py` — 导出两个工厂
- Modify `src/gateway/community/adapters/web/app.py` — 构造并挂 `app.state.access_key_issuer`/`app_registrar`；`include_router(admin, prefix="/admin")`
- Create `src/gateway/community/adapters/web/admin.py` — `POST /access-keys`、`POST /apps`
- Create `tests/integration/test_admin_issuance.py` — HTTP 签发/注册 + 422

所有命令在 `src/gateway` 下用 `.venv/bin/python -m pytest ...` 与 `.venv/bin/python -m ruff ...`。包路径嵌套一层 `src`：实际物理路径是 `src/gateway/src/gateway/community/...`，cwd 为 `src/gateway` 时相对路径正确。

---

### Task 1: `PrincipalSigner.sign_token`（SPI + bare 实现）

**Files:**
- Modify: `src/gateway/community/spi/principal_signer/_ports.py`
- Modify: `src/gateway/community/plugins/principal_signer/bare/_plugin.py`
- Test: `tests/unit/plugins/test_principal_signer.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/plugins/test_principal_signer.py` 末尾追加：

```python
async def test_sign_token_signs_arbitrary_claims_with_kid_header() -> None:
    signer = BarePrincipalSigner(_cfg("k"), clock=lambda: _FIXED_NOW)
    token = await signer.sign_token(
        {"iss": "gateway", "typ": "access_key", "sub": "ak-1", "tenant": "t", "jti": "j1"}
    )
    decoded = jwt.decode(token, "k", algorithms=["HS256"])
    assert decoded["typ"] == "access_key"
    assert decoded["sub"] == "ak-1"
    assert decoded["tenant"] == "t"
    assert decoded["jti"] == "j1"
    assert jwt.get_unverified_header(token)["kid"] == "bare"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/plugins/test_principal_signer.py::test_sign_token_signs_arbitrary_claims_with_kid_header -q`
Expected: FAIL（`AttributeError: 'BarePrincipalSigner' object has no attribute 'sign_token'`）

- [ ] **Step 3: SPI 加方法**

在 `src/gateway/community/spi/principal_signer/_ports.py` 的 `PrincipalSigner` Protocol 内，紧接 `sign` 方法之后加：

```python
    async def sign_token(self, claims: Mapping[str, object]) -> str:
        """Sign an arbitrary claim set as an HS256 JWT (kid in the JOSE header).

        The principal-forwarding ``sign`` delegates here; credential issuance
        (access_key / app tokens) also uses it so all gateway-issued JWTs share
        one key.
        """
        ...
```

- [ ] **Step 4: bare 实现 + 重构 `sign` 委托**

在 `src/gateway/community/plugins/principal_signer/bare/_plugin.py` 的 `BarePrincipalSigner` 内，把现有 `sign` 方法替换为下面两个方法（`sign` 构造 principal claims 后委托 `sign_token`，新增 `sign_token`）：

```python
    async def sign(
        self, principals: Mapping[PrincipalType, Principal], *, audience: str
    ) -> str:
        now = int(self._clock())
        claims = {
            "iss": self._cfg.issuer,
            "aud": audience,
            "iat": now,
            "exp": now + self._cfg.ttl_seconds,
            "principals": [p.model_dump(mode="json") for p in principals.values()],
        }
        return await self.sign_token(claims)

    async def sign_token(self, claims: Mapping[str, object]) -> str:
        return jwt.encode(
            claims,
            self._cfg.signing_key,
            algorithm="HS256",
            headers={"kid": self._cfg.kid},
        )
```

- [ ] **Step 5: 跑 signer 全量测试（含原 `sign` 回归）确认通过**

Run: `.venv/bin/python -m pytest tests/unit/plugins/test_principal_signer.py -q`
Expected: PASS（8 passed：原 7 + 新 1）

- [ ] **Step 6: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/spi/principal_signer src/gateway/community/plugins/principal_signer/bare tests/unit/plugins/test_principal_signer.py
.venv/bin/python -m ruff check .
git add src/gateway/community/spi/principal_signer src/gateway/community/plugins/principal_signer tests/unit/plugins/test_principal_signer.py
git commit -m "feat(principal_signer): add generic sign_token for credential issuance"
```

---

### Task 2: `AccessKeyIssuer`

**Files:**
- Create: `src/gateway/community/core/access_key/_issuer.py`
- Modify: `src/gateway/community/core/access_key/__init__.py`
- Test: `tests/unit/plugins/test_access_key_issuer.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/plugins/test_access_key_issuer.py`：

```python
"""Unit tests for AccessKeyIssuer (mints JWT, persists row, returns record)."""

from __future__ import annotations

from datetime import datetime

import jwt
import pytest

from gateway.community.bootstrap._authn import build_database
from gateway.community.core.access_key import AccessKeyIssuer, AccessKeyRepository
from gateway.community.core.access_key._issuer import IssuedAccessKey
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)

_EXPIRE = datetime(2027, 1, 1, 0, 0, 0)
_FIXED_NOW = 1_700_000_000


@pytest.fixture
def issuer() -> AccessKeyIssuer:
    return AccessKeyIssuer(
        build_database(),
        BarePrincipalSigner(PrincipalSignerConfig(signing_key="k")),
        clock=lambda: _FIXED_NOW,
    )


async def test_issue_persists_row_and_returns_record() -> None:
    issued = await issuer.issue("ak-new", "t1", _EXPIRE)
    assert isinstance(issued, IssuedAccessKey)
    assert issued.access_key_id == "ak-new"
    assert issued.tenant == "t1"
    assert issued.expire_at == _EXPIRE
    assert issued.token

    rec = await AccessKeyRepository(build_database()).find_access_key_by_token(issued.token)
    assert rec is not None
    assert rec.access_key_id == "ak-new"
    assert rec.tenant == "t1"
    assert rec.expire_at == _EXPIRE


async def test_issue_token_has_expected_claims() -> None:
    issued = await issuer.issue("ak-new", "t1", _EXPIRE)
    decoded = jwt.decode(issued.token, "k", algorithms=["HS256"])
    assert decoded["typ"] == "access_key"
    assert decoded["sub"] == "ak-new"
    assert decoded["tenant"] == "t1"
    assert decoded["iat"] == _FIXED_NOW
    assert decoded["exp"] == int(_EXPIRE.timestamp())
    assert isinstance(decoded["jti"], str) and decoded["jti"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/plugins/test_access_key_issuer.py -q`
Expected: FAIL（`ModuleNotFoundError: gateway.community.core.access_key._issuer` 或导入错）

- [ ] **Step 3: 写 issuer**

创建 `src/gateway/community/core/access_key/_issuer.py`：

```python
"""AccessKeyIssuer — issue an access key: mint a JWT token, persist the row, return the record.

The issued JWT is stored as the ``token`` PK of ``baas_access_key_token``; the
authn ``access_key_token`` strategy still resolves it by opaque DB lookup
(``find_access_key_by_token``). The JWT's claims are for the caller / downstream
to self-verify; the gateway only matches the string. Shares the gateway HMAC
key via :class:`PrincipalSigner.sign_token`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from gateway.community.spi.database import DataSourcePlugin
from gateway.community.spi.principal_signer import PrincipalSigner

from ._orm import AccessKeyRow

_ISSUER = "gateway"


@dataclass(frozen=True)
class IssuedAccessKey:
    """An access key just issued: its record fields plus the freshly minted token."""

    access_key_id: str
    tenant: str
    expire_at: datetime
    token: str


class AccessKeyIssuer:
    """Mint a JWT, write the ``baas_access_key_token`` row, return the record + token."""

    def __init__(
        self,
        db: DataSourcePlugin,
        signer: PrincipalSigner,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._signer = signer
        self._clock = clock

    async def issue(
        self, access_key_id: str, tenant: str, expire_at: datetime
    ) -> IssuedAccessKey:
        claims = {
            "iss": _ISSUER,
            "typ": "access_key",
            "sub": access_key_id,
            "tenant": tenant,
            "iat": int(self._clock()),
            "exp": int(expire_at.timestamp()),
            "jti": uuid.uuid4().hex,
        }
        token = await self._signer.sign_token(claims)
        with self._db.orm_session() as session:
            session.add(
                AccessKeyRow(
                    token=token,
                    access_key_id=access_key_id,
                    tenant=tenant,
                    expire_at=expire_at,
                )
            )
        return IssuedAccessKey(
            access_key_id=access_key_id,
            tenant=tenant,
            expire_at=expire_at,
            token=token,
        )
```

- [ ] **Step 4: 导出**

修改 `src/gateway/community/core/access_key/__init__.py`，在现有 import 与 `__all__` 中加入 `_issuer` 的导出。最终内容：

```python
"""Access-key domain — canonical data-access (``AccessKeyRegistry`` SPI impl).

Holds the ORM row (:class:`AccessKeyRow`), the canonical
:class:`AccessKeyRepository` impl, and :class:`AccessKeyIssuer` (mints + persists
access keys). The :class:`~gateway.community.spi.access_key.AccessKeyRegistry`
contract lives in the access-key SPI. The authn ``access_key_token`` strategy
depends on the SPI, not this module.
"""

from ._issuer import AccessKeyIssuer, IssuedAccessKey
from ._orm import AccessKeyRow
from ._repository import AccessKeyRepository

__all__ = [
    "AccessKeyIssuer",
    "AccessKeyRepository",
    "AccessKeyRow",
    "IssuedAccessKey",
]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/unit/plugins/test_access_key_issuer.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/core/access_key tests/unit/plugins/test_access_key_issuer.py
.venv/bin/python -m ruff check .
git add src/gateway/community/core/access_key tests/unit/plugins/test_access_key_issuer.py
git commit -m "feat(access_key): add AccessKeyIssuer (JWT + DB write)"
```

---

### Task 3: `AppRegistrar`

**Files:**
- Create: `src/gateway/community/core/app/_registrar.py`
- Modify: `src/gateway/community/core/app/__init__.py`
- Test: `tests/unit/plugins/test_app_registrar.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/plugins/test_app_registrar.py`：

```python
"""Unit tests for AppRegistrar (mints JWT, persists row, returns record)."""

from __future__ import annotations

import jwt
import pytest

from gateway.community.bootstrap._authn import build_database
from gateway.community.core.app import AppRegistrar, AppRepository
from gateway.community.core.app._registrar import IssuedApp
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)

_FIXED_NOW = 1_700_000_000


@pytest.fixture
def registrar() -> AppRegistrar:
    return AppRegistrar(
        build_database(),
        BarePrincipalSigner(PrincipalSignerConfig(signing_key="k")),
        clock=lambda: _FIXED_NOW,
    )


async def test_register_persists_row_and_returns_record() -> None:
    issued = await registrar.register("app-x", "X App", "org-1", "assistant", "t1")
    assert isinstance(issued, IssuedApp)
    assert issued.app_id == "app-x"
    assert issued.app_name == "X App"
    assert issued.owners == "org-1"
    assert issued.app_type == "assistant"
    assert issued.tenant == "t1"
    assert issued.token

    rec = await AppRepository(build_database()).find_app_by_token(issued.token)
    assert rec is not None
    assert rec.app_id == "app-x"
    assert rec.tenant == "t1"


async def test_register_token_has_expected_claims_and_no_exp() -> None:
    issued = await registrar.register("app-x", "X App", "org-1", "assistant", "t1")
    decoded = jwt.decode(issued.token, "k", algorithms=["HS256"])
    assert decoded["typ"] == "app"
    assert decoded["sub"] == "app-x"
    assert decoded["tenant"] == "t1"
    assert decoded["iat"] == _FIXED_NOW
    assert "exp" not in decoded
    assert isinstance(decoded["jti"], str) and decoded["jti"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/plugins/test_app_registrar.py -q`
Expected: FAIL（`ModuleNotFoundError: gateway.community.core.app._registrar` 或导入错）

- [ ] **Step 3: 写 registrar**

创建 `src/gateway/community/core/app/_registrar.py`：

```python
"""AppRegistrar — register an app: mint a JWT token, persist the row, return the record.

The issued JWT is stored as the ``token`` PK of ``avernet_apps``; the authn
``app_token`` strategy still resolves it by opaque DB lookup
(``find_app_by_token``). App tokens do NOT expire (the table has no
``expire_at``). Shares the gateway HMAC key via :class:`PrincipalSigner.sign_token`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from gateway.community.spi.database import DataSourcePlugin
from gateway.community.spi.principal_signer import PrincipalSigner

from ._orm import AppRow

_ISSUER = "gateway"


@dataclass(frozen=True)
class IssuedApp:
    """An app just registered: its record fields plus the freshly minted token."""

    app_id: str
    app_name: str
    owners: str
    app_type: str
    tenant: str
    token: str


class AppRegistrar:
    """Mint a JWT, write the ``avernet_apps`` row, return the record + token."""

    def __init__(
        self,
        db: DataSourcePlugin,
        signer: PrincipalSigner,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._signer = signer
        self._clock = clock

    async def register(
        self,
        app_id: str,
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
    ) -> IssuedApp:
        claims = {
            "iss": _ISSUER,
            "typ": "app",
            "sub": app_id,
            "tenant": tenant,
            "iat": int(self._clock()),
            "jti": uuid.uuid4().hex,
        }
        token = await self._signer.sign_token(claims)
        with self._db.orm_session() as session:
            session.add(
                AppRow(
                    token=token,
                    app_id=app_id,
                    app_name=app_name,
                    owners=owners,
                    app_type=app_type,
                    tenant=tenant,
                )
            )
        return IssuedApp(
            app_id=app_id,
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
            token=token,
        )
```

- [ ] **Step 4: 导出**

修改 `src/gateway/community/core/app/__init__.py`，最终内容：

```python
"""App domain — canonical data-access (``AppRegistry`` SPI impl) + registration.

Holds the ORM row (:class:`AppRow`), the canonical :class:`AppRepository` impl,
and :class:`AppRegistrar` (registers + persists apps). The
:class:`~gateway.community.spi.app.AppRegistry` contract lives in the app SPI.
The authn ``app_token`` strategy depends on the SPI, not this module.
"""

from ._orm import AppRow
from ._registrar import AppRegistrar, IssuedApp
from ._repository import AppRepository

__all__ = [
    "AppRegistrar",
    "AppRepository",
    "AppRow",
    "IssuedApp",
]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/unit/plugins/test_app_registrar.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/core/app tests/unit/plugins/test_app_registrar.py
.venv/bin/python -m ruff check .
git add src/gateway/community/core/app tests/unit/plugins/test_app_registrar.py
git commit -m "feat(app): add AppRegistrar (JWT + DB write)"
```

---

### Task 4: bootstrap 工厂

**Files:**
- Create: `src/gateway/community/bootstrap/_credential_issuance.py`
- Modify: `src/gateway/community/bootstrap/__init__.py`

- [ ] **Step 1: 写工厂模块**

创建 `src/gateway/community/bootstrap/_credential_issuance.py`：

```python
"""Composition root for credential issuance (access_key / app registration).

Builds :class:`AccessKeyIssuer` / :class:`AppRegistrar` wired to the same
``DataSourcePlugin`` and ``PrincipalSigner`` the rest of the gateway uses. The
adapters receive them via ``app.state``.
"""

from __future__ import annotations

from gateway.community.core.access_key import AccessKeyIssuer
from gateway.community.core.app import AppRegistrar
from gateway.community.spi.database import DataSourcePlugin
from gateway.community.spi.principal_signer import PrincipalSigner


def build_access_key_issuer(
    db: DataSourcePlugin, signer: PrincipalSigner
) -> AccessKeyIssuer:
    """Build the AccessKeyIssuer (shares the gateway signer + DB)."""
    return AccessKeyIssuer(db, signer)


def build_app_registrar(
    db: DataSourcePlugin, signer: PrincipalSigner
) -> AppRegistrar:
    """Build the AppRegistrar (shares the gateway signer + DB)."""
    return AppRegistrar(db, signer)
```

- [ ] **Step 2: 从 bootstrap 导出**

修改 `src/gateway/community/bootstrap/__init__.py`，加入新 import 与 `__all__` 条目。最终内容：

```python
"""Bootstrap — dependency injection and application lifecycle.

The composition root: wires concrete plugins and services into the app. Adapters
import the built objects from here (e.g. the ``Authenticator``) rather than
constructing plugins themselves.
"""

from ._authn import Authenticator, build_authenticator, build_database
from ._credential_issuance import build_access_key_issuer, build_app_registrar
from ._forwarding import Forwarding, build_forwarding
from ._principal_signer import build_principal_signer

__all__ = [
    "Authenticator",
    "Forwarding",
    "build_access_key_issuer",
    "build_app_registrar",
    "build_authenticator",
    "build_database",
    "build_forwarding",
    "build_principal_signer",
]
```

- [ ] **Step 3: 校验可导入 + 可构造**

Run:
```bash
.venv/bin/python -c "
from gateway.community.bootstrap import build_database, build_principal_signer, build_access_key_issuer, build_app_registrar
db = build_database()
signer = build_principal_signer()
assert build_access_key_issuer(db, signer).__class__.__name__ == 'AccessKeyIssuer'
assert build_app_registrar(db, signer).__class__.__name__ == 'AppRegistrar'
print('ok')
"
```
Expected: 打印 `ok`，无异常。

- [ ] **Step 4: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/bootstrap
.venv/bin/python -m ruff check .
git add src/gateway/community/bootstrap
git commit -m "feat(bootstrap): factories for access_key issuer + app registrar"
```

---

### Task 5: admin 路由 + app.py 接线

**Files:**
- Create: `src/gateway/community/adapters/web/admin.py`
- Modify: `src/gateway/community/adapters/web/app.py`

- [ ] **Step 1: 写 admin 路由**

创建 `src/gateway/community/adapters/web/admin.py`：

```python
"""Admin endpoints — issue access keys and register apps.

NOT FOR PRODUCTION: these endpoints are **unauthenticated** (single-box / dev
convenience). A production deployment must gate them behind an admin credential
(not in scope for this workstream).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(tags=["admin"])


class AccessKeyRequest(BaseModel):
    access_key_id: str
    tenant: str
    expire_at: datetime


class AppRequest(BaseModel):
    app_id: str
    app_name: str
    owners: str
    app_type: str = "UNKNOWN"
    tenant: str


def _error(status: int, subcode: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": status * 1000 + subcode, "message": message, "data": None},
    )


@router.post("/access-keys", status_code=201)
async def issue_access_key(
    payload: AccessKeyRequest, request: Request
) -> JSONResponse:
    issuer = request.app.state.access_key_issuer
    try:
        issued = await issuer.issue(
            payload.access_key_id, payload.tenant, payload.expire_at
        )
    except Exception:
        return _error(500, 1, "access key issuance failed")
    return JSONResponse(
        status_code=201,
        content={
            "access_key_id": issued.access_key_id,
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
            payload.app_id,
            payload.app_name,
            payload.owners,
            payload.app_type,
            payload.tenant,
        )
    except Exception:
        return _error(500, 1, "app registration failed")
    return JSONResponse(
        status_code=201,
        content={
            "app_id": issued.app_id,
            "app_name": issued.app_name,
            "owners": issued.owners,
            "app_type": issued.app_type,
            "tenant": issued.tenant,
            "token": issued.token,
        },
    )
```

- [ ] **Step 2: app.py 接线（构造 + 挂 state + include_router）**

修改 `src/gateway/community/adapters/web/app.py`：

2a. 把 `create_app` 内的 lazy bootstrap 导入块改为（加两个工厂）：

```python
    from gateway.community.bootstrap import (
        build_access_key_issuer,
        build_app_registrar,
        build_authenticator,
        build_database,
        build_forwarding,
        build_principal_signer,
    )

    db = build_database()
    authenticator = build_authenticator(db)
    forwarding = build_forwarding()
    principal_signer = build_principal_signer()
    access_key_issuer = build_access_key_issuer(db, principal_signer)
    app_registrar = build_app_registrar(db, principal_signer)
```

2b. 在文件顶部 import 区，加（紧跟已有的 `from gateway.community.adapters.web._forward import ...` 之后）：

```python
from gateway.community.adapters.web.admin import router as admin_router
```

2c. 在设置 `app.state` 的段落，追加两行，使该段变为：

```python
    # Hand the composed subsystems to the delivery layer via app.state.
    app.state.authenticator = authenticator
    app.state.domain_map = forwarding.domain_map
    app.state.forwarder = forwarding.forwarder
    app.state.principal_signer = principal_signer
    app.state.access_key_issuer = access_key_issuer
    app.state.app_registrar = app_registrar
```

2d. 在注册 catch-all 路由**之前**，挂载 admin 路由（在 `app.add_api_route("/{full_path:path}", ...)` 那行之前插入）：

```python
    # Admin endpoints (issuance/registration) — explicit routes, so they win
    # over the catch-all forward. Unauthenticated (single-box/dev only).
    app.include_router(admin_router, prefix="/admin")
```

- [ ] **Step 3: 校验应用可建 + 路由已挂**

Run:
```bash
.venv/bin/python -c "
from gateway.community.adapters.web.app import create_app
app = create_app()
assert app.state.access_key_issuer is not None
assert app.state.app_registrar is not None
paths = {r.path for r in app.routes}
assert '/admin/access-keys' in paths
assert '/admin/apps' in paths
print('ok')
"
```
Expected: 打印 `ok`，无异常。

- [ ] **Step 4: ruff + 提交**

```bash
.venv/bin/python -m ruff format src/gateway/community/adapters/web
.venv/bin/python -m ruff check .
git add src/gateway/community/adapters/web
git commit -m "feat(web): admin endpoints for access_key issuance + app registration"
```

---

### Task 6: HTTP 集成测试

**Files:**
- Test: `tests/integration/test_admin_issuance.py`

- [ ] **Step 1: 写测试**

创建 `tests/integration/test_admin_issuance.py`：

```python
"""Integration: /admin endpoints issue usable credentials end-to-end."""

from __future__ import annotations

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from gateway.community.adapters.web.app import create_app
from gateway.community.bootstrap._authn import build_database
from gateway.community.core.access_key import AccessKeyRepository
from gateway.community.core.app import AppRepository
from gateway.community.plugins.principal_signer.bare._plugin import _DEV_FALLBACK_KEY


async def test_issue_access_key_via_http() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/access-keys",
            json={
                "access_key_id": "ak-http",
                "tenant": "t",
                "expire_at": "2027-01-01T00:00:00",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_key_id"] == "ak-http"
    assert body["tenant"] == "t"
    token = body["token"]

    decoded = jwt.decode(token, _DEV_FALLBACK_KEY, algorithms=["HS256"])
    assert decoded["typ"] == "access_key"
    assert decoded["sub"] == "ak-http"

    rec = await AccessKeyRepository(build_database()).find_access_key_by_token(token)
    assert rec is not None
    assert rec.access_key_id == "ak-http"


async def test_register_app_via_http() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps",
            json={
                "app_id": "app-http",
                "app_name": "Http App",
                "owners": "org-1",
                "app_type": "assistant",
                "tenant": "t",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["app_id"] == "app-http"
    token = body["token"]

    decoded = jwt.decode(token, _DEV_FALLBACK_KEY, algorithms=["HS256"])
    assert decoded["typ"] == "app"
    assert decoded["sub"] == "app-http"
    assert "exp" not in decoded

    rec = await AppRepository(build_database()).find_app_by_token(token)
    assert rec is not None
    assert rec.app_id == "app-http"


async def test_missing_field_returns_422() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/access-keys",
            json={"access_key_id": "x"},  # missing tenant + expire_at
        )
    assert resp.status_code == 422


async def test_bad_expire_at_returns_422() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/access-keys",
            json={
                "access_key_id": "x",
                "tenant": "t",
                "expire_at": "not-a-date",
            },
        )
    assert resp.status_code == 422
```

- [ ] **Step 2: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/integration/test_admin_issuance.py -q`
Expected: PASS（4 passed）。若失败，查看 `create_app` 的 `app.state.access_key_issuer`/`app_registrar` 是否挂上、路由是否注册。

- [ ] **Step 3: ruff + 提交**

```bash
.venv/bin/python -m ruff format tests/integration/test_admin_issuance.py
.venv/bin/python -m ruff check .
git add tests/integration/test_admin_issuance.py
git commit -m "test(admin): integration-test access_key issuance + app registration"
```

---

### Task 7: 全量校验

- [ ] **Step 1: 全量 ruff**

Run: `.venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check .`
Expected: 全部 already formatted；All checks passed。

- [ ] **Step 2: 全量测试（排除需运行中网关的 e2e）**

Run: `.venv/bin/python -m pytest -q --deselect tests/e2e/baseline/test_health.py`
Expected: 全绿；新增：`test_principal_signer.py`(+1) + `test_access_key_issuer.py`(2) + `test_app_registrar.py`(2) + `test_admin_issuance.py`(4)。

- [ ] **Step 3: 若有改动收尾提交**

```bash
git status
git add -A && git commit -m "chore: ruff + green suite for credential issuance" || true
```

---

## Self-Review notes

- spec §1 目标（access_key 签发 + app 注册）→ Task 2、Task 3、Task 5、Task 6。
- spec §2 组件/放置（含 `PrincipalSigner.sign_token` SPI 增量 + `BarePrincipalSigner` 重构、`IssuedAccessKey`/`IssuedApp`、admin router、`app.state`）→ Task 1、Task 2、Task 3、Task 4、Task 5。
- spec §3 claims（access_key: iss/typ/sub/tenant/iat/exp/jti + kid 头；app: 无 exp）→ Task 2 Step 3、Task 3 Step 3，并由 Task 2/3/6 测试钉住。
- spec §4 数据流/HTTP → Task 5、Task 6。
- spec §5 组装 → Task 4、Task 5。
- spec §6 错误（422 自动 / 500 兜底 / 重复不拦截）→ Task 5（500 `_error` + FastAPI 422）、Task 6（422 用例）；重复不拦截由 `jti` 唯一 + PK=token 保证，无需额外代码。
- spec §7 测试 → Task 1/2/3/6/7。
- 类型一致：`sign_token(claims: Mapping[str, object]) -> str`（SPI 与 bare 一致）；`AccessKeyIssuer.issue(access_key_id, tenant, expire_at) -> IssuedAccessKey`；`AppRegistrar.register(app_id, app_name, owners, app_type, tenant) -> IssuedApp`；`build_access_key_issuer(db, signer)`/`build_app_registrar(db, signer)`；`app.state.access_key_issuer`/`app_registrar`；admin 路径 `/admin/access-keys`、`/admin/apps`。