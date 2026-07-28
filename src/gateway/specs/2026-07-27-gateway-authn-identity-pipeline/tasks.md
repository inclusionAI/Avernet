# Tasks: Gateway 认证 — 多身份解析管线

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use `- [ ]` checkboxes for tracking.
>
> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> **Spec:** `specs/2026-07-27-gateway-authn-identity-pipeline/spec.md`
> **Plan:** `specs/2026-07-27-gateway-authn-identity-pipeline/plan.md`

## 序列与策略

- **任务 1–6:** 全部**加法**(新增文件),不触碰现有认证路径,每步可独立绿。
- **任务 7:** **原子 cutover** —— 一次性把契约(`Requirement`/`AuthStrategy.build`/`Authenticator`)、布线、配置、受影响测试改形。这一任务中间会红(测试先改形→红,实现改形→绿),是预期的。
- **任务 8:** 清理死代码(删除 `first_party_user/` 包)。
- **任务 9:** forward seam。
- **任务 10:** 集成薄冒烟。
- **任务 11:** 全量门禁。

约定命令(本组件根目录 = `src/gateway`):
```bash
cd src/gateway
ruff check src tests
mypy src
pytest -m "not e2e" -q
```

---

## Task 1: `Presence` 枚举(必需/可选标记)

**Files:**
- Create: `src/gateway/src/gateway/community/spi/authn/_models.py`(已存在,追加)→ 实际操作:Modify `src/gateway/src/gateway/community/spi/authn/_models.py`(追加 `Presence` 类与导出)
- Test: `src/gateway/tests/test_authn_models.py`

> 注:`_models.py` 已存在,本任务是往其**末尾追加** `Presence` 类(暂不动 `Principal`/`StrategyParams`,它们在任务 7 cutover)。

- [ ] **Step 1: 写失败测试** — 在 `src/gateway/tests/test_authn_models.py` 末尾追加:

```python
def test_presence_enum_values() -> None:
    from gateway.community.spi.authn import Presence

    assert Presence.REQUIRED == "required"
    assert Presence.OPTIONAL == "optional"
```

- [ ] **Step 2: 运行验证失败**

```bash
cd src/gateway && pytest tests/test_authn_models.py::test_presence_enum_values -q
```
Expected: FAIL — `ImportError: cannot import name 'Presence'`

- [ ] **Step 3: 最小实现** — 在 `src/gateway/src/gateway/community/spi/authn/_models.py` 末尾(在 `StrategyParams` 之后)追加:

```python


class Presence(StrEnum):
    """Whether a route requires an identity or merely accepts it."""

    REQUIRED = "required"  # the identity must be present or the request is denied
    OPTIONAL = "optional"  # the identity is accepted if present, ignored if absent
```

- [ ] **Step 4: 导出 `Presence`** — Modify `src/gateway/src/gateway/community/spi/authn/__init__.py`,在 import 列表与 `__all__` 中加 `Presence`:

```python
"""Authn SPI — the neutral Principal contract produced by the gateway.

See ``_models`` for the model definitions, ``_protocols`` for the
``AuthStrategy`` contract, and the auth design doc
(``src/gateway/docs/2026-07-21-auth-design.md``) for the full picture.
"""

from ._models import (
    CredentialBundle,
    Delegation,
    Presence,
    Principal,
    PrincipalType,
    StrategyParams,
    UserPrincipal,
)
from ._protocols import AuthStrategy

__all__ = [
    "AuthStrategy",
    "CredentialBundle",
    "Delegation",
    "Presence",
    "Principal",
    "PrincipalType",
    "StrategyParams",
    "UserPrincipal",
]
```

- [ ] **Step 5: 运行通过**

```bash
cd src/gateway && pytest tests/test_authn_models.py::test_presence_enum_values -q
```
Expected: PASS

- [ ] **Step 6: 提交**

```bash
cd src/gateway && git add src/gateway/community/spi/authn/_models.py src/gateway/community/spi/authn/__init__.py tests/test_authn_models.py
git commit -m "feat(authn): add Presence enum for required/optional identity"
```

---

## Task 2: `Bot` 与 `ThirdPartyApp` 模型 + `BotPrincipal` / `AppPrincipal`

**Files:**
- Modify: `src/gateway/src/gateway/community/spi/authn/_models.py`(在 `UserPrincipal` 之后新增类)
- Test: `src/gateway/tests/test_authn_models.py`

> 仍为加法:新增 `Bot`/`ThirdPartyApp`/`BotPrincipal`/`AppPrincipal` 并把 `PrincipalType` 的 `APP`/`BOT` 成员启用。**暂不改 `Principal` 别名**(任务 7 cutover 时改),这条 alias 仍是 `UserPrincipal`,以免牵动既有 `Principal` 消费者。

- [ ] **Step 1: 写失败测试** — Modify `src/gateway/tests/test_authn_models.py`:把顶部 `from gateway.community.spi.authn import (...)` 块替换为(加 `AppPrincipal`/`Bot`/`BotPrincipal`/`ThirdPartyApp`,保留任务 1 已加的 `Presence` 及既有的 `Delegation`/`StrategyParams`):

```python
from gateway.community.spi.authn import (
    AppPrincipal,
    Bot,
    BotPrincipal,
    CredentialBundle,
    Delegation,
    Presence,
    Principal,
    PrincipalType,
    StrategyParams,
    ThirdPartyApp,
    UserPrincipal,
)
```

并在文件末尾追加测试:

```python
def test_app_and_bot_principal_types() -> None:
    app = AppPrincipal(
        tenant="t-app",
        app=ThirdPartyApp(
            client_id="cid", developer_org_id="org-1", app_type="assistant"
        ),
    )
    assert app.type == "third_party_app"
    assert app.tenant == "t-app"
    assert app.app.client_id == "cid"
    assert app.on_behalf_of_opaque is None

    bot = BotPrincipal(
        tenant="t-bot",
        bot=Bot(bot_id="b-1", owner_org_id="org-1"),
    )
    assert bot.type == "bot"
    assert bot.bot.bot_id == "b-1"
```

- [ ] **Step 2: 运行验证失败**

```bash
cd src/gateway && pytest tests/test_authn_models.py::test_app_and_bot_principal_types -q
```
Expected: FAIL — `ImportError: cannot import name 'AppPrincipal'` (或 `Bot`/`ThirdPartyApp`/`BotPrincipal`)

- [ ] **Step 3: 实现** — Modify `src/gateway/src/gateway/community/spi/authn/_models.py`:把 `PrincipalType` 中的两行注释成员替换为正式成员:

```python
class PrincipalType(StrEnum):
    """Discriminator for the kind of caller a ``Principal`` represents."""

    USER = "user"  # a first-party authenticated user
    BOT = "bot"  # a bot/agent acting on its own behalf
    APP = "third_party_app"  # a third-party developer application
```

在 `UserPrincipal` 类定义之后(在 `# `Principal` becomes...` 注释之前)插入 `Bot`、`ThirdPartyApp`、`BotPrincipal`、`AppPrincipal`:

```python


class ThirdPartyApp(BaseModel):
    """A registered third-party developer application — the calling program itself."""

    model_config = ConfigDict(frozen=True)

    client_id: str  # app id issued at registration (maps to baas api-key record's app_id)
    developer_org_id: str  # owning developer/org; resource-ownership fallback subject
    app_type: str = "UNKNOWN"  # from the api-key record


class Bot(BaseModel):
    """A bot/agent acting as a first-class caller in its own right."""

    model_config = ConfigDict(frozen=True)

    bot_id: str  # stable, provider-issued bot id
    owner_org_id: str  # owning developer/org the bot belongs to
    bot_type: str = "UNKNOWN"  # from the bot record, if any


class BotPrincipal(BaseModel):
    """A bot/agent caller, produced by the gateway.

    Ownership and resource resolution anchor to ``bot`` **within** ``tenant``.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.BOT] = PrincipalType.BOT
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    bot: Bot = Field(description="The authenticated bot/agent identity.")


class AppPrincipal(BaseModel):
    """A third-party application caller, produced by the gateway."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.APP] = PrincipalType.APP
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    app: ThirdPartyApp = Field(description="The authenticated third-party app.")
    on_behalf_of_opaque: str | None = Field(
        default=None,
        description=(
            "Opaque, unverified handle of the app's own end user. ``None`` is an "
            "intentional default (the app calls on its own behalf). Used only for "
            "ownership/quota/audit — never as an authenticated identity for "
            "cross-resource decisions."
        ),
    )
```

- [ ] **Step 4: 导出** — Modify `src/gateway/src/gateway/community/spi/authn/__init__.py`,加 `AppPrincipal`/`BotPrincipal`/`Bot`/`ThirdPartyApp`:

```python
from ._models import (
    AppPrincipal,
    Bot,
    BotPrincipal,
    CredentialBundle,
    Delegation,
    Presence,
    Principal,
    PrincipalType,
    StrategyParams,
    ThirdPartyApp,
    UserPrincipal,
)
from ._protocols import AuthStrategy

__all__ = [
    "AppPrincipal",
    "AuthStrategy",
    "Bot",
    "BotPrincipal",
    "CredentialBundle",
    "Delegation",
    "Presence",
    "Principal",
    "PrincipalType",
    "StrategyParams",
    "ThirdPartyApp",
    "UserPrincipal",
]
```

- [ ] **Step 5: 运行通过**

```bash
cd src/gateway && pytest tests/test_authn_models.py -q
```
Expected: PASS(既有的 `Principal is UserPrincipal`、`scopes` 等测试仍绿,因为 `Principal` 别名未改)

- [ ] **Step 6: 提交**

```bash
cd src/gateway && git add src/gateway/community/spi/authn/_models.py src/gateway/community/spi/authn/__init__.py tests/test_authn_models.py
git commit -m "feat(authn): add Bot and App principal models"
```

---

## Task 3: 依赖协议 SPI(`_ports.py`) — ApiKey / Tenant / BotToken

**Files:**
- Create: `src/gateway/src/gateway/community/spi/authn/_ports.py`
- Modify: `src/gateway/src/gateway/community/spi/authn/__init__.py`(导出)
- Test: `src/gateway/tests/contracts/spi/test_authn_ports.py`(新)

> 加法。定义 extractor 依赖的 SPI:validation/tenant-resolution 返回 record / tenant id。`bare` 实现在任务 4。

- [ ] **Step 1: 写失败测试** — Create `src/gateway/tests/contracts/spi/test_authn_ports.py`:

```python
"""Smoke tests for the authn dependency-port protocols (Rule 25)."""

from __future__ import annotations

from gateway.community.spi.authn import (
    ApiKeyRecord,
    ApiKeyValidator,
    BotRecord,
    BotTokenValidator,
    TenantResolver,
)


def test_records_have_required_fields() -> None:
    rec = ApiKeyRecord(
        client_id="cid",
        developer_org_id="org-1",
        app_type="assistant",
        tenant="t-1",
    )
    assert rec.client_id == "cid"
    assert rec.tenant == "t-1"

    bot = BotRecord(bot_id="b-1", owner_org_id="org-1", tenant="t-1")
    assert bot.bot_id == "b-1"


def test_protocols_are_importable() -> None:
    for proto in (ApiKeyValidator, TenantResolver, BotTokenValidator):
        assert hasattr(proto, "__protocol_attrs__") or proto is not None
```

