# change-space-for-others 显式 env 订正能力 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `POST /api/bots/change-space-for-others` 支持显式传入目标数据环境 `env`，使预发实例可以订正 prod 行的 `ac_bots.space_id`；同时把失败原因计数补进审计日志。

**Architecture:** 自底向上单参数穿透。仓储方法加 `env: str | None = None` 关键字参数（默认回退 `get_current_env()`，所有既有调用零变化）；SpaceAccess/BotQuota/BotSpace 三个核心服务同模式透传；router 解析请求 `env` 字段、回显生效值、审计日志补 `env=` 与 `failed_reasons={}`。不新增平行方法、不复制业务规则。

**Tech Stack:** Python 3.12 / FastAPI + injector（DI）/ SQLAlchemy 仓储 / pytest（unit+integration）/ ruff。

**Spec:** `docs/superpowers/specs/2026-10-16-change-space-for-others-env-param-design.md`（同分支）。

**统一签名约定（所有任务一致）：** 凡涉及方法加的参数一律为关键字参数 `env: str | None = None`；方法体首行 `env = env or get_current_env()` 归一；仓储层归一是防御性的（`self.Model.env == (env or get_current_env())`）。

**基线:** `origin/REL20261015`。命令都在仓库根 `apps/backend` 下执行（`uv run pytest ...`）。

---

### Task 1: 仓储读写方法的显式 env（BotRepository）

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/repository/implementations/bot/bot.py:157-158`（`_env`）、`:168-185`（`get_by_id_and_owner`）、`:601-627`（`update_space_by_owner`）
- Modify: `apps/backend/src/agentclaw/community/core/repository/protocols/bot/bot.py:50`、`:281`
- Test: `apps/backend/tests/community/repository/bot/test_bot_repository_unified.py`（追加两位）

- [ ] **Step 1: 写失败测试**

在 `test_bot_repository_unified.py` 末尾追加（该文件的 `repo`/`db` fixture、`_data()` 帮手均已存在；文件顶部已 import `BotModel` 见既有用例 `test_get_env_scoped` 中的局部 import 方式）:

```python
# ── explicit-env read/write (admin cross-env correction) ──────────


def test_get_by_id_and_owner_explicit_env(repo, db):
    rec = repo.insert(_data())
    with repo._db.orm_session() as s:
        from agentclaw.community.plugin_api.models import BotModel

        s.query(BotModel).filter(BotModel.id == rec["id"]).update(
            {BotModel.env: "prod"}
        )
    # Runtime env (dev in tests) must no longer see the row …
    assert repo.get_by_id_and_owner("bot-1", "emp1") is None
    # … but the explicit target env does.
    assert repo.get_by_id_and_owner("bot-1", "emp1", env="prod")["bot_id"] == "bot-1"


def test_update_space_by_owner_explicit_env(repo, db):
    rec = repo.insert(_data())
    with repo._db.orm_session() as s:
        from agentclaw.community.plugin_api.models import BotModel

        s.query(BotModel).filter(BotModel.id == rec["id"]).update(
            {BotModel.env: "prod"}
        )
    updated = repo.update_space_by_owner(
        bot_id="bot-1", owner_id="emp1", space_id=9, env="prod"
    )
    assert updated["space_id"] == 9
    # The tail re-read must honor the explicit env too.
    assert repo.get_by_id_and_owner("bot-1", "emp1", env="prod")["space_id"] == 9
    assert repo.get_by_id_and_owner("bot-1", "emp1") is None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest tests/community/repository/bot/test_bot_repository_unified.py::test_get_by_id_and_owner_explicit_env tests/community/repository/bot/test_bot_repository_unified.py::test_update_space_by_owner_explicit_env -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'env'`

- [ ] **Step 3: 最小实现**

`implementations/bot/bot.py`。

`_env`（原 `def _env(self):`）:

```python
    def _env(self, env: Optional[str] = None):
        # Optional explicit env keeps admin cross-env corrections on the
        # same single business code path as every other caller.
        return self.Model.env == (env or get_current_env())
```

`get_by_id_and_owner` — 签名加 `env`，filter 用 `self._env(env)`:

```python
    def get_by_id_and_owner(
        self,
        bot_id: str,
        owner_id: str,
        *,
        execution_options: Optional[dict] = None,
        env: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            q = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self._env(env),
            )
            if execution_options:
                q = q.execution_options(**execution_options)
            bot = q.first()
            return bot.to_dict() if bot else None
```

`update_space_by_owner` — 签名加 `env`，filter 用 `self._env(env)`，**尾部 re-read 也必须传 env**（这是本任务最容易漏的一处）:

```python
    def update_space_by_owner(
        self,
        *,
        bot_id: str,
        owner_id: str,
        space_id: int,
        env: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        # … docstring 不变 …
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(env),
                )
                .update(
                    {
                        self.Model.space_id: space_id,
                        self.Model.modifier_id: owner_id,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
        if rowcount == 0:
            return None
        return self.get_by_id_and_owner(bot_id, owner_id, env=env)
```

`protocols/bot/bot.py:50` 的 `get_by_id_and_owner` 与 `:281` 的 `update_space_by_owner` 各自签名补 `env: str | None = None` 关键字参数（协议与实现完全同形）。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest tests/community/repository/bot/test_bot_repository_unified.py -v`
Expected: 全 PASS（含既有 env-scoping 用例不回归）

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/repository/implementations/bot/bot.py apps/backend/src/agentclaw/community/core/repository/protocols/bot/bot.py apps/backend/tests/community/repository/bot/test_bot_repository_unified.py
git commit -m "feat(backend): explicit env on bot repo read/space-write primitives"
```

---

### Task 2: 仓储配额计数的显式 env（quota.py 两个 count）

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/repository/implementations/bot/quota.py:11-17`（`count_cloud_bots_in_personal_space`）、`:35`（`count_cloud_bots_by_space`）
- Modify: `apps/backend/src/agentclaw/community/core/repository/protocols/bot/bot.py:327`、`:342`
- Test: `apps/backend/tests/community/repository/bot/test_bot_repository_unified.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_count_cloud_bots_by_space_explicit_env(repo, db):
    rec = repo.insert(_data())
    with repo._db.orm_session() as s:
        from agentclaw.community.plugin_api.models import BotModel

        s.query(BotModel).filter(BotModel.id == rec["id"]).update(
            {BotModel.env: "prod", BotModel.space_id: 5}
        )
    assert repo.count_cloud_bots_by_space(space_id=5, env="prod") == 1
    assert repo.count_cloud_bots_by_space(space_id=5) == 0


def test_count_cloud_bots_in_personal_space_explicit_env(repo, db):
    rec = repo.insert(_data(bot_type="personal"))
    with repo._db.orm_session() as s:
        from agentclaw.community.plugin_api.models import BotModel

        s.query(BotModel).filter(BotModel.id == rec["id"]).update(
            {BotModel.env: "prod", BotModel.space_id: 5}
        )
    assert (
        repo.count_cloud_bots_in_personal_space(
            owner_id="emp1", personal_space_id=5, env="prod"
        )
        == 1
    )
    assert repo.count_cloud_bots_in_personal_space(owner_id="emp1", personal_space_id=5) == 0
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest tests/community/repository/bot/test_bot_repository_unified.py -k "count_cloud_bots and explicit_env" -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'env'`

- [ ] **Step 3: 最小实现**

`implementations/bot/quota.py` 两个方法签名各加 `env: str | None = None`，filter 中 `self._env()` 改为 `self._env(env)`（`_env` 继承自 `BotRepository`，Task 1 已改）。`protocols/bot/bot.py:327/:342` 同形补参。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest tests/community/repository/bot/test_bot_repository_unified.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/repository/implementations/bot/quota.py apps/backend/src/agentclaw/community/core/repository/protocols/bot/bot.py apps/backend/tests/community/repository/bot/test_bot_repository_unified.py
git commit -m "feat(backend): explicit env on quota count primitives"
```

---

### Task 3: SpaceAccessService 的显式 env（require_space / require_space_member）

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/spaces/services/space_access_service.py:22-26`（`require_space`）、`:55-62`（`require_space_member`）
- Modify: `apps/backend/src/agentclaw/community/core/spaces/space_service_protocol.py:56-61`（`SpaceAccessServiceProtocol.require_space_member`）
- Test: `apps/backend/tests/community/core/spaces/test_space_access_service.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_require_space_and_member_use_explicit_env() -> None:
    repository = MagicMock()
    space = _space()
    member = _member()
    repository.get_space.return_value = space
    repository.get_member.return_value = member

    space_, member_ = SpaceAccessService(repository).require_space_member(
        space_id=7, user_id="member-1", env="prod"
    )

    assert space_ == space
    assert member_ == member
    repository.get_space.assert_called_once_with(space_id=7, env="prod")
    repository.get_member.assert_called_once_with(
        space_id=7, user_id="member-1", env="prod"
    )


def test_require_space_default_env_falls_back_to_runtime() -> None:
    from agentclaw.community.utils.env_utils import get_current_env

    repository = MagicMock()
    repository.get_space.return_value = _space()
    repository.get_member.return_value = _member()

    SpaceAccessService(repository).require_space_member(space_id=7, user_id="member-1")

    repository.get_space.assert_called_once_with(
        space_id=7, env=get_current_env()
    )
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest tests/community/core/spaces/test_space_access_service.py -k explicit_env or default_env -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'env'`

- [ ] **Step 3: 最小实现**

`space_access_service.py`:

```python
    def require_space(self, *, space_id: int, env: str | None = None):
        space = self._repository.get_space(
            space_id=space_id, env=env or get_current_env()
        )
        if space is None:
            raise SpaceNotFoundError("space not found")
        return space
```

（`require_space_reference` / `_team_space` 若复用 `require_space(space_id=...)` 调用**不用改**——默认参数保持既有行为。）

```python
    def require_space_member(
        self, *, space_id: int, user_id: str, env: str | None = None
    ):
        space = self.require_space(space_id=space_id, env=env)
        member = self._repository.get_member(
            space_id=space_id, user_id=user_id, env=env or get_current_env()
        )
        if member is None:
            raise SpaceAccessDeniedError("space membership required")
        return space, member
```

`space_service_protocol.py:58` 的 `require_space_member` 签名补 `env: str | None = None`。

注：`SpaceRepositoryProtocol.get_space/get_member` 本就显式收 env，仓储侧无改动。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest tests/community/core/spaces/test_space_access_service.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/spaces/services/space_access_service.py apps/backend/src/agentclaw/community/core/spaces/space_service_protocol.py apps/backend/tests/community/core/spaces/test_space_access_service.py
git commit -m "feat(backend): explicit env on space access checks"
```

---

### Task 4: BotQuotaService.guard_add 的 env 穿透（scope/inspect/locked/lock_key）

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/bot_quota_service_protocol.py:22-26`（`guard_add`）
- Modify: `apps/backend/src/agentclaw/community/core/spaces/protocols.py:19`（消费侧窄化协议 `SpaceAccessServiceProtocol.require_space`）、`:25`（`require_space_member`）——BotQuotaService 以它做类型标注，`_scope` 调 `require_space(..., env=…)` 需协议同步补 `env: str | None = None`
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/services/bot_quota_service.py:71-82`（`_inspect`）、`:95-118`（`guard_add`）、`:171-189`（`_scope`）、`:200-226`（`_locked`）、`:228-237`（`_lock_key`）
- Test: `apps/backend/tests/community/core/bot_management/services/test_bot_quota_service.py`（追加 + 修正 2 处既有计数断言）

**⚠️ 锁键是本任务的核心，不是细节**：`_lock_key` 含 `get_current_env()` 段，显式 env 必须换掉它，否则预发订正 prod 配额时锁键落在 `pre` 命名空间，与 prod 实例形成跨实例写竞争。

- [ ] **Step 1: 写失败测试 + 修既有断言**

追加：

```python
def test_guard_add_threads_explicit_env_to_scope_counts_and_lock_key():
    service, repository, _policy, _cache, access = _service()
    access.require_space.return_value = _space()
    repository.count_cloud_bots_by_space.return_value = 0

    with service.guard_add(owner_id="u1", space_id=42, env="prod") as snapshot:
        assert snapshot.ceiling == TEAM_DEFAULT_BOT_CEILING

    access.require_space.assert_called_once_with(space_id=42, env="prod")
    repository.count_cloud_bots_by_space.assert_called_once_with(
        space_id=42, env="prod"
    )


def test_guard_add_lock_key_uses_explicit_env():
    from hashlib import sha256

    from agentclaw.community.utils.tenant_utils import get_current_avernet_tenant

    service, repository, _policy, cache, _access = _service()
    repository.count_cloud_bots_by_space.return_value = 0

    with service.guard_add(owner_id="u1", space_id=42, env="prod"):
        pass
    with service.guard_add(owner_id="u1", space_id=42):
        pass

    keys = [call.args[0] for call in cache.acquire_lock_strict.call_args_list]
    assert keys[0] != keys[1]
    raw = "\0".join((get_current_avernet_tenant(), "prod", "space_quota:42"))
    assert keys[0] == f"bot-quota:{sha256(raw.encode('utf-8')).hexdigest()}"
```

> Note: `scope.lock_scope` 字符串格式以实实现为准（`_scope` 返回的 `BotQuotaScope.lock_scope` 生成处；先读 `bot_quota.py` 的 `BotQuotaScope` 定义，把测试里的 `"space_quota:42"` 替换成真实格式），断言意图 = 锁 key 必须随 env 变化。

既有断言修正（`_inspect` 计数现在会带 `env=get_current_env()`）：
- `test_personal_space_reuses_the_owner_ceiling_and_counts_legacy_rows`: `count_cloud_bots_in_personal_space.assert_called_once_with(owner_id="u1", personal_space_id=7, env=get_current_env())`（测试文件顶部补 `from agentclaw.community.utils.env_utils import get_current_env`）
- `test_team_space_defaults_to_twenty_and_counts_every_owner`: `count_cloud_bots_by_space.assert_called_once_with(space_id=42, env=get_current_env())`

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest tests/community/core/bot_management/services/test_bot_quota_service.py -v`
Expected: 新增两例 FAIL (`TypeError: unexpected keyword argument 'env'`)；两处修正后的既有断言 FAIL（收到 env= 而断言不同）

- [ ] **Step 3: 最小实现**

`bot_quota_service.py`（保持其它gente调用点零变化）：

```python
    def _inspect(self, scope: BotQuotaScope, *, env: str | None = None) -> BotQuotaSnapshot:
        ceiling = self._ceiling(scope)
        if scope.space_type is SpaceType.PERSONAL:
            used = self._repository.count_cloud_bots_in_personal_space(
                owner_id=scope.owner_id, personal_space_id=scope.space_id, env=env
            )
        else:
            if scope.space_id is None:
                raise BotQuotaConfigurationError("Team Space requires a numeric id")
            used = self._repository.count_cloud_bots_by_space(
                space_id=scope.space_id, env=env
            )
        return BotQuotaSnapshot(scope=scope, ceiling=ceiling, used=int(used))
```

（公开 `inspect()`/`assert_can_add()` 调 `_inspect(scope)` 不传 env——默认行为不变。）

```python
    def guard_add(
        self,
        *,
        owner_id: str,
        space_id: int | None,
        env: str | None = None,
    ) -> Iterator[BotQuotaSnapshot]:
        env = env or get_current_env()
        try:
            scope = self._scope(owner_id=owner_id, space_id=space_id, env=env)
        except BotQuotaError:
            raise
        except Exception as exc:
            raise BotQuotaUnavailableError("Bot quota state is unavailable") from exc
        with self._locked(scope, env=env):
            try:
                snapshot = self._assert_available(self._inspect(scope, env=env))
            except BotQuotaError:
                raise
            except Exception as exc:
                raise BotQuotaUnavailableError(
                    "Bot quota state is unavailable"
                ) from exc
            yield snapshot
```

```python
    def _scope(
        self, *, owner_id: str, space_id: int | None, env: str | None = None
    ) -> BotQuotaScope:
        # … space_id is None 分支不变 …
        space = self._space_access.require_space(space_id=space_id, env=env)
        # … 余下不变 …
```

```python
    @contextmanager
    def _locked(self, scope: BotQuotaScope, *, env: str | None = None) -> Iterator[None]:
        lock_key = self._lock_key(scope, env=env)
        # 余下不变（set/reset_team_ceiling 的 self._locked(scope) 调用无需改）
```

```python
    @staticmethod
    def _lock_key(scope: BotQuotaScope, *, env: str | None = None) -> str:
        raw = "\0".join(
            (
                get_current_avernet_tenant(),
                env or get_current_env(),
                scope.lock_scope,
            )
        )
        digest = sha256(raw.encode("utf-8")).hexdigest()
        return f"bot-quota:{digest}"
```

`bot_quota_service_protocol.py:22-26` `guard_add` 签名补 `env: str | None = None`。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest tests/community/core/bot_management/services/test_bot_quota_service.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/bot_management/bot_quota_service_protocol.py apps/backend/src/agentclaw/community/core/bot_management/services/bot_quota_service.py apps/backend/tests/community/core/bot_management/services/test_bot_quota_service.py
git commit -m "feat(backend): quota guard honors explicit env incl. lock key"
```

---

### Task 5: BotSpaceService.change_space 的 env 归一与穿透

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/bot_space_service_protocol.py:16-18`
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/bot_space.py:14`（`BotSpaceAccessProtocol.require_space_member` 补 `env: str | None = None`——BotSpaceService 以它标注 `_space_access`）
- Modify: `apps/backend/src/agentclaw/community/core/bot_management/services/bot_space_service.py:44-104`
- Test: `apps/backend/tests/community/core/bot_management/services/test_bot_space_service.py`（追加 + 修正 6 处既有断言）

- [ ] **Step 1: 写失败测试 + 修既有断言**

追加：

```python
def test_explicit_env_threaded_to_every_collaborator():
    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7}
    target = _space(space_id=42)
    service, repository, access, quota = _service(bot=bot, space=target)

    service.change_space(bot_id="b1", owner_id="u1", space_id=42, env="prod")

    repository.get_by_id_and_owner.assert_called_once_with(
        "b1", "u1", env="prod"
    )
    access.require_space_member.assert_called_once_with(
        space_id=42, user_id="u1", env="prod"
    )
    quota.guard_add.assert_called_once_with(
        owner_id="u1", space_id=42, env="prod"
    )
    repository.update_space_by_owner.assert_called_once_with(
        bot_id="b1", owner_id="u1", space_id=42, env="prod"
    )


def test_default_env_falls_back_to_runtime_everywhere():
    from agentclaw.community.utils.env_utils import get_current_env

    bot = {"bot_id": "b1", "bot_type": "personal", "space_id": 7}
    target = _space(space_id=42)
    service, repository, access, quota = _service(bot=bot, space=target)

    service.change_space(bot_id="b1", owner_id="u1", space_id=42)

    access.require_space_member.assert_called_once_with(
        space_id=42, user_id="u1", env=get_current_env()
    )
    repository.update_space_by_owner.assert_called_once_with(
        bot_id="b1", owner_id="u1", space_id=42, env=get_current_env()
    )
```

既有断言修正（`env=get_current_env()`，文件顶部补 import）：
- `test_teclaw_transfer_skips_quota_not_membership` 之后紧跟的 `access.require_space_member.assert_called_once_with(space_id=42, user_id="u1")`（两处，分属两个用例）→ 补 `env=get_current_env()`
- `test_moves_owned_cloud_bot_to_joined_team_space` / `test_moves_bot_back_to_owners_numeric_personal_space` / `test_normalizing_legacy_personal_space_does_not_consume_new_capacity` 中的 `repository.update_space_by_owner.assert_called_once_with(bot_id="b1", owner_id="u1", space_id=…)` → 补 `env=get_current_env()`
- `test_full_target_space_blocks_the_move_before_the_write` 中的 `quota.guard_add.assert_called_once_with(owner_id="u1", space_id=42)` → 补 `env=get_current_env()`

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest tests/community/core/bot_management/services/test_bot_space_service.py -v`
Expected: 新增两例 FAIL；修正过的既有断言 FAIL（实现尚未传 env）

- [ ] **Step 3: 最小实现**

`bot_space_service.py:44`（签名 + 归一 + 穿透；约束判断段不动）:

```python
    def change_space(
        self, *, bot_id: str, owner_id: str, space_id: int, env: str | None = None
    ) -> BotSpaceAssignmentResult:
        env = env or get_current_env()
        bot = self._repository.get_by_id_and_owner(bot_id, owner_id, env=env)
        if bot is None:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        space, _member = self._space_access.require_space_member(
            space_id=space_id,
            user_id=owner_id,
            env=env,
        )
        # … personal-space / desktop / teclaw 约束段原文不变 …
        # 分支内两次：
            updated = self._repository.update_space_by_owner(
                bot_id=bot_id,
                owner_id=owner_id,
                space_id=persisted_space_id,
                env=env,
            )
        # 配额分支：
            with self._bot_quota.guard_add(owner_id=owner_id, space_id=space.id, env=env):
                updated = (与上面同一调用)
        # … 余下不变 …
```

（顶部已 import `get_current_env`？该文件现在没有——补 `from agentclaw.community.utils.env_utils import get_current_env`。）

`bot_space_service_protocol.py:16-18`:

```python
    def change_space(
        self,
        *,
        bot_id: str,
        owner_id: str,
        space_id: int,
        env: str | None = None,
    ) -> BotSpaceAssignmentResult: ...
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest tests/community/core/bot_management/services/test_bot_space_service.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/bot_management/bot_space_service_protocol.py apps/backend/src/agentclaw/community/core/bot_management/services/bot_space_service.py apps/backend/tests/community/core/bot_management/services/test_bot_space_service.py
git commit -m "feat(backend): space change service normalizes target env once"
```

---

### Task 6: Router 的 env 入参、回显与审计增强

**Files:**
- Modify: `apps/backend/src/agentclaw/community/adapters/http/bot_management/router.py:944-1100`（`change_space_for_others`）
- Test: `apps/backend/tests/community/api/bot_management/test_router.py:2497-2712`（`TestChangeSpaceForOthers` 修 3 处既有断言 + 加 4 个用例）

- [ ] **Step 1: 写失败测试 + 修既有断言**

既有断言修正（router 现在每条 service 调用带 `env=…`）：
- `test_success_and_service_called_per_item` 中两处 `assert_any_call(bot_id="b1", owner_id="u1", space_id=123)` / `(bot_id="b2", …)` → 补 `env=None`
- `test_mixed_batch_reports_per_item_outcome` 中 `def _change(*, bot_id, owner_id, space_id):` → `def _change(*, bot_id, owner_id, space_id, env=None):`

新增用例（进 `TestChangeSpaceForOthers` 类）：

```python
    def test_env_threaded_to_service_and_echoed(self, space_admin_client):
        from agentclaw.community.utils.env_utils import get_current_env

        tc, space_svc = space_admin_client
        space_svc.change_space.return_value = _assignment(changed=True)
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [{"owner_id": "u1", "bot_id": "b1"}],
                "env": "prod",
            },
        )
        body = resp.json()
        assert body["success"] is True
        space_svc.change_space.assert_called_once_with(
            bot_id="b1", owner_id="u1", space_id=123, env="prod"
        )
        assert body["data"]["env"] == "prod"

    def test_env_blank_treated_as_absent(self, space_admin_client):
        from agentclaw.community.utils.env_utils import get_current_env

        tc, space_svc = space_admin_client
        space_svc.change_space.return_value = _assignment(changed=True)
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [{"owner_id": "u1", "bot_id": "b1"}],
                "env": "   ",
            },
        )
        body = resp.json()
        assert body["success"] is True
        space_svc.change_space.assert_called_once_with(
            bot_id="b1", owner_id="u1", space_id=123, env=None
        )
        assert body["data"]["env"] == get_current_env()

    def test_env_wrong_type_rejected(self, space_admin_client):
        tc, space_svc = space_admin_client
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [{"owner_id": "u1", "bot_id": "b1"}],
                "env": 123,
            },
        )
        assert resp.json()["error_code"] == 400
        space_svc.change_space.assert_not_called()

    def test_failed_reasons_appear_in_summary_log(
        self, space_admin_client, caplog
    ):
        import logging as _logging

        tc, space_svc = space_admin_client
        space_svc.change_space.side_effect = BotNotFoundError("Bot not found: b1")
        with caplog.at_level(_logging.INFO, logger="start"):
            tc.post(
                "/api/bots/change-space-for-others",
                json={
                    "space_id": 123,
                    "bot_list": [{"owner_id": "u1", "bot_id": "b1"}],
                },
            )
        assert any(
            "failed_reasons={'BotNotFound': 1}" in r.message
            for r in caplog.records
            if "change_space_for_others" in r.message
        )
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest tests/community/api/bot_management/test_router.py::TestChangeSpaceForOthers -v`
Expected: 新增 4 例 FAIL；修正的 2 处既有断言 FAIL

- [ ] **Step 3: 最小实现**

router 内 `change_space_for_others`：

入参解析（放在 `space_id`/`bot_list` 校验段之后）：

```python
        env_value = data.get("env")
        if env_value is not None:
            if not isinstance(env_value, str):
                return ApiResponse(
                    success=False,
                    message="参数错误: env 必须是字符串",
                    error_code=400,
                    data=None,
                )
            env_value = env_value.strip() or None
```

逐条循环内 service 调用：

```python
                assignment = space_service.change_space(
                    bot_id=bot_id, owner_id=owner_id, space_id=space_id,
                    env=env_value,
                )
```

失败原因计数（`results`/`succeeded` 初始化旁）：

```python
        failed_reasons: dict[str, int] = {}
```

逐分支计数（各 append 失败结果处同步 +1）：
- 必填缺失分支：`failed_reasons["MissingFields"] = failed_reasons.get("MissingFields", 0) + 1`
- `except BotNotFoundError`：同上 `…["BotNotFound"]…`
- `except (BotOperationNotAllowedError, SpaceError, BotQuotaError)`：`…["NotAllowed"]`（仅 `BotOperationNotAllowedError`）——**此处按异常类型分开**，把一个 except 拆为：

```python
            except BotOperationNotAllowedError as e:
                failed_reasons["NotAllowed"] = failed_reasons.get("NotAllowed", 0) + 1
                results.append({…原样…})
            except SpaceError as e:
                failed_reasons["SpaceError"] = failed_reasons.get("SpaceError", 0) + 1
                results.append({…原样…})
            except BotQuotaError as e:
                failed_reasons["Quota"] = failed_reasons.get("Quota", 0) + 1
                results.append({…原样…})
```

- `except Exception`（Unhandled）：同上 `…["Unhandled"]…`

> 注意 `BotQuotaError` 与 `SpaceError` 的继承关系需先核实：`BotQuotaError` 不是 `SpaceError` 的子类（`bot_quota.py` 独立定义）——若核实结果不同，按真实类型树排 except 顺序。

汇总行 + 响应：

```python
        effective_env = env_value or get_current_env()
        logger.info(
            f"[bot_router.change_space_for_others] space_id={space_id} "
            f"caller={caller_user_id}({caller_nick_name}) "
            f"env={effective_env} "
            f"total={len(results)} succeeded={succeeded} "
            f"failed={failed} failed_reasons={failed_reasons}"
        )
        return ApiResponse(
            success=True,
            message=f"批量变更完成: 成功 {succeeded} 个, 失败 {failed} 个",
            data={
                "space_id": space_id,
                "env": effective_env,
                "total": len(results),
                "succeeded": succeeded,
                "failed": failed,
                "results": results,
            },
        )
```

router 顶部 import 区补 `from agentclaw.community.utils.env_utils import get_current_env`。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest tests/community/api/bot_management/test_router.py -v`
Expected: 全 PASS（含本文件其余 169 个用例）

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/adapters/http/bot_management/router.py apps/backend/tests/community/api/bot_management/test_router.py
git commit -m "feat(backend): change-space-for-others explicit env + failure audit"
```

---

### Task 7: 端到端回归、ENDF 与 pre-PR 检查

**Files:** 无新改动（验证任务）

- [ ] **Step 1: 端到端框架用例**

Run: `uv run pytest tests/community/endpoints/test_change_space_for_others.py -v`
Expected: 全 PASS（ENDF 用例 run 真实 SQLite world、经全层到仓储，是对 env 穿透的最强回归；若 `ok_batch_moves_bots` 因链上某层没默认回退 env 失败，回到对应 Task 修）

- [ ] **Step 2: 全量相关面回归**

Run: `uv run pytest tests/community/api/bot_management/ tests/community/core/bot_management/ tests/community/core/spaces/ tests/community/repository/bot/ -q`
Expected: 全 PASS

- [ ] **Step 3: ruff**

Run: `uv run ruff check apps/backend/src/agentclaw/community/core/repository apps/backend/src/agentclaw/community/core/bot_management apps/backend/src/agentclaw/community/core/spaces apps/backend/src/agentclaw/community/adapters/http/bot_management tests/community/api/bot_management/test_router.py tests/community/repository/bot/test_bot_repository_unified.py`
Expected: 零 error

- [ ] **Step 4: changed-line coverage 预检（本仓口径）**

Run: `./ci_test.sh`（仓库根，跑之前 commit 全部改动）
Expected: changed-line coverage ≥ 80%
> 若门禁误报歧义，对照 merge-tree 口径：本地 REL ref 可能陈旧，用 `origin/REL20261015` 作 base 复现 PR 视角。

- [ ] **Step 5: push + PR**

```bash
git push -u origin feat/change-space-for-others-env-param
```

PR base = `REL20261015`（热修线；与 spec §9 一致）。标题格式参照仓库惯例：
`feat(backend): explicit target env for change-space-for-others`
PR 描述含 Problem/Solution/Validation 三段，并链接 spec 文档。
> pre-push gate 若因 mergeTarget 默认 <>
 origin/REL20261015 误报（本机 config 钉过 REL 的历史坑），推前 `env AVERNET_PRE_PUSH_MERGE_TARGET=origin/REL20261015 git push -u origin feat/change-space-for-others-env-param`。

- [ ] **Step 6: 收尾记录**

把 trace `0b446a4d17920666103644083efd55` 排障结论与本实现 PR 号记入工作记忆（env=prod 验证配方：预发 Postman 传 `"env": "prod"` 重放原请求即应对 20260729_oup3wzek 成功）。

---

## Self-Review 记录

- **Spec 覆盖**：§3 契约(T6)、§4 穿透(T3/4/5)、§5 仓储(T1/2)、§6 审计(T6)、§7 错误处理(T6 精确对应)、§8 测试(各 Task+T7)——全部有对应任务。
- **占位符扫描**：无 TBD/TODO；T4 测试里 `scope.lock_scope` 格式标注了核实指令（属实现细节核对，不是占位）。
- **类型一致性**：统一 `env: str | None = None` 关键字参数；`get_by_id_and_owner` 位置参 (bot_id, owner_id) 在 T5 服务层调用与 T6 router 层调用均一致。