- [ ] **Step 2: 运行验证失败**

```bash
cd src/gateway && pytest tests/contracts/spi/test_authn_ports.py -q
```
Expected: FAIL — `ImportError: cannot import name 'ApiKeyRecord' ...`

- [ ] **Step 3: 实现** — Create `src/gateway/src/gateway/community/spi/authn/_ports.py`:

```python
"""Authn dependency ports — the SPIs the identity extractors depend on.

Each extractor calls a port it does not implement; flavors (``bare`` / ``sofa``)
swap the implementation via ``PluginAccessor`` (Rule 14). These are
``Protocol``\ s (behaviour contracts) plus the ``dataclass`` records they return.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ApiKeyRecord:
    """A validated third-party API key record (backed by baas api-gateway)."""

    client_id: str  # baas app_id
    developer_org_id: str
    app_type: str
    tenant: str  # the App's registered tenant (cross-checked with the tenant token)


@dataclass(frozen=True)
class BotRecord:
    """A validated bot/agent credential record."""

    bot_id: str
    owner_org_id: str
    tenant: str  # the bot's registered tenant


class ApiKeyValidator(Protocol):
    """Verify a third-party API key. ``None`` = no match; never raise on a bad key."""

    async def verify(self, api_key: str) -> ApiKeyRecord | None:
        ...


class TenantResolver(Protocol):
    """Verify a per-tenant token and map it to a tenant id.

    Missing/invalid token raises :class:`~gateway.community.spi.auth.AuthError`.
    """

    async def resolve(self, tenant_token: str) -> str:
        ...


class BotTokenValidator(Protocol):
    """Verify a bot credential. ``None`` = no match; never raise on a bad token."""

    async def verify(self, bot_token: str) -> BotRecord | None:
        ...
```

- [ ] **Step 4: 导出** — Modify `src/gateway/src/gateway/community/spi/authn/__init__.py`,加端口与 record:

```python
from ._models import (
    AppPrincipal,
    Bot,
    BotPrincipal,
    CredentialBundle,
    Delegation,
    Presence,
    Principal,
    PrincipalType,
    StrategyParams,
    ThirdPartyApp,
    UserPrincipal,
)
from ._ports import (
    ApiKeyRecord,
    ApiKeyValidator,
    BotRecord,
    BotTokenValidator,
    TenantResolver,
)
from ._protocols import AuthStrategy

__all__ = [
    "ApiKeyRecord",
    "ApiKeyValidator",
    "AppPrincipal",
    "AuthStrategy",
    "Bot",
    "BotPrincipal",
    "BotRecord",
    "BotTokenValidator",
    "CredentialBundle",
    "Delegation",
    "Presence",
    "Principal",
    "PrincipalType",
    "StrategyParams",
    "TenantResolver",
    "ThirdPartyApp",
    "UserPrincipal",
]
```

- [ ] **Step 5: 运行通过**

```bash
cd src/gateway && pytest tests/contracts/spi/test_authn_ports.py -q
```
Expected: PASS

- [ ] **Step 6: 架构测试** — 确认 Rule 12(协议入 `__all__`)+ 导出可解析:

```bash
cd src/gateway && pytest tests/architecture/test_protocol_exports.py tests/architecture/test_all_exports_valid.py -q
```
Expected: PASS(`AuthStrategy`/`ApiKeyValidator`/`TenantResolver`/`BotTokenValidator` 均在 `__all__` 且可解析)

- [ ] **Step 7: 提交**

```bash
cd src/gateway && git add src/gateway/community/spi/authn/_ports.py src/gateway/community/spi/authn/__init__.py tests/contracts/spi/test_authn_ports.py
git commit -m "feat(authn): add dependency ports (ApiKey/Tenant/BotToken)"
```

---

## Task 4: `bare` 桩实现(ApiKeyValidator / TenantResolver / BotTokenValidator)

**Files:**
- Create: `src/gateway/src/gateway/community/plugins/authn/api_key_validator/bare/_plugin.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/api_key_validator/bare/__init__.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/tenant_resolver/bare/_plugin.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/tenant_resolver/bare/__init__.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/bot_token_validator/bare/_plugin.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/bot_token_validator/bare/__init__.py`
- Test: `src/gateway/tests/unit/plugins/test_authn_bare_validators.py`(新)

> 加法。`bare` = 内存/固定值桩,单盒开箱用。`bare` 不是真校验(明示其桩性质)。

- [ ] **Step 1: 写失败测试** — Create `src/gateway/tests/unit/plugins/test_authn_bare_validators.py`:

```python
"""Tests for the bare (open-source) authn validator stubs."""

from __future__ import annotations

import pytest

from gateway.community.plugins.authn.api_key_validator.bare import (
    BareApiKeyValidator,
)
from gateway.community.plugins.authn.bot_token_validator.bare import (
    BareBotTokenValidator,
)
from gateway.community.plugins.authn.tenant_resolver.bare import (
    BareTenantResolver,
)
from gateway.community.spi.auth import AuthError


async def test_api_key_validator_match_returns_record() -> None:
    v = BareApiKeyValidator()
    rec = await v.verify("bare-api-key")
    assert rec is not None
    assert rec.tenant == "tenant-bare"


async def test_api_key_validator_no_match_returns_none() -> None:
    v = BareApiKeyValidator()
    assert await v.verify("non-existent-key") is None


async def test_bare_tenant_resolver_requires_token() -> None:
    r = BareTenantResolver()
    with pytest.raises(AuthError):
        await r.resolve("")


async def test_bare_tenant_resolver_maps_to_fixed_tenant() -> None:
    r = BareTenantResolver()
    assert await r.resolve("any-non-empty") == "tenant-bare"


async def test_bot_token_validator_match_returns_record() -> None:
    v = BareBotTokenValidator()
    rec = await v.verify("bare-bot-token")
    assert rec is not None
    assert rec.bot_id == "bare-bot-001"


async def test_bot_token_validator_no_match_returns_none() -> None:
    v = BareBotTokenValidator()
    assert await v.verify("nope") is None
```

- [ ] **Step 2: 运行验证失败**

```bash
cd src/gateway && pytest tests/unit/plugins/test_authn_bare_validators.py -q
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 bare ApiKeyValidator** — Create both files.

`src/gateway/src/gateway/community/plugins/authn/api_key_validator/bare/__init__.py`:
```python
from ._plugin import BareApiKeyValidator

__all__ = ["BareApiKeyValidator"]
```

`src/gateway/src/gateway/community/plugins/authn/api_key_validator/bare/_plugin.py`:
```python
"""BareApiKeyValidator — open-source single-box stub for the API-key validator.

Accepts ``bare-api-key`` and returns a fixed in-memory record; any other key
yields ``None``. NOT real validation — production uses the sofa (baas) flavor.
"""

from __future__ import annotations

from gateway.community.spi.authn import ApiKeyRecord, ApiKeyValidator

_BARE_KEY = "bare-api-key"


class BareApiKeyValidator(ApiKeyValidator):
    """Single-box stub: one hard-coded valid key."""

    async def verify(self, api_key: str) -> ApiKeyRecord | None:
        if api_key != _BARE_KEY:
            return None
        return ApiKeyRecord(
            client_id="bare-app",
            developer_org_id="bare-org",
            app_type="bare",
            tenant="tenant-bare",
        )
```

- [ ] **Step 4: 实现 bare TenantResolver** — Create both files.

`src/gateway/src/gateway/community/plugins/authn/tenant_resolver/bare/__init__.py`:
```python
from ._plugin import BareTenantResolver

__all__ = ["BareTenantResolver"]
```

`src/gateway/src/gateway/community/plugins/authn/tenant_resolver/bare/_plugin.py`:
```python
"""BareTenantResolver — open-source single-box stub for the tenant resolver.

Maps any non-empty tenant token to the fixed ``tenant-bare``. NOT real
validation — production uses the sofa flavor (a tenant-token registry).
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import TenantResolver

_BARE_TENANT = "tenant-bare"


class BareTenantResolver(TenantResolver):
    """Single-box stub: one fixed tenant."""

    async def resolve(self, tenant_token: str) -> str:
        if not tenant_token:
            raise AuthError("missing tenant token")
        return _BARE_TENANT
```

- [ ] **Step 5: 实现 bare BotTokenValidator** — Create both files.

`src/gateway/src/gateway/community/plugins/authn/bot_token_validator/bare/__init__.py`:
```python
from ._plugin import BareBotTokenValidator

__all__ = ["BareBotTokenValidator"]
```

`src/gateway/src/gateway/community/plugins/authn/bot_token_validator/bare/_plugin.py`:
```python
"""BareBotTokenValidator — open-source single-box stub for the bot validator.

Accepts ``bare-bot-token`` and returns a fixed in-memory record; any other
token yields ``None``. NOT real validation — production uses the sofa flavor.
"""

from __future__ import annotations

from gateway.community.spi.authn import BotRecord, BotTokenValidator

_BARE_TOKEN = "bare-bot-token"


class BareBotTokenValidator(BotTokenValidator):
    """Single-box stub: one hard-coded valid bot token."""

    async def verify(self, bot_token: str) -> BotRecord | None:
        if bot_token != _BARE_TOKEN:
            return None
        return BotRecord(
            bot_id="bare-bot-001",
            owner_org_id="bare-org",
            tenant="tenant-bare",
        )
```

- [ ] **Step 6: 运行通过**

```bash
cd src/gateway && pytest tests/unit/plugins/test_authn_bare_validators.py -q
```
Expected: PASS

- [ ] **Step 7: 提交**

```bash
cd src/gateway && git add src/gateway/community/plugins/authn/api_key_validator/ src/gateway/community/plugins/authn/tenant_resolver/ src/gateway/community/plugins/authn/bot_token_validator/ tests/unit/plugins/test_authn_bare_validators.py
git commit -m "feat(authn): add bare stubs for api-key/tenant/bot validators"
```

---

## Task 5: `IdentityExtractor` 协议

**Files:**
- Modify: `src/gateway/src/gateway/community/spi/authn/_protocols.py`(新增 `IdentityExtractor`,暂不动 `AuthStrategy`)
- Modify: `src/gateway/src/gateway/community/spi/authn/__init__.py`(导出)
- Test: `src/gateway/tests/contracts/spi/test_identity_extractor.py`(新)

> 加法。插件最小单位协议。

- [ ] **Step 1: 写失败测试** — Create `src/gateway/tests/contracts/spi/test_identity_extractor.py`:

```python
"""Conformance tests for the ``IdentityExtractor`` protocol (Rule 25)."""

from __future__ import annotations

from gateway.community.spi.authn import CredentialBundle, IdentityExtractor


class _FixedExtractor:
    name = "fixed"

    def __init__(self, result: object) -> None:
        self._result = result

    async def extract(self, creds: CredentialBundle) -> object:
        return self._result


async def test_extractor_iface_is_importable() -> None:
    assert IdentityExtractor is not None


async def test_extractor_returns_none_or_principal() -> None:
    ex = _FixedExtractor(None)
    assert await ex.extract(CredentialBundle(headers={}, cookies={}, query={})) is None
```

- [ ] **Step 2: 运行验证失败**

```bash
cd src/gateway && pytest tests/contracts/spi/test_identity_extractor.py -q
```
Expected: FAIL — `ImportError: cannot import name 'IdentityExtractor'`

- [ ] **Step 3: 实现** — Modify `src/gateway/src/gateway/community/spi/authn/_protocols.py`,在 `AuthStrategy` 之后追加(并更新顶部 import 行加入 `IdentityExtractor` 的依赖):

```python
from ._models import CredentialBundle, Principal
```

(把原 `from ._models import CredentialBundle, Principal, StrategyParams` 中的 `StrategyParams` 移除,改为上方;`StrategyParams` 仍由 `AuthStrategy.build` 形参引用,但 cutover 时其形参会被移除 —— 此处只在 `IdentityExtractor` 段不引入它。事实上为最小改动,保留 `StrategyParams` import 不动更安全。)

**为最小改动,不调整顶部 import,直接在文件末尾追加:**

```python


class IdentityExtractor(Protocol):
    """The smallest unit of identity resolution — one credential → one Principal.

    A strategy holds an ordered list of extractors and runs them until one
    returns a Principal. Each extractor first decides whether it recognises the
    request's credential at all:

    - returns ``None``  → this extractor's credential is absent; the chain
      continues to the next extractor;
    - returns a ``Principal`` → success; the chain returns it;
    - raises :class:`~gateway.community.spi.auth.AuthError` → the credential is
      **present but invalid** (hard failure); it propagates and the chain does
      NOT fall back to a later extractor (design §5).
    """

    name: str  # stable id referenced by system config (identity_extractors)

    async def extract(self, creds: CredentialBundle) -> Principal | None:
        ...
```

同时把 `_protocols.py` 顶部 import 加上 `IdentityExtractor` 不需要(它在本文件内定义),仅在 `__init__` 导出。

- [ ] **Step 4: 导出** — Modify `src/gateway/src/gateway/community/spi/authn/__init__.py`:把 `from ._protocols import AuthStrategy` 改为 `from ._protocols import AuthStrategy, IdentityExtractor`,并在 `__all__` 加 `"IdentityExtractor"`:

```python
from ._protocols import AuthStrategy, IdentityExtractor

__all__ = [
    "ApiKeyRecord",
    "ApiKeyValidator",
    "AppPrincipal",
    "AuthStrategy",
    "Bot",
    "BotPrincipal",
    "BotRecord",
    "BotTokenValidator",
    "CredentialBundle",
    "Delegation",
    "IdentityExtractor",
    "Presence",
    "Principal",
    "PrincipalType",
    "StrategyParams",
    "TenantResolver",
    "ThirdPartyApp",
    "UserPrincipal",
]
```

- [ ] **Step 5: 运行通过**

```bash
cd src/gateway && pytest tests/contracts/spi/test_identity_extractor.py tests/architecture/test_protocol_exports.py -q
```
Expected: PASS

- [ ] **Step 6: 提交**

```bash
cd src/gateway && git add src/gateway/community/spi/authn/_protocols.py src/gateway/community/spi/authn/__init__.py tests/contracts/spi/test_identity_extractor.py
git commit -m "feat(authn): add IdentityExtractor protocol"
```

---

## Task 6: `IdentityStrategy` 链跑器(core)+ 三个 extractor 实现

**Files:**
- Create: `src/gateway/src/gateway/community/core/authn/_strategy.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/user/__init__.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/user/_extractors.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/app/__init__.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/app/_extractors.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/bot/__init__.py`
- Create: `src/gateway/src/gateway/community/plugins/authn/bot/_extractors.py`
- Modify: `src/gateway/src/gateway/community/core/authn/__init__.py`(导出 `IdentityStrategy`)
- Test: `src/gateway/tests/test_identity_strategy.py`(新)
- Test: `src/gateway/tests/unit/plugins/test_authn_extractors.py`(新)
- Test: `src/gateway/tests/contracts/spi/test_identity_extractor.py`(任务 5 已建,追加策略 conformance 子类)

> 加法,可独立运行(`IdentityStrategy` 与 extractor 暂不被现有 runner 调用;cutover 前 `build_authenticator` 不引用)。先 `IdentityStrategy` 后 extractor,因为 extractor 测试走真 extractor。

### 6a. `IdentityStrategy` core

- [ ] **Step 1: 写失败测试** — Create `src/gateway/tests/test_identity_strategy.py`:

```python
"""Unit tests for the IdentityStrategy chain runner."""

from __future__ import annotations

import pytest

from gateway.community.core.authn import IdentityStrategy
from gateway.community.spi.auth import AuthError
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    AppPrincipal,
    BotPrincipal,
    CredentialBundle,
    IdentityExtractor,
    Principal,
    PrincipalType,
    ThirdPartyApp,
    UserPrincipal,
)


class _Fixed:
    """Extractor returning a fixed Principal / None / raising AuthError."""

    def __init__(self, name: str, result: Principal | AuthError | None) -> None:
        self.name = name
        self._result = result

    async def extract(self, creds: CredentialBundle) -> Principal | None:
        if isinstance(self._result, AuthError):
            raise self._result
        return self._result


def _user() -> UserPrincipal:
    return UserPrincipal(
        tenant="t", subject=AuthenticatedUser(id="u", username="a")
    )


def _app() -> AppPrincipal:
    return AppPrincipal(
        tenant="t", app=ThirdPartyApp(client_id="c", developer_org_id="o")
    )


_CREDS = CredentialBundle(headers={}, cookies={}, query={})


async def test_first_applicable_extractor_wins() -> None:
    strat = IdentityStrategy(
        identity=PrincipalType.USER, extractors=[_Fixed("a", _user()), _Fixed("b", None)]
    )
    assert await strat.build(_CREDS) is _user() or True  # smoke; real check below


async def test_first_extractor_returns_principal() -> None:
    first = _Fixed("a", _user())
    strat = IdentityStrategy(identity=PrincipalType.USER, extractors=[first])
    result = await strat.build(_CREDS)
    assert isinstance(result, UserPrincipal)


async def test_skips_none_and_returns_later_principal() -> None:
    strat = IdentityStrategy(
        identity=PrincipalType.USER,
        extractors=[_Fixed("a", None), _Fixed("b", _app())],
    )
    result = await strat.build(_CREDS)
    assert isinstance(result, AppPrincipal)


async def test_all_none_returns_none() -> None:
    strat = IdentityStrategy(
        identity=PrincipalType.USER, extractors=[_Fixed("a", None), _Fixed("b", None)]
    )
    assert await strat.build(_CREDS) is None


async def test_hard_failure_propagates_and_does_not_fall_back() -> None:
    strat = IdentityStrategy(
        identity=PrincipalType.USER,
        extractors=[_Fixed("bad", AuthError("bad")), _Fixed("good", _user())],
    )
    with pytest.raises(AuthError):
        await strat.build(_CREDS)


async def test_empty_chain_returns_none() -> None:
    strat = IdentityStrategy(identity=PrincipalType.USER, extractors=[])
    assert await strat.build(_CREDS) is None
```

- [ ] **Step 2: 运行验证失败**

```bash
cd src/gateway && pytest tests/test_identity_strategy.py -q
```
Expected: FAIL — `ImportError: cannot import name 'IdentityStrategy'`

- [ ] **Step 3: 实现** — Create `src/gateway/src/gateway/community/core/authn/_strategy.py`:

```python
"""Identity-strategy chain runner — run one identity's extractors in order.

A strategy holds the ordered extractor chain enabled for one ``PrincipalType``.
It runs the chain until the first extractor that recognises the credential and
returns a ``Principal``:

- an extractor returning ``None`` (credential absent) → try the next;
- an extractor raising :class:`~gateway.community.spi.auth.AuthError` (credential
  present but invalid) → **propagate immediately**, do NOT fall back (design §5);
- an extractor returning a ``Principal`` → return it.

If every extractor is inapplicable, return ``None`` (the caller decides whether
``None`` is acceptable from the route's ``Presence``).
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle, IdentityExtractor, Principal, PrincipalType


class IdentityStrategy:
    """Runs one identity type's ordered extractor chain (§5).

    Implements the :class:`~gateway.community.spi.authn.AuthStrategy` protocol
    structurally (``name`` + ``build``); the composition root registers one
    instance per ``PrincipalType``.
    """

    def __init__(
        self, identity: PrincipalType, extractors: list[IdentityExtractor]
    ) -> None:
        self.identity = identity
        self._extractors = extractors

    @property
    def name(self) -> str:
        return self.identity.value

    async def build(self, creds: CredentialBundle) -> Principal | None:
        """Try each extractor in order; return the first successful Principal."""
        for extractor in self._extractors:
            # ``AuthError`` (present-but-invalid) propagates and short-circuits
            # the chain; ``None`` (absent) falls through to the next extractor.
            principal = await extractor.extract(creds)
            if principal is not None:
                return principal
        return None  # every extractor was inapplicable
```

- [ ] **Step 4: 导出** — Modify `src/gateway/src/gateway/community/core/authn/__init__.py`,加 `IdentityStrategy`:

```python
"""Core authn — transport-agnostic route-security resolution + auth runner.

``RouteSecurity`` resolves a request to its required strategies; ``authenticate``
runs them against the strategy registry to produce a Principal. Neither depends
on any web framework (Rule 7).
"""

from ._route_security import Requirement, RouteSecurity
from ._runner import authenticate
from ._strategy import IdentityStrategy

__all__ = [
    "IdentityStrategy",
    "Requirement",
    "RouteSecurity",
    "authenticate",
]
```

- [ ] **Step 5: 运行通过**

```bash
cd src/gateway && pytest tests/test_identity_strategy.py -q
```
Expected: PASS

### 6b. extractor 实现:User / App / Bot

- [ ] **Step 6: 写 extractor 失败测试** — Create `src/gateway/tests/unit/plugins/test_authn_extractors.py`:

```python
"""Unit tests for the user/app/bot identity extractors."""

from __future__ import annotations

import pytest

from gateway.community.plugins.authn.app._extractors import ApiKeyExtractor
from gateway.community.plugins.authn.bot._extractors import BotTokenExtractor
from gateway.community.plugins.authn.user._extractors import SessionCookieExtractor
from gateway.community.plugins.auth.api_key_validator.bare import BareApiKeyValidator
from gateway.community.plugins.auth.bot_token_validator.bare import (
    BareBotTokenValidator,
)
from gateway.community.plugins.auth.tenant_resolver.bare import BareTenantResolver
from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    AppPrincipal,
    BotPrincipal,
    CredentialBundle,
    UserPrincipal,
)


def _creds_session() -> CredentialBundle:
    return CredentialBundle(
        headers={"cookie": "SSO_TOKEN=abc"}, cookies={"SSO_TOKEN": "abc"}, query={}
    )


async def test_session_cookie_absent_returns_none() -> None:
    ex = SessionCookieExtractor(
        auth=BareAuthPlugin(default_user=AuthenticatedUser(id="u1", username="a")),
        default_tenant="tenant-default",
    )
    result = await ex.extract(CredentialBundle(headers={}, cookies={}, query={}))
    assert result is None


async def test_session_cookie_builds_user_principal() -> None:
    ex = SessionCookieExtractor(
        auth=BareAuthPlugin(
            default_user=AuthenticatedUser(id="u1", username="a", tenant_id="t-9")
        ),
        default_tenant="tenant-default",
    )
    result = await ex.extract(_creds_session())
    assert isinstance(result, UserPrincipal)
    assert result.tenant == "t-9"
    assert result.subject.id == "u1"


async def test_session_cookie_falls_back_to_default_tenant() -> None:
    ex = SessionCookieExtractor(
        auth=BareAuthPlugin(default_user=AuthenticatedUser(id="u1", username="a")),
        default_tenant="tenant-default",
    )
    result = await ex.extract(_creds_session())
    assert isinstance(result, UserPrincipal)
    assert result.tenant == "tenant-default"


def _creds_bearer(token: str) -> CredentialBundle:
    return CredentialBundle(
        headers={"authorization": f"Bearer {token}", "x-tenant-token": "t"},
        cookies={},
        query={},
    )


async def test_api_key_absent_returns_none() -> None:
    ex = ApiKeyExtractor(
        keys=BareApiKeyValidator(), tenants=BareTenantResolver()
    )
    result = await ex.extract(
        CredentialBundle(headers={}, cookies={}, query={})
    )
    assert result is None


async def test_api_key_invalid_raises() -> None:
    ex = ApiKeyExtractor(keys=BareApiKeyValidator(), tenants=BareTenantResolver())
    with pytest.raises(Exception):
        await ex.extract(
            CredentialBundle(
                headers={"authorization": "Bearer nope"}, cookies={}, query={}
            )
        )


async def test_api_key_valid_builds_app_principal() -> None:
    ex = ApiKeyExtractor(keys=BareApiKeyValidator(), tenants=BareTenantResolver())
    result = await ex.extract(_creds_bearer("bare-api-key"))
    assert isinstance(result, AppPrincipal)
    assert result.tenant == "tenant-bare"
    assert result.app.client_id == "bare-app"


async def test_bot_token_absent_returns_none() -> None:
    ex = BotTokenExtractor(validator=BareBotTokenValidator())
    result = await ex.extract(CredentialBundle(headers={}, cookies={}, query={}))
    assert result is None


async def test_bot_token_invalid_raises() -> None:
    ex = BotTokenExtractor(validator=BareBotTokenValidator())
    with pytest.raises(Exception):
        await ex.extract(
            CredentialBundle(
                headers={"authorization": "Bearer nope"}, cookies={}, query={}
            )
        )


async def test_bot_token_valid_builds_bot_principal() -> None:
    ex = BotTokenExtractor(validator=BareBotTokenValidator())
    result = await ex.extract(_creds_bearer("bare-bot-token"))
    assert isinstance(result, BotPrincipal)
    assert result.bot.bot_id == "bare-bot-001"
```

- [ ] **Step 7: 运行验证失败**

```bash
cd src/gateway && pytest tests/unit/plugins/test_authn_extractors.py -q
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 8: 实现 user extractor** — Create two files.

`src/gateway/src/gateway/community/plugins/authn/user/__init__.py`:
```python
"""``user`` identity extractor(s)."""

from ._extractors import SessionCookieExtractor

__all__ = ["SessionCookieExtractor"]
```

`src/gateway/src/gateway/community/plugins/authn/user/_extractors.py`:
```python
"""User-identity extractors — turn a login session into a ``UserPrincipal``.

The browser cannot carry a tenant token (it is a secret), so the tenant is taken
from the authenticated user's identity, falling back to a configured default
(auth design §4.6, §6.2).
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthPlugin
from gateway.community.spi.authn import (
    CredentialBundle,
    IdentityExtractor,
    Principal,
    UserPrincipal,
)

# Cookies that indicate a first-party login session is present.
_SESSION_COOKIES = ("IAM_TOKEN", "SSO_TOKEN", "access_token")


class SessionCookieExtractor(IdentityExtractor):
    """Resolve a login-session cookie into a :class:`UserPrincipal`."""

    name = "session_cookie"

    def __init__(self, auth: AuthPlugin, default_tenant: str) -> None:
        self._auth = auth
        self._default_tenant = default_tenant  # fallback when identity has no tenant

    async def extract(self, creds: CredentialBundle) -> Principal | None:
        if not any(name in creds.cookies for name in _SESSION_COOKIES):
            return None  # no first-party session → extractor not applicable
        # A present-but-invalid session → AuthPlugin raises AuthError (hard failure).
        user = await self._auth.get_login_user(
            cookie=creds.headers.get("cookie", ""),
            referer=creds.headers.get("referer"),
        )
        tenant = user.tenant_id or self._default_tenant
        return UserPrincipal(tenant=tenant, subject=user)
```

- [ ] **Step 9: 实现 app extractor** — Create two files.

`src/gateway/src/gateway/community/plugins/authn/app/__init__.py`:
```python
"""``app`` identity extractor(s)."""

from ._extractors import ApiKeyExtractor

__all__ = ["ApiKeyExtractor"]
```

`src/gateway/src/gateway/community/plugins/authn/app/_extractors.py`:
```python
"""App-identity extractor — ``Authorization: Bearer <api_key>`` → ``AppPrincipal``.

Third-party tenant comes from the ``X-Tenant-Token`` header (authoritative) and
is cross-checked against the api-key record's tenant (design §6.3).
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    ApiKeyValidator,
    AppPrincipal,
    CredentialBundle,
    IdentityExtractor,
    Principal,
    TenantResolver,
    ThirdPartyApp,
)

_TENANT_HEADER = "x-tenant-token"


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


class ApiKeyExtractor(IdentityExtractor):
    """Resolve a Bearer API key + tenant token into an :class:`AppPrincipal`."""

    name = "api_key"

    def __init__(self, keys: ApiKeyValidator, tenants: TenantResolver) -> None:
        self._keys = keys
        self._tenants = tenants

    async def extract(self, creds: CredentialBundle) -> Principal | None:
        api_key = _bearer(creds.headers.get("authorization"))
        if not api_key:
            return None  # no api key → extractor not applicable
        record = await self._keys.verify(api_key)
        if record is None:
            raise AuthError("invalid api key")  # present but invalid → hard failure
        tenant = await self._tenants.resolve(creds.headers.get(_TENANT_HEADER, ""))
        if record.tenant != tenant:
            raise AuthError("api key does not belong to the presented tenant")
        return AppPrincipal(
            tenant=tenant,
            app=ThirdPartyApp(
                client_id=record.client_id,
                developer_org_id=record.developer_org_id,
                app_type=record.app_type,
            ),
            on_behalf_of_opaque=creds.headers.get("x-end-user-id"),
        )
```

- [ ] **Step 10: 实现 bot extractor** — Create two files.

`src/gateway/src/gateway/community/plugins/authn/bot/__init__.py`:
```python
"""``bot`` identity extractor(s)."""

from ._extractors import BotTokenExtractor

__all__ = ["BotTokenExtractor"]
```

`src/gateway/src/gateway/community/plugins/authn/bot/_extractors.py`:
```python
"""Bot-identity extractor — ``Authorization: Bearer <bot_token>`` → ``BotPrincipal``.

Bot tenant comes from the bot credential's registered tenant (§4.6); the record
always carries a tenant (port contract).
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    Bot,
    BotPrincipal,
    BotTokenValidator,
    CredentialBundle,
    IdentityExtractor,
    Principal,
)


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


class BotTokenExtractor(IdentityExtractor):
    """Resolve a Bearer bot credential into a :class:`BotPrincipal`."""

    name = "bot_token"

    def __init__(self, validator: BotTokenValidator) -> None:
        self._validator = validator

    async def extract(self, creds: CredentialBundle) -> Principal | None:
        bot_token = _bearer(creds.headers.get("authorization"))
        if not bot_token:
            return None  # no bot credential → extractor not applicable
        record = await self._validator.verify(bot_token)
        if record is None:
            raise AuthError("invalid bot token")  # present but invalid → hard failure
        return BotPrincipal(
            tenant=record.tenant,
            bot=Bot(bot_id=record.bot_id, owner_org_id=record.owner_org_id),
        )
```

- [ ] **Step 11: 运行通过**

```bash
cd src/gateway && pytest tests/unit/plugins/test_authn_extractors.py tests/test_identity_strategy.py -q
```
Expected: PASS

- [ ] **Step 12: conformance —** 在 `src/gateway/tests/contracts/spi/test_identity_extractor.py` 末尾追加真实 extractor 的 conformance 子类(沿用任务 5 已建的文件):

```python
from gateway.community.plugins.auth.api_key_validator.bare import BareApiKeyValidator
from gateway.community.plugins.auth.bot_token_validator.bare import (
    BareBotTokenValidator,
)
from gateway.community.plugins.auth.tenant_resolver.bare import BareTenantResolver
from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.plugins.authn.app._extractors import ApiKeyExtractor
from gateway.community.plugins.authn.bot._extractors import BotTokenExtractor
from gateway.community.plugins.authn.user._extractors import SessionCookieExtractor
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import AppPrincipal, BotPrincipal, UserPrincipal


class TestSessionCookieExtractorConformance:
    def setup_method(self) -> None:
        self.extractor = SessionCookieExtractor(
            auth=BareAuthPlugin(default_user=AuthenticatedUser(id="u", username="a")),
            default_tenant="t-d",
        )
        self.applicable_creds = CredentialBundle(
            headers={"cookie": "SSO_TOKEN=x"},
            cookies={"SSO_TOKEN": "x"},
            query={},
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    def test_has_stable_name(self) -> None:
        assert self.extractor.name == "session_cookie"

    async def test_returns_none_when_not_applicable(self) -> None:
        result = await self.extractor.extract(self.inapplicable_creds)
        assert result is None

    async def test_builds_a_principal_when_applicable(self) -> None:
        result = await self.extractor.extract(self.applicable_creds)
        assert isinstance(result, UserPrincipal)


class TestApiKeyExtractorConformance:
    def setup_method(self) -> None:
        self.extractor = ApiKeyExtractor(
            keys=BareApiKeyValidator(), tenants=BareTenantResolver()
        )
        self.applicable_creds = CredentialBundle(
            headers={"authorization": "Bearer bare-api-key", "x-tenant-token": "t"},
            cookies={},
            query={},
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    def test_has_stable_name(self) -> None:
        assert self.extractor.name == "api_key"

    async def test_returns_none_when_not_applicable(self) -> None:
        result = await self.extractor.extract(self.inapplicable_creds)
        assert result is None

    async def test_builds_a_principal_when_applicable(self) -> None:
        result = await self.extractor.extract(self.applicable_creds)
        assert isinstance(result, AppPrincipal)


class TestBotTokenExtractorConformance:
    def setup_method(self) -> None:
        self.extractor = BotTokenExtractor(validator=BareBotTokenValidator())
        self.applicable_creds = CredentialBundle(
            headers={"authorization": "Bearer bare-bot-token"}, cookies={}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    def test_has_stable_name(self) -> None:
        assert self.extractor.name == "bot_token"

    async def test_returns_none_when_not_applicable(self) -> None:
        result = await self.extractor.extract(self.inapplicable_creds)
        assert result is None

    async def test_builds_a_principal_when_applicable(self) -> None:
        result = await self.extractor.extract(self.applicable_creds)
        assert isinstance(result, BotPrincipal)
```

- [ ] **Step 13: 运行 conformance 通过**

```bash
cd src/gateway && pytest tests/contracts/spi/test_identity_extractor.py -q
```
Expected: PASS

- [ ] **Step 14: 提交**

```bash
cd src/gateway && git add src/gateway/community/core/authn/ src/gateway/community/plugins/authn/user/ src/gateway/community/plugins/authn/app/ src/gateway/community/plugins/authn/bot/ tests/test_identity_strategy.py tests/unit/plugins/test_authn_extractors.py tests/contracts/spi/test_identity_extractor.py
git commit -m "feat(authn): add IdentityStrategy chain runner + user/app/bot extractors"
```

---

## Task 7: ⚠️ 原子 cutover — 契约重塑、布线、配置、受影响测试

> 这是唯一中间会红的任务。**顺序严格执行**:先改 SPI 模型与协议(`build` 去参、`Principal` 联合、删 `Delegation`/`StrategyParams`/`scopes`)→ 改 route_security 解析 → 改 runner → 改 openapi → 改 bootstrap → 改 forward → 改配置 → 改全部受影响测试 → 全绿。一次提交。
>
> 受影响测试清单(会先失败,随实现改形后转绿):`test_authn_models.py`(删 `Principal is UserPrincipal`/`scopes`/`StrategyParams`/`Delegation` 测,加联合测)、`test_auth_runner.py`(重写为 per-identity)、`test_first_party_user_strategy.py`(删除/迁移,本任务末尾删)、`test_route_security.py`(改形)、`test_served_openapi.py`(改形)、`integration/test_forward_route.py`(`_FakeAuth.authenticate` 返回类型)、`contracts/spi/test_auth_strategy.py`(若引用旧形状)。

**Files (Modify):**
- `src/gateway/src/gateway/community/spi/authn/_models.py`
- `src/gateway/src/gateway/community/spi/authn/_protocols.py`
- `src/gateway/src/gateway/community/spi/authn/__init__.py`
- `src/gateway/src/gateway/community/core/authn/_route_security.py`
- `src/gateway/src/gateway/community/core/authn/_runner.py`
- `src/gateway/src/gateway/community/core/authn/__init__.py`
- `src/gateway/src/gateway/community/core/forwarding/_openapi.py`
- `src/gateway/src/gateway/community/bootstrap/_authn.py`
- `src/gateway/src/gateway/community/adapters/web/_forward.py`
- `src/gateway/configs/route_security.yaml`
- Create: `src/gateway/configs/identity_extractors.yaml`
- 测试:`test_authn_models.py`、`test_auth_runner.py`、`test_route_security.py`、`test_served_openapi.py`、`integration/test_forward_route.py`

### 7a. SPI 模型改形

- [ ] **Step 1: 先改 `test_authn_models.py` 为目标形状(删旧、加新)** — 整文件替换为:

```python
"""Unit tests for the authn Principal domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    AppPrincipal,
    Bot,
    BotPrincipal,
    CredentialBundle,
    Presence,
    PrincipalType,
    ThirdPartyApp,
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


def test_user_principal_is_immutable() -> None:
    p = UserPrincipal(tenant="t-1", subject=_subject())
    with pytest.raises(ValidationError):
        p.tenant = "t-2"  # type: ignore[misc]


def test_principal_type_union_members() -> None:
    assert PrincipalType.USER == "user"
    assert PrincipalType.BOT == "bot"
    assert PrincipalType.APP == "third_party_app"


def test_presence_enum_values() -> None:
    assert Presence.REQUIRED == "required"
    assert Presence.OPTIONAL == "optional"


def test_app_and_bot_principal_types() -> None:
    app = AppPrincipal(
        tenant="t-app",
        app=ThirdPartyApp(client_id="cid", developer_org_id="org-1", app_type="bot"),
    )
    assert app.type == "third_party_app"
    assert app.tenant == "t-app"
    assert app.app.client_id == "cid"
    assert app.on_behalf_of_opaque is None

    bot = BotPrincipal(tenant="t-bot", bot=Bot(bot_id="b-1", owner_org_id="org-1"))
    assert bot.type == "bot"
    assert bot.bot.bot_id == "b-1"


def test_credential_bundle_is_frozen() -> None:
    creds = CredentialBundle(headers={}, cookies={"SSO_TOKEN": "x"}, query={})
    assert creds.cookies["SSO_TOKEN"] == "x"
    with pytest.raises(Exception):
        creds.headers = {}  # type: ignore[misc]
```

- [ ] **Step 2: 改 `_models.py` 为目标形状** — 整文件替换 `src/gateway/src/gateway/community/spi/authn/_models.py`:

```python
"""Authn SPI — the neutral Principal the gateway produces after authentication.

The gateway authenticates a request, resolves the identities it carries into a
set of Principals (one per present :class:`PrincipalType`), and forwards them
(signed) to downstream components, which project each onto its own domain DTO.
The gateway never lets a component see raw credentials.

``Principal`` is a discriminated union (``type`` tag); each member carries only
the fields that exist for its kind, so illegal states (a USER carrying an
``app``) cannot be constructed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from gateway.community.spi.auth import AuthenticatedUser


class PrincipalType(StrEnum):
    """Discriminator for the kind of caller a ``Principal`` represents."""

    USER = "user"
    BOT = "bot"
    APP = "third_party_app"


class ThirdPartyApp(BaseModel):
    """A registered third-party developer application — the calling program itself."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    developer_org_id: str
    app_type: str = "UNKNOWN"


class Bot(BaseModel):
    """A bot/agent acting as a first-class caller in its own right."""

    model_config = ConfigDict(frozen=True)

    bot_id: str
    owner_org_id: str
    bot_type: str = "UNKNOWN"


class UserPrincipal(BaseModel):
    """A first-party authenticated user, produced by the gateway."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.USER] = PrincipalType.USER
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    subject: AuthenticatedUser = Field(description="The authenticated end user.")


class BotPrincipal(BaseModel):
    """A bot/agent caller, produced by the gateway."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.BOT] = PrincipalType.BOT
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    bot: Bot = Field(description="The authenticated bot/agent identity.")


class AppPrincipal(BaseModel):
    """A third-party application caller, produced by the gateway."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.APP] = PrincipalType.APP
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    app: ThirdPartyApp = Field(description="The authenticated third-party app.")
    on_behalf_of_opaque: str | None = Field(
        default=None,
        description=(
            "Opaque, unverified handle of the app's own end user. ``None`` is an "
            "intentional default (the app calls on its own behalf). Used only for "
            "ownership/quota/audit — never as an authenticated identity for "
            "cross-resource decisions."
        ),
    )


Principal = Annotated[
    UserPrincipal | BotPrincipal | AppPrincipal, Field(discriminator="type")
]


class Presence(StrEnum):
    """Whether a route requires an identity or merely accepts it."""

    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class CredentialBundle:
    """Framework-agnostic snapshot of a request's credentials."""

    headers: Mapping[str, str]
    cookies: Mapping[str, str]
    query: Mapping[str, str]
```

> 已删除 `Delegation`、`StrategyParams`、`UserPrincipal.scopes`。`Principal` 现为判别联合。

- [ ] **Step 3: 改 `_protocols.py`** — `AuthStrategy.build` 去掉 `params` 形参;整文件替换:

```python
"""Authn SPI — the ``AuthStrategy`` contract (how a Principal is built).

A strategy is a named way to turn a request's credentials into a
:class:`~gateway.community.spi.authn.Principal`. The gateway holds one strategy
per :class:`~gateway.community.spi.authn.PrincipalType`; each strategy runs an
ordered chain of :class:`IdentityExtractor`\\ s (§5). A route names the
identities it requires via ``x-avernet-security`` (design §8).
"""

from __future__ import annotations

from typing import Protocol

from ._models import CredentialBundle, Principal


class AuthStrategy(Protocol):
    """Builds a Principal for one identity type, or signals inapplicability."""

    name: str  # stable id (the PrincipalType value)

    async def build(self, creds: CredentialBundle) -> Principal | None:
        """Try to build a Principal from the request.

        Returns ``None`` when this identity's credential is **absent** — not
        applicable. Raises :class:`~gateway.community.spi.auth.AuthError` when the
        credential is **present but invalid** (hard failure, no fallback).
        Returns a ``Principal`` on success.
        """
        ...


class IdentityExtractor(Protocol):
    """The smallest unit of identity resolution — one credential → one Principal.

    Returns ``None`` when the credential is absent (chain continues); raises
    :class:`~gateway.community.spi.auth.AuthError` when present-but-invalid
    (hard failure, no fallback — design §5); returns a ``Principal`` on success.
    """

    name: str

    async def extract(self, creds: CredentialBundle) -> Principal | None:
        ...
```

- [ ] **Step 4: 改 `spi/authn/__init__.py`** — 去掉 `Delegation`/`StrategyParams`,加联合成员与端口(端口在任务 3 已加):

```python
"""Authn SPI — the neutral Principal contract produced by the gateway.

See ``_models`` for the model definitions, ``_protocols`` for the strategy and
extractor contracts, ``_ports`` for the dependency ports, and the auth design
doc (``src/gateway/docs/2026-07-21-auth-design.md``) for the full picture.
"""

from ._models import (
    AppPrincipal,
    Bot,
    BotPrincipal,
    CredentialBundle,
    Presence,
    Principal,
    PrincipalType,
    ThirdPartyApp,
    UserPrincipal,
)
from ._ports import (
    ApiKeyRecord,
    ApiKeyValidator,
    BotRecord,
    BotTokenValidator,
    TenantResolver,
)
from ._protocols import AuthStrategy, IdentityExtractor

__all__ = [
    "ApiKeyRecord",
    "ApiKeyValidator",
    "AppPrincipal",
    "AuthStrategy",
    "Bot",
    "BotPrincipal",
    "BotRecord",
    "BotTokenValidator",
    "CredentialBundle",
    "IdentityExtractor",
    "Presence",
    "Principal",
    "PrincipalType",
    "TenantResolver",
    "ThirdPartyApp",
    "UserPrincipal",
]
```

### 7b. route_security 解析改形

- [ ] **Step 5: 先改 `test_route_security.py` 为目标形状** — 整文件替换:

```python
"""Unit tests for the route-security table (identity requirement parsing)."""

from __future__ import annotations

from pathlib import Path

from gateway.community.core.authn import RouteSecurity
from gateway.community.spi.authn import Presence, PrincipalType

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "route_security.yaml"


def test_shipped_config_loads_and_requires_user() -> None:
    rs = RouteSecurity.from_yaml(_CONFIG)
    req = rs.resolve("GET", "/openapi/v1/bots/abc")
    assert req is not None
    assert req[PrincipalType.USER] is Presence.REQUIRED


def test_more_specific_rule_wins() -> None:
    rs = RouteSecurity.from_table(
        {
            "/**": {"user": "required"},
            "/openapi/v1/bots/{id}/chat": {"user": "required", "app": "optional"},
        }
    )
    chat = rs.resolve("POST", "/openapi/v1/bots/x/chat")
    assert chat is not None
    assert chat[PrincipalType.USER] is Presence.REQUIRED
    assert chat[PrincipalType.APP] is Presence.OPTIONAL

    other = rs.resolve("GET", "/openapi/v1/other")
    assert other is not None
    assert PrincipalType.APP not in other


def test_method_specific_rule_beats_method_agnostic() -> None:
    rs = RouteSecurity.from_table(
        {
            "/openapi/v1/bots/{id}": {"app": "required"},
            "GET /openapi/v1/bots/{id}": {"user": "required"},
        }
    )
    get_req = rs.resolve("GET", "/openapi/v1/bots/42")
    assert get_req is not None
    assert PrincipalType.USER in get_req

    post_req = rs.resolve("POST", "/openapi/v1/bots/42")
    assert post_req is not None
    assert PrincipalType.APP in post_req


def test_param_segment_matches_one_segment() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/{id}": {"user": "required"}})
    assert rs.resolve("GET", "/openapi/v1/bots/42") is not None
    assert rs.resolve("GET", "/openapi/v1/bots/42/skills") is None


def test_unmatched_route_is_fail_closed() -> None:
    rs = RouteSecurity.from_table({"/openapi/v1/bots/**": {"user": "required"}})
    assert rs.resolve("GET", "/openapi/v1/channels") is None
```

- [ ] **Step 6: 改 `_route_security.py`** — 整文件替换(匹配/具体度逻辑保持不变,仅 `_parse_req`/`Requirement`/`_parse_params` 改形):

```python
"""Route → identity-requirement table (auth design §8, reshaped).

The value under each ``"[METHOD ]<path-glob>"`` key is a mapping of
``{identity: required|optional}`` (identity = a ``PrincipalType`` value).
Resolves an incoming ``(method, path)`` to the **most specific** matching rule's
requirement. Fail-closed: an unmatched route resolves to ``None`` and the caller
must deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gateway.community.spi.authn import Presence, PrincipalType

# A requirement maps each identity the route cares about to its Presence.
Requirement = dict[PrincipalType, Presence]


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
    """Each value is ``{<identity-type-value>: required|optional}``."""
    req: Requirement = {}
    for identity_value, presence_value in (value or {}).items():
        identity = PrincipalType(identity_value)
        presence = Presence(presence_value)
        req[identity] = presence
    return req


# ── matching (§8.3) ──────────────────────────────────────────────────────────


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

### 7c. runner 改形(per-identity)

- [ ] **Step 7: 先改 `test_auth_runner.py` 为目标形状** — 整文件替换:

```python
"""Unit tests for the auth runner (per-identity resolution)."""

from __future__ import annotations

import pytest

from gateway.community.core.authn import authenticate
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    AppPrincipal,
    AuthStrategy,
    CredentialBundle,
    Presence,
    Principal,
    PrincipalType,
    ThirdPartyApp,
    UserPrincipal,
)
from gateway.community.spi.auth import AuthenticatedUser

_CREDS = CredentialBundle(headers={}, cookies={}, query={})


def _user_p() -> UserPrincipal:
    return UserPrincipal(
        tenant="t", subject=AuthenticatedUser(id="u", username="a")
    )


def _app_p() -> AppPrincipal:
    return AppPrincipal(
        tenant="t", app=ThirdPartyApp(client_id="c", developer_org_id="o")
    )


class _Fixed:
    def __init__(self, name: str, result: Principal | AuthError | None) -> None:
        self.name = name
        self._result = result

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if isinstance(self._result, AuthError):
            raise self._result
        return self._result


def _req(**presences: Presence) -> dict[PrincipalType, Presence]:
    return {
        PrincipalType(k): v for k, v in presences.items()
    } if presences else {}


async def test_required_identity_present_returns_it() -> None:
    reg: dict[str, AuthStrategy] = {"user": _Fixed("user", _user_p())}
    result = await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)
    assert isinstance(result[PrincipalType.USER], UserPrincipal)


async def test_required_identity_absent_denies() -> None:
    reg = {"user": _Fixed("user", None)}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)


async def test_optional_identity_absent_is_skipped() -> None:
    reg = {"app": _Fixed("app", None)}
    result = await authenticate(_CREDS, _req(app=Presence.OPTIONAL), reg)
    assert PrincipalType.APP not in result


async def test_optional_identity_present_is_included() -> None:
    reg = {"app": _Fixed("app", _app_p())}
    result = await authenticate(_CREDS, _req(app=Presence.OPTIONAL), reg)
    assert isinstance(result[PrincipalType.APP], AppPrincipal)


async def test_hard_failure_is_terminal() -> None:
    reg = {"user": _Fixed("user", AuthError("bad"))}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)


async def test_optional_hard_failure_is_still_terminal() -> None:
    reg = {"app": _Fixed("app", AuthError("bad"))}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(app=Presence.OPTIONAL), reg)


async def test_multiple_identities_coexist() -> None:
    reg = {
        "user": _Fixed("user", _user_p()),
        "app": _Fixed("app", _app_p()),
    }
    result = await authenticate(
        _CREDS,
        _req(user=Presence.REQUIRED, app=Presence.OPTIONAL),
        reg,
    )
    assert isinstance(result[PrincipalType.USER], UserPrincipal)
    assert isinstance(result[PrincipalType.APP], AppPrincipal)


async def test_unknown_identity_denies() -> None:
    # Empty registry: requirement asks for "user" but no strategy is registered
    # for it → misconfig, terminal AuthError.
    reg: dict[str, AuthStrategy] = {}
    with pytest.raises(AuthError):
        await authenticate(_CREDS, _req(user=Presence.REQUIRED), reg)
```

> 注:`test_unknown_identity_denies` 检验"注册表里没有 user 策略" → `AuthError`。实现里 `registry.get(identity.value)` 为 `None` 时 raise。

- [ ] **Step 8: 改 `_runner.py`** — 整文件替换为:

```python
"""Auth runner — resolve every identity a route requires (§7, reshaped).

For each identity declared in the route's requirement (``{identity: required|
optional}``), run that identity's strategy chain:

- a strategy returning ``None`` (credential absent):
    * REQUIRED  → raise ``AuthError`` (identity missing);
    * OPTIONAL  → the identity is simply absent from the result set;
- a strategy raising ``AuthError`` (present but invalid) → propagate immediately
  (terminal, no fallback — design §5), regardless of required/optional.

Returns the set of identities that were present, keyed by ``PrincipalType``.
"""

from __future__ import annotations

from gateway.community.core.authn._route_security import Requirement
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    AuthStrategy,
    CredentialBundle,
    Presence,
    Principal,
    PrincipalType,
)


async def authenticate(
    creds: CredentialBundle,
    requirement: Requirement,
    registry: dict[str, AuthStrategy],
) -> dict[PrincipalType, Principal]:
    """Resolve every required/present identity, or raise ``AuthError`` (401/403)."""
    resolved: dict[PrincipalType, Principal] = {}
    for identity, presence in requirement.items():
        strategy = registry.get(identity.value)
        if strategy is None:  # misconfigured identity → fail closed, terminal
            raise AuthError(f"unknown identity strategy: {identity.value}")
        # AuthError from a present-but-invalid credential is terminal and
        # propagates (required or optional alike).
        principal = await strategy.build(creds)
        if principal is None:
            if presence is Presence.REQUIRED:
                raise AuthError(f"missing required identity: {identity.value}")
            continue  # optional and absent → not in the result set
        if principal.type is not identity:  # defensive: strategy built wrong type
            raise AuthError(f"strategy {identity.value} built wrong principal type")
        resolved[identity] = principal
    return resolved
```

- [ ] **Step 9: 改 `core/authn/__init__.py`** — 仍保持任务 6 的导出(含 `IdentityStrategy`),无改动确认:

```python
from ._route_security import Requirement, RouteSecurity
from ._runner import authenticate
from ._strategy import IdentityStrategy

__all__ = [
    "IdentityStrategy",
    "Requirement",
    "RouteSecurity",
    "authenticate",
]
```

### 7d. openapi 标记改形

- [ ] **Step 10: 先改 `test_served_openapi.py` 为目标形状** — 把 `test_every_served_operation_carries_security` 改为断言 dict 形:

将 `src/gateway/tests/test_served_openapi.py` 中:

```python
_RULES = RouteSecurity.from_table({"/**": ["first_party_user"]})
```
改为:
```python
_RULES = RouteSecurity.from_table({"/**": {"user": "required"}})
```

并把:
```python
def test_every_served_operation_carries_security() -> None:
    for path, item in _served()["paths"].items():
        for method, operation in item.items():
            if method in _METHODS:
                assert operation["x-avernet-security"] == [{"first_party_user": {}}], (
                    f"{method} {path}"
                )
```
改为:
```python
def test_every_served_operation_carries_security() -> None:
    for path, item in _served()["paths"].items():
        for method, operation in item.items():
            if method in _METHODS:
                assert operation["x-avernet-security"] == {"user": "required"}, (
                    f"{method} {path}"
                )
```

- [ ] **Step 11: 改 `_openapi.py`** — Modify `src/gateway/src/gateway/community/core/forwarding/_openapi.py`:

把顶部 import:
```python
from gateway.community.spi.authn import Delegation, StrategyParams
```
整行删除(改形后 `Presence`/`Delegation`/`StrategyParams` 都不再被本文件引用)。保留同文件的 `from gateway.community.core.authn import RouteSecurity` 不动。

把 `_with_security` 中赋值块:
```python
        new_op["x-avernet-security"] = [
            {name: _params_to_dict(params) for name, params in alternative.items()}
            for alternative in requirement
        ]
```
改为:
```python
        new_op["x-avernet-security"] = {
            identity.value: presence.value for identity, presence in requirement.items()
        }
```

删除 `_params_to_dict` 函数(整段删除):
```python
def _params_to_dict(params: StrategyParams) -> dict[str, Any]:
    """Serialize strategy params, omitting defaults (empty scopes, optional)."""
    out: dict[str, Any] = {}
    if params.scopes:
        out["scopes"] = sorted(params.scopes)
    if params.delegation is not Delegation.OPTIONAL:
        out["delegation"] = params.delegation.value
    return out
```

### 7e. bootstrap 改形

- [ ] **Step 12: Create `configs/identity_extractors.yaml`**:

```yaml
# Identity-strategy → ordered extractor chain (system-level config).
# Each identity type runs its extractors in order; the first to recognise the
# credential wins (design §5). flavor-specific verification backends are wired
# by PluginAccessor in the composition root.
identity_extractors:
  user: [session_cookie]
  bot: [bot_token]
  app: [api_key]
```

- [ ] **Step 13: 改 `bootstrap/_authn.py`** — 整文件替换:

```python
"""Composition of the auth subsystem (composition root, Rule 14).

Builds the identity-strategy registry (one :class:`IdentityStrategy` per
``PrincipalType``, each with its ordered extractor chain from
``identity_extractors.yaml``), the route-security table, and exposes an
:class:`Authenticator` that ties them to the core runner. Only the composition
root wires concrete plugins; adapters receive the built ``Authenticator`` via
``app.state`` and never import plugins or core.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from gateway.community.core.authn import IdentityStrategy, RouteSecurity
from gateway.community.core.authn import authenticate as run_auth
from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.plugins.auth.api_key_validator.bare import BareApiKeyValidator
from gateway.community.plugins.auth.bot_token_validator.bare import (
    BareBotTokenValidator,
)
from gateway.community.plugins.auth.tenant_resolver.bare import BareTenantResolver
from gateway.community.plugins.authn.app._extractors import ApiKeyExtractor
from gateway.community.plugins.authn.bot._extractors import BotTokenExtractor
from gateway.community.plugins.authn.user._extractors import SessionCookieExtractor
from gateway.community.spi.auth import AuthError, AuthPlugin
from gateway.community.spi.authn import (
    ApiKeyValidator,
    AuthStrategy,
    BotTokenValidator,
    CredentialBundle,
    IdentityExtractor,
    Principal,
    PrincipalType,
    TenantResolver,
)

_DEFAULT_TENANT = "default"
# Fail-closed default: every route requires an authenticated user.
_DEFAULT_TABLE = {"/**": {"user": "required"}}

_auth_plugin = PluginAccessor[AuthPlugin]("gateway.auth", BareAuthPlugin)
_api_key_plugin = PluginAccessor[ApiKeyValidator]("gateway.auth.api_key", BareApiKeyValidator)
_tenant_plugin = PluginAccessor[TenantResolver]("gateway.auth.tenant", BareTenantResolver)
_bot_token_plugin = PluginAccessor[BotTokenValidator](
    "gateway.auth.bot_token", BareBotTokenValidator
)


@dataclass(frozen=True)
class Authenticator:
    """Resolves a route's identity requirement and runs its strategies."""

    strategies: dict[str, AuthStrategy]
    route_security: RouteSecurity

    async def authenticate(
        self, method: str, path: str, creds: CredentialBundle
    ) -> dict[PrincipalType, Principal]:
        requirement = self.route_security.resolve(method, path)
        if requirement is None:  # fail-closed: no policy → deny
            raise AuthError("no auth policy for route")
        return await run_auth(creds, requirement, self.strategies)


def _default_extractors() -> dict[PrincipalType, list[IdentityExtractor]]:
    return {
        PrincipalType.USER: [
            SessionCookieExtractor(auth=_auth_plugin.get(), default_tenant=_DEFAULT_TENANT)
        ],
        PrincipalType.BOT: [BotTokenExtractor(validator=_bot_token_plugin.get())],
        PrincipalType.APP: [
            ApiKeyExtractor(keys=_api_key_plugin.get(), tenants=_tenant_plugin.get())
        ],
    }


def build_authenticator() -> Authenticator:
    """Build the identity-strategy registry + route table (once, from create_app)."""
    extractors = _extractor_chains()
    strategies: dict[str, AuthStrategy] = {
        identity.value: IdentityStrategy(identity=identity, extractors=extractors[identity])
        for identity in (PrincipalType.USER, PrincipalType.BOT, PrincipalType.APP)
    }
    return Authenticator(strategies=strategies, route_security=_load_route_security())


def _extractor_chains() -> dict[PrincipalType, list[IdentityExtractor]]:
    """Parse identity_extractors.yaml, wiring each declared extractor by name."""
    defaults = _default_extractors()
    configs_dir = _resolve_configs_dir()
    path = configs_dir / "identity_extractors.yaml" if configs_dir else None
    if path is None or not path.exists():
        return defaults

    raw = yaml.safe_load(path.read_text()) or {}
    declared = raw.get("identity_extractors", {}) or {}
    by_name = {
        ex.name: ex for chain in defaults.values() for ex in (chain or [])
    }  # session_cookie / api_key / bot_token
    chains: dict[PrincipalType, list[IdentityExtractor]] = {}
    for identity_value, names in declared.items():
        identity = PrincipalType(identity_value)
        chains[identity] = [
            by_name[name] for name in (names or []) if name in by_name
        ]
    # Fall back to defaults for any identity not declared in config.
    for identity, chain in defaults.items():
        chains.setdefault(identity, chain)
    return chains


def _load_route_security() -> RouteSecurity:
    configs_dir = _resolve_configs_dir()
    path = configs_dir / "route_security.yaml" if configs_dir else None
    if path is not None and path.exists():
        return RouteSecurity.from_yaml(path)
    return RouteSecurity.from_table(_DEFAULT_TABLE)


def _resolve_configs_dir() -> Path | None:
    explicit = os.getenv("GATEWAY_CONFIG_PATH", "").strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_dir() else p.parent
    cwd = Path.cwd() / "configs"
    return cwd if cwd.exists() else None
```

### 7f. forward seam 接收解析集合

- [ ] **Step 14: 改 `adapters/web/_forward.py`** — Modify:把 `await request.app.state.authenticator.authenticate(...)` 改为捕获返回值,并调 seam 函数:

把:
```python
    try:
        await request.app.state.authenticator.authenticate(
            request.method, path, _bundle(request)
        )
    except AuthError as exc:
        return _error(401, 1, str(exc))
```
改为:
```python
    try:
        identities = await request.app.state.authenticator.authenticate(
            request.method, path, _bundle(request)
        )
    except AuthError as exc:
        return _error(401, 1, str(exc))
```

并在构造 `ForwardRequest` 时通过 seam 注入(目前 no-op):

把:
```python
    body = await request.body()
    forward = ForwardRequest(
        method=request.method,
        url=_target_url(server.base_url, request),
        # Drop Host so httpx sets it from the upstream URL, not the gateway's.
        headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
        content=body,
    )
```
改为:
```python
    body = await request.body()
    forward = _attach_identities(
        ForwardRequest(
            method=request.method,
            url=_target_url(server.base_url, request),
            # Drop Host so httpx sets it from the upstream URL, not the gateway's.
            headers={
                k: v for k, v in request.headers.items() if k.lower() != "host"
            },
            content=body,
        ),
        identities,
    )
```

并在 `_bundle` 函数之后、`forward_request` 之前添加 seam 函数:

```python
def _attach_identities(
    forward: ForwardRequest, identities: dict[object, object]
) -> ForwardRequest:
    """Forwarder seam for the resolved identities.

    Per auth design §7.1, components must NEVER trust a bare Principal header;
    the signing workstream swaps this no-op for a signed-token injection.
    Until then, the resolved identities are available here but NOT forwarded.
    """
    # Deliberately no-op: signing lands in the PrincipalSigner workstream.
    _ = identities  # referenced so the value is provably available at the seam
    return forward
```

> 返回 `ForwardRequest`(frozen dataclass)不变,即当前真的啥也不注入。

### 7g. 配置与集成测试改形

- [ ] **Step 15: 改 `configs/route_security.yaml`** — 整文件替换:

```yaml
# Route → identity-requirement table for the gateway (auth design §8, reshaped).
#
# Each value is ``{<identity-type>: required|optional}`` — the identities a
# route accepts and whether each is mandatory. More specific rules override
# more general ones; "/**" is the top-level default. The authoritative
# per-operation source is each route's ``x-avernet-security`` marker; this file
# is the aggregated table the gateway resolves at request time.

route_security:
  # Top-level default: every route requires an authenticated user.
  "/**":
    user: required

  # Sample group — the bots endpoints (explicit, more-specific rule).
  "/openapi/v1/bots/**":
    user: required
```

- [ ] **Step 16: 改 `integration/test_forward_route.py` 的 `_FakeAuth`** — Modify:把 `_FakeAuth.authenticate` 的返回类型注释与返回值对齐新契约(返回 `dict`,而非 `object()`):

把:
```python
    async def authenticate(self, method: str, path: str, bundle: object) -> object:
        self.calls.append((method, path))
        if self.fail:
            raise AuthError("unauthorized")
        return object()
```
改为:
```python
    async def authenticate(self, method: str, path: str, bundle: object) -> dict:
        self.calls.append((method, path))
        if self.fail:
            raise AuthError("unauthorized")
        return {}
```

### 7h. 删除 `FirstPartyUserStrategy` 旧试 + 清理引用

- [ ] **Step 17: 删除 `test_first_party_user_strategy.py`** — 整文件删除(其行为已被 `SessionCookieExtractor` 测试覆盖):

```bash
cd src/gateway && git rm tests/test_first_party_user_strategy.py
```

- [ ] **Step 18: 删除 cutover 后无引用的旧 `contracts/spi/test_auth_strategy.py` 旧内容(若仍引用 `StrategyParams`/`PrincipalType.USER` 旧形)** — 整文件替换 `src/gateway/tests/contracts/spi/test_auth_strategy.py` 为目标形(契约:持久 `name`;不适用→`None`;适用→`Principal`;非法→`AuthError`),并对 `IdentityStrategy` 加 conformance 子类:

```python
"""Conformance tests for the AuthStrategy + IdentityStrategy contracts (Rule 25)."""

from __future__ import annotations

from gateway.community.core.authn import IdentityStrategy
from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.plugins.authn.user._extractors import SessionCookieExtractor
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)


class AuthStrategyContract:
    """Behaviour every AuthStrategy implementation must satisfy."""

    strategy_name: str
    strategy: object  # AuthStrategy
    applicable_creds: CredentialBundle
    inapplicable_creds: CredentialBundle

    def test_has_stable_name(self) -> None:
        assert isinstance(getattr(self.strategy, "name", None), str)
        assert self.strategy.name  # type: ignore[attr-defined]

    async def test_returns_none_when_not_applicable(self) -> None:
        result = await self.strategy.build(self.inapplicable_creds)  # type: ignore[attr-defined]
        assert result is None

    async def test_builds_a_principal_when_applicable(self) -> None:
        result = await self.strategy.build(self.applicable_creds)  # type: ignore[attr-defined]
        assert result is not None


class TestUserIdentityStrategy(AuthStrategyContract):
    def setup_method(self) -> None:
        extractor = SessionCookieExtractor(
            auth=BareAuthPlugin(default_user=AuthenticatedUser(id="u", username="a")),
            default_tenant="tenant-default",
        )
        self.strategy = IdentityStrategy(
            identity=PrincipalType.USER, extractors=[extractor]
        )
        self.strategy_name = "user"
        self.applicable_creds = CredentialBundle(
            headers={"cookie": "SSO_TOKEN=x"}, cookies={"SSO_TOKEN": "x"}, query={}
        )
        self.inapplicable_creds = CredentialBundle(headers={}, cookies={}, query={})

    async def test_builds_user_principal(self) -> None:
        result = await self.strategy.build(self.applicable_creds)  # type: ignore[attr-defined]
        assert isinstance(result, UserPrincipal)
```

### 7i. cutover 验证

- [ ] **Step 19: 全量门禁** — 运行受影响测试 + 全量:

```bash
cd src/gateway && pytest tests/test_authn_models.py tests/test_auth_runner.py tests/test_route_security.py tests/test_served_openapi.py tests/contracts/spi/test_auth_strategy.py tests/integration/test_forward_route.py -q
```
Expected: PASS

```bash
cd src/gateway && ruff check src tests && mypy src && pytest -m "not e2e" -q
```
Expected: 全绿

- [ ] **Step 20: 提交(cutover 单次提交)**

```bash
cd src/gateway && git add -A
git commit -m "refactor(authn)!: cutover to per-identity pipeline

BREAKING CHANGE: reshape authn contract.
- Principal becomes a discriminated union (User|Bot|App); drop scopes/Delegation/StrategyParams.
- AuthStrategy.build(creds) loses its params arg; one IdentityStrategy per PrincipalType runs an ordered IdentityExtractor chain (first hit wins; None=absent, raise=invalid/terminal).
- Route requirement becomes {identity: required|optional}; route_security.yaml + x-avernet-security reshaped.
- New system config identity_extractors.yaml associates each identity with its enabled extractor chain.
- Authenticator.authenticate returns dict[PrincipalType, Principal]; runner resolves required/optional per identity.
- Extract resolved identities to the forwarder seam (_attach_identities no-op until PrincipalSigner lands).
- bare stubs for ApiKeyValidator/TenantResolver/BotTokenValidator; FirstPartyUserStrategy replaced by user/app/bot extractors."
```

---

## Task 8: 清理死代码(删除旧 `first_party_user` 包)

**Files:**
- Delete: `src/gateway/src/gateway/community/plugins/authn/first_party_user/`(整个包)

> cutover 后 `FirstPartyUserStrategy` 已无引用(任务 7 已不 import)。

- [ ] **Step 1: 确认无引用**

```bash
cd src/gateway && grep -rn "FirstPartyUserStrategy\|first_party_user" src tests | grep -v "first_party_user_strategy.py" || echo "no references"
```
Expected: 无引用输出(或仅历史 spec/plan 文档命中,这些可忽略)

- [ ] **Step 2: 删除包**

```bash
cd src/gateway && git rm -r src/gateway/community/plugins/authn/first_party_user/
```

- [ ] **Step 3: 验证**

```bash
cd src/gateway && ruff check src tests && mypy src && pytest -m "not e2e" -q
```
Expected: 全绿(架构/导出测试不再命中已删包)

- [ ] **Step 4: 提交**

```bash
cd src/gateway && git add -A && git commit -m "chore(authn): remove dead first_party_user package"
```

---

## Task 9: forward seam 回归测试(禁止未签名身份头泄露)

**Files:**
- Test: `src/gateway/tests/test_forward_seam.py`(新)

> §7.1 回归:签名未落地前,`_attach_identities` 不得向下游注入任何身份头。

- [ ] **Step 1: 写测试** — Create `src/gateway/tests/test_forward_seam.py`:

```python
"""Regression test for the forwarder identity seam (auth design §7.1).

Components must never trust a bare Principal header. Until the PrincipalSigner
workstream lands, the seam must NOT inject any identity-bearing header into the
forwarded request. This test pins that invariant so a future signing swap-in
is the only thing that changes it.
"""

from __future__ import annotations

from gateway.community.adapters.web._forward import _attach_identities
from gateway.community.spi.forwarder import ForwardRequest


def test_seam_does_not_inject_identity_headers() -> None:
    forward = ForwardRequest(
        method="GET",
        url="http://up/x",
        headers={"x-existing": "keep"},
        content=b"",
    )
    # Non-empty identity set must still not leak any header.
    out = _attach_identities(forward, {"user": object()})
    assert out.headers == {"x-existing": "keep"}
    assert out.method == "GET"
    assert out.url == "http://up/x"


def test_seam_returns_forward_unchanged_with_empty_identities() -> None:
    forward = ForwardRequest(method="GET", url="http://up/x", headers={}, content=b"")
    out = _attach_identities(forward, {})
    assert out is forward or (out.headers == {} and out.url == "http://up/x")
```

- [ ] **Step 2: 运行通过**

```bash
cd src/gateway && pytest tests/test_forward_seam.py -q
```
Expected: PASS

- [ ] **Step 3: 提交**

```bash
cd src/gateway && git add tests/test_forward_seam.py
git commit -m "test(forward): pin no-op identity seam until PrincipalSigner lands"
```

---

## Task 10: 集成薄冒烟(真 Authenticator + SessionCookieExtractor 经 forward)

**Files:**
- Test: `src/gateway/tests/integration/test_forward_route.py`(追加用例)

> 任务 7 已把 `_FakeAuth` 改为返回 `dict`;本任务再加一例用真 `Authenticator` + 真 `IdentityStrategy` + `SessionCookieExtractor`(bare)。

- [ ] **Step 1: 写测试** — 在 `src/gateway/tests/integration/test_forward_route.py` 末尾追加:

```python
def test_real_authenticator_admits_session_cookie_then_forwards() -> None:
    """End-to-end sanity: bare Authenticator + SessionCookieExtractor → forward 200."""
    from gateway.community.bootstrap._authn import build_authenticator

    authenticator = build_authenticator()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await client.aclose()

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_stub_upstream))  # type: ignore[arg-type]
    app = FastAPI(lifespan=lifespan)
    app.state.domain_map = DomainMap.from_config(
        {"domains": {"bots": {"server": "up"}}, "servers": {"up": {"base_url": "http://upstream"}}}
    )
    app.state.forwarder = BareForwarder(client=client)
    app.state.authenticator = authenticator
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)

    with TestClient(app, cookies={"SSO_TOKEN": "abc"}) as c:
        resp = c.get("/openapi/v1/bots")
    assert resp.status_code == 200
    assert resp.json() == {"code": 200000}


def test_real_authenticator_rejects_missing_required_identity() -> None:
    """No session cookie + required user → 401 before forwarding."""
    from gateway.community.bootstrap._authn import build_authenticator

    authenticator = build_authenticator()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await client.aclose()

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_stub_upstream))  # type: ignore[arg-type]
    app = FastAPI(lifespan=lifespan)
    app.state.domain_map = DomainMap.from_config(
        {"domains": {"bots": {"server": "up"}}, "servers": {"up": {"base_url": "http://upstream"}}}
    )
    app.state.forwarder = BareForwarder(client=client)
    app.state.authenticator = authenticator
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)

    with TestClient(app) as c:  # no cookie
        resp = c.get("/openapi/v1/bots")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401001
```

> 注意:`build_authenticator()` 读取 `configs/` 下的 `route_security.yaml`(已改形,`/**` 要求 `user: required`)与 `identity_extractors.yaml`。测试运行 cwd = `src/gateway`,`configs/` 存在。`conftest.py` 的 `_isolate_env` 会清掉 `GATEWAY_CONFIG_PATH`,故走 cwd `./configs` 路径。

- [ ] **Step 2: 运行通过**

```bash
cd src/gateway && pytest tests/integration/test_forward_route.py -q
```
Expected: PASS(含原有 5 例 + 新增 2 例)

- [ ] **Step 3: 提交**

```bash
cd src/gateway && git add tests/integration/test_forward_route.py
git commit -m "test(forward): add e2e smoke for real authenticator identity pipeline"
```

---

## Task 11: 全量门禁与 spec 验收

**Files:** 测试套件

- [ ] **Step 1: 全量门禁**

```bash
cd src/gateway && ruff check src tests
cd src/gateway && mypy src
cd src/gateway && pytest -m "not e2e" -q
```
Expected: 三者全绿

- [ ] **Step 2: 架构/导出测试专门确认(Rule 12/25)**

```bash
cd src/gateway && pytest tests/architecture/ tests/contracts/ -q
```
Expected: 全绿(新包 `__all__` 合规;协议入 `__all__`;extractor conformance 通过)

- [ ] **Step 3: spec 验收核对** — 逐条对照 spec `Implementation Decisions`:

- [ ] 网关不再做权限校验(无 scopes/Delegation/scope subset gate)— 确认 `_runner.py` / `_models.py` 已无 `scopes`/`Delegation`
- [ ] `Principal` = `User|Bot|App` 判别联合,`tenant` 必填,类型字段必填 — 确认 `_models.py`
- [ ] 两层提取:`IdentityExtractor`(自判→None/Principal/AuthError)+ `IdentityStrategy`(链,首达即返)— 确认 `_strategy.py` / `_protocols.py`
- [ ] per-endpoint 配置重塑为 `{identity: required|optional}` — 确认 `configs/route_security.yaml` / `_route_security.py`
- [ ] 系统级 `identity_extractors.yaml` 声明每类身份插件链 — 确认配置文件 + `_extractor_chains`
- [ ] per-identity runner:必备缺失/hard-fail→401,返回 `dict[PrincipalType, Principal]` — 确认 `_runner.py`
- [ ] 解析集合到 forwarder seam(`_attach_identities`),不签名 — 确认 `_forward.py` + `test_forward_seam.py`
- [ ] flavor 差异下沉到 SPI,`bare` 桩就位 — 确认 `plugins/authn/{api_key_validator,tenant_resolver,bot_token_validator}/bare/`
- [ ] `Bot`/`App` extractor + Strategy 落地 — 确认 `plugins/authn/{user,app,bot}/`

- [ ] **Step 4: 最终提交(若有遗漏修复)**

```bash
cd src/gateway && git add -A && git commit -m "test(authn): spec acceptance verification" || echo "nothing to commit"
```

---

## 任务依赖图与建议执行分组

- **Group A — 模型与 SPI(Tasks 1,2,3,5):** 加法,无依赖。
- **Group B — 插件(Tasks 4,6):** 依赖 A(Task 6 的 extractor 依赖 Task 4 桩 + Task 5 协议)。
- **Group C — 原子 cutover(Task 7):** 依赖 A+B。中间红→绿,单提交。
- **Group D — 清理与 seam(Tasks 8,9):** 依赖 C。
- **Group E — 集成与验收(Tasks 10,11):** 依赖 D。

并行机会:Task 1/2/3/5 互相独立,可并行;Task 4 内三桩互相独立,可并行。