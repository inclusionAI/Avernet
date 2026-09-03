# OpenAPI template_config 快照透传 Implementation Plan

> **状态(2026-09-01,实施期)**:Task 4 的查询分发设计已被 REL #1785(`template_config_for_public` verbatim 决策)取代,该 commit 在 REL 落位时被跳过;查询面以 REL 现状为准。其余 Task 全部按计划落地。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `POST /openapi/v1/bots` 接收 template-factory 快照(`engine_properties.template_type + template_config`,与 available-tc-list item 逐字段对应),原样落库;三个查询面(bots/{id}/all)对工厂快照透传回显(减密钥);`template` 键改名 `template_config`。

**Architecture:** strategy 工厂分支判定与运行时消费(`consumes_template_config`)对齐(`template_key`+`template_uid` 双非空);查询投影在 `project_template_config_for_public` 内 dispatch(四键任一 → 透传投影),`_to_bot` 与 `/all` attach 零改动;密钥(token)加密走既有 `_encrypt_token_field` 门控,thetaKey 无后端加密入口、原样落库、查询剔除。

**Tech Stack:** Python 3.11+ / pydantic v2 / pytest / FastAPI(backend `src/backend`);网关 OpenAPI 契约 `src/gateway/configs/schemas/bots.openapi.json`(手工精准 patch,禁止全量 regen)+ ocb 仓库双写。

**设计 spec(权威):** `docs/superpowers/specs/2026-09-01-openapi-template-config-passthrough-design.md`

**测试跑法(全部命令在仓库根 `/Users/rongzhi/PycharmProjects/Avernet` 下执行):**

- 单文件单测:`cd src/backend && uv run pytest tests/community/<path> -v`
- 变更行覆盖率 gate(推 PR 前必跑):`bash src/backend/scripts/ci_test.sh`(本地自动推导 base ref,与 CI 同阈值)
- gateway 契约:`cd src/gateway && uv run pytest tests -k "bots" -v`

**执行约定(用户已拍板):** 每 Task 实现+测试,全部完成后**一次批量终审**(不做每 Task 双重审查);分支从 `origin/dev` 新开 `feat/openapi-template-config-passthrough`。

---

### Task 1: strategy.prepare_create 键域迁移 + 工厂快照分支

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_management/engines/aicoding/strategy.py:150-237`(`prepare_create`)
- Test: `src/backend/tests/community/core/bot_management/test_application_coding_create.py`

- [ ] **Step 1: 迁移既有测试键名(`"template"` → `"template_config"`)并写新工厂用例(RED)**

在 `test_application_coding_create.py` 里,把所有调用 `_strategy_prepare` / `BotCreateSpec(engine_properties=...)` / 断言里的字面量 `{"template": ...}` 全局替换为 `{"template_config": ...}`(文件内约 20 处;`create_flow` 包装用例见 Task 2,本 Task 只改 strategy 直调的)。然后在文件尾部追加以下用例(完整代码):

```python
# ── template-factory snapshot passthrough (v3 contract) ─────────────────────

_FACTORY_SNAPSHOT = {
    "template_key": "applicationCoding",
    "template_uid": "aicoding_bot_template",
    "template_version": "V1",
    "template_version_id": 2800006,
    "template_name": "应用 Bot",
    "image": "reg.antgroup-inc.cn/aixcoding/aixcoding-arca:20260901140138",
    "resource_spec": {"cpu": "4", "memory": "8g", "disk": "50"},
    "envs": {"AIX_SKIP_DAEMON": "false"},
    "capabilities": {"channel_management": False},
    "bot_template_config": {"id": 2800006, "custom_field_config": {}},
    "custom_field_values": {"field_a": "value_a"},
}


def test_factory_snapshot_passthrough_keeps_type_and_config_verbatim() -> None:
    prepared = _strategy_prepare(
        "claude_code",
        {
            "template_type": "applicationCoding",
            "template_config": dict(_FACTORY_SNAPSHOT),
        },
    )
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config == _FACTORY_SNAPSHOT
    # requires_workspace_hosting 与老 TC 直传链路一致:工厂模板不加 hosting 门槛
    assert prepared.requires_workspace_hosting is not True


def test_factory_snapshot_accepts_any_template_type_value() -> None:
    snapshot = dict(_FACTORY_SNAPSHOT, template_key="userCustomTemplate")
    prepared = _strategy_prepare(
        "claude_code",
        {"template_type": "custom_xxx", "template_config": snapshot},
    )
    assert prepared.template_type == "custom_xxx"
    assert prepared.template_config == snapshot


def test_factory_snapshot_requires_declared_template_type() -> None:
    with pytest.raises(BotTemplateInvalidError) as excinfo:
        _strategy_prepare(
            "claude_code", {"template_config": dict(_FACTORY_SNAPSHOT)}
        )
    assert "template_type" in str(excinfo.value)


def test_factory_snapshot_rejects_plain_template_type() -> None:
    with pytest.raises(BotTemplateInvalidError):
        _strategy_prepare(
            "claude_code",
            {
                "template_type": " ",
                "template_config": dict(_FACTORY_SNAPSHOT),
            },
        )


def test_factory_snapshot_rejects_handcrafted_field_mix() -> None:
    mixed = dict(_FACTORY_SNAPSHOT, devflow_workflow="app-flow")
    with pytest.raises(BotTemplateInvalidError) as excinfo:
        _strategy_prepare(
            "claude_code",
            {"template_type": "applicationCoding", "template_config": mixed},
        )
    assert "devflow_workflow" in str(excinfo.value)


def test_factory_snapshot_public_mode_rejects_server_managed_fields() -> None:
    polluted = dict(_FACTORY_SNAPSHOT, engine_form="aicoding")
    with pytest.raises(BotTemplateInvalidError):
        _strategy_prepare(
            "claude_code",
            {"template_type": "applicationCoding", "template_config": polluted},
            template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
        )


def test_factory_snapshot_keeps_factory_marker_keys_in_public_mode() -> None:
    # template_key/template_uid/version 键不在 RESERVED 清单,PUBLIC 模式放行
    prepared = _strategy_prepare(
        "claude_code",
        {"template_type": "applicationCoding", "template_config": dict(_FACTORY_SNAPSHOT)},
        template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
    )
    assert prepared.template_config["template_uid"] == "aicoding_bot_template"


def test_factory_snapshot_gates_match_application_coding() -> None:
    props = {"template_type": "architect", "template_config": dict(_FACTORY_SNAPSHOT)}
    with pytest.raises(BotCombinationUnsupportedError):
        _strategy_prepare("claude_code", props, deployment_mode="local")
    with pytest.raises(BotCombinationUnsupportedError):
        _strategy_prepare("openclaw", props)
    with pytest.raises(BotCombinationUnsupportedError):
        _strategy_prepare("claude_code", props, bot_type="service")
    with pytest.raises(BotCombinationUnsupportedError):
        _strategy_prepare("claude_code", props, space_kind="team")


def test_handcrafted_path_rejects_foreign_template_type() -> None:
    with pytest.raises(BotTemplateInvalidError):
        _strategy_prepare(
            "claude_code",
            {"template_type": "architect", "template_config": {"devflow_workflow": "x"}},
        )


def test_engine_properties_with_template_key_only_stays_handcrafted() -> None:
    # 不完整快照(缺 template_uid)→ 走手填路径,工厂键按未知键存活(现状)
    prepared = _strategy_prepare(
        "claude_code",
        {
            "template_config": {
                "template_key": "applicationCoding",
                "devflow_workflow": "x",
            }
        },
    )
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config["template_key"] == "applicationCoding"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run pytest tests/community/core/bot_management/test_application_coding_create.py -v`
Expected: FAIL —— 迁移后的旧用例报 `unsupported engine_properties fields: ['template']` 或 `engine_properties.template_config is required`;新工厂用例报 template_type/template 未按工厂处理。

- [ ] **Step 3: 重写 `prepare_create`(GREEN)**

`strategy.py` — 先加模块级常量(放在 `CODING_TEMPLATE_TYPES` 定义附近):

```python
#: Public ``engine_properties`` envelope keys (v3 contract). The single
#: implementation behind the hand-written application-coding shape and the
#: template-factory snapshot passthrough shape.
_ENGINE_PROPERTIES_KEYS = frozenset({"template_type", "template_config"})
_HANDCRAFTED_MIX_REJECT_KEYS = (
    "devflow_workflow",
    "yuque_kb_repos",
    "code_repos",
)
```

然后用下面的完整新实现替换 `prepare_create` 的方法体(方法签名不变;原 150-237 整段替换):

```python
    def prepare_create(
        self,
        *,
        engine_type: str,
        engine_properties: Dict[str, Any],
        bot_type: str,
        deployment_mode: str,
        space_kind: str,
        template_validation_mode: BotCreateTemplateValidationMode = (
            BotCreateTemplateValidationMode.LEGACY
        ),
    ) -> PreparedBotCreate:
        """Parse and validate coding create input (single owner).

        Two input shapes: the public ``engine_properties.template_config``
        hand-written application-coding config, and the template-factory
        snapshot passthrough (identified by full template identity:
        ``template_key`` + ``template_uid``, aligned with
        ``consumes_template_config``). Combination gates keep their
        historical order, error types and messages so the HTTP mappings
        answer identically; server-managed-field rejection follows the
        caller's validation mode.
        """
        if not engine_properties:
            return PreparedBotCreate()

        # Envelope integrity for keys this engine owns. The public schema's
        # ``extra="forbid"`` cannot guard direct Core-level spec construction,
        # so unknown keys fail here instead of being silently ignored.
        unknown_keys = set(engine_properties) - _ENGINE_PROPERTIES_KEYS
        if unknown_keys:
            raise BotTemplateInvalidError(
                f"unsupported engine_properties fields: {sorted(unknown_keys)}"
            )
        if "template_config" not in engine_properties:
            raise BotTemplateInvalidError(
                "engine_properties.template_config is required"
            )
        declarative_type = engine_properties.get("template_type")

        # Historical combination gates, in their historical order. The gate set
        # and messages are mirrored (production-dead) in
        # ``bot_inventory/policies/combo_policy.py``
        # ``assert_application_coding_create`` — keep the two in sync, or
        # single-source them once bot_management may depend on bot_inventory.
        if deployment_mode != "cloud":
            raise BotCombinationUnsupportedError("application coding is cloud-only")
        if engine_type != CLAUDE_CODE_ENGINE_TYPE:
            # The strategy class is registered for both engine types, but
            # application-coding creation stays claude_code-only.
            raise BotCombinationUnsupportedError(
                f"application coding does not support engine: {engine_type}"
            )
        if bot_type != "personal":
            raise BotCombinationUnsupportedError(
                "application coding bot must be personal"
            )
        if space_kind != "personal":
            raise BotCombinationUnsupportedError(
                "application coding is personal-space only"
            )

        template = engine_properties["template_config"]
        if self._is_factory_snapshot(template):
            return self._prepare_factory_snapshot(
                declarative_type=declarative_type,
                template=template,
                template_validation_mode=template_validation_mode,
            )

        if declarative_type is not None and declarative_type != "applicationCoding":
            # Non-factory inputs may only declare the legacy type; anything
            # else must come through a factory snapshot instead.
            raise BotTemplateInvalidError(
                "engine_properties.template_type must be applicationCoding "
                "for non factory snapshots"
            )
        if template is None:
            # Core-only legacy compatibility shape: the key's presence is the
            # application-coding intent, ``None`` the intentionally-omitted
            # config. The public schema requires a non-empty dict, so callers
            # cannot express this through HTTP.
            return PreparedBotCreate(
                template_type="applicationCoding",
                template_config=None,
                requires_workspace_hosting=True,
            )
        sanitized = to_internal_template_config(
            template,
            reject_server_managed_fields=(
                template_validation_mode is BotCreateTemplateValidationMode.PUBLIC
            ),
        )
        return PreparedBotCreate(
            template_type="applicationCoding",
            template_config=_validate_application_coding_config(sanitized),
            requires_workspace_hosting=True,
        )

    @staticmethod
    def _is_factory_snapshot(template: Any) -> bool:
        """Full template identity, matching ``consumes_template_config``.

        ``template_key`` AND ``template_uid`` both non-empty: a snapshot with
        only scattered factory keys stays on the hand-crafted path (factory
        keys survive as unknown keys) so creation perception never diverges
        from runtime consumption.
        """
        if not isinstance(template, Mapping):
            return False
        return bool(template.get("template_key") and template.get("template_uid"))

    def _prepare_factory_snapshot(
        self,
        *,
        declarative_type: Any,
        template: Any,
        template_validation_mode: BotCreateTemplateValidationMode,
    ) -> PreparedBotCreate:
        """Validate and pass through a template-factory snapshot verbatim.

        Passthrough contract: the caller (available-tc-list consumer) owns
        the snapshot semantics; we only enforce identity completeness, the
        no-mix rule against hand-written keys, and (in PUBLIC mode) the
        server-managed-field ownership rules. No ``template_type`` value
        validation — the factory vocabulary is open.
        """
        if not isinstance(template, dict) or not template:
            raise BotTemplateInvalidError(
                "applicationCoding template_config must not be empty"
            )
        if not (isinstance(declarative_type, str) and declarative_type.strip()):
            raise BotTemplateInvalidError(
                "engine_properties.template_type is required for template "
                "factory snapshots"
            )
        mixed = [key for key in _HANDCRAFTED_MIX_REJECT_KEYS if key in template]
        if mixed:
            raise BotTemplateInvalidError(
                "template factory snapshot must not mix application-coding "
                f"fields: {sorted(mixed)}"
            )
        return PreparedBotCreate(
            template_type=declarative_type,
            template_config=to_internal_template_config(
                template,
                reject_server_managed_fields=(
                    template_validation_mode
                    is BotCreateTemplateValidationMode.PUBLIC
                ),
            ),
            # No workspace-hosting gate: aligned with the legacy TC direct
            # path (create_flow generic branch), NOT the applicationCoding
            # DIMA hosting requirement.
        )
```

注意:原方法里 `if not template:`(空 dict 拒绝)分支在手填路径被吸收进 `_validate_application_coding_config`(空 dict 返回时它报"must not be empty")——保持:`sanitized` 为空 dict 时 `_validate_application_coding_config({})` 抛"applicationCoding template_config must not be empty"。上面的手填路径删除了独立的 `if not template:` 检查,确认空 dict 场景仍被 `_validate_application_coding_config` 覆盖(它的第一支就是 `if not value: raise`)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && uv run pytest tests/community/core/bot_management/test_application_coding_create.py -v`
Expected: PASS(全部)。若有失败,先看失败信息是否来自 create_flow 包装用例(Task 2 处理),strategy 直调用例必须全绿再继续。

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_management/engines/aicoding/strategy.py \
        src/backend/tests/community/core/bot_management/test_application_coding_create.py
git commit -m "feat(backend): accept template-factory snapshots in engine_properties"
```

---

### Task 2: create_flow legacy 包装键名迁移

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_management/create_flow.py:322`
- Test: `src/backend/tests/community/core/bot_management/test_application_coding_create.py`(routing 用例)

- [ ] **Step 1: 确认/补 routing 用例(RED)**

`test_application_coding_create.py` 已有 create_flow routing 用例(`_prepare_spec` + `_application_coding_spec`)。确认 Task 1 的迁移已把它们的 engine_properties 断言改为 `{"template_config": ...}`;若没有显式断言包装形态,追加:

```python
def test_legacy_application_coding_pair_is_wrapped_as_template_config() -> None:
    prepared = _prepare_spec(_application_coding_spec())
    # create_flow 把 legacy pair 归一成 strategy 的 v3 键域后消费
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config == {"devflow_workflow": "x"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run pytest tests/community/core/bot_management/test_application_coding_create.py -v`
Expected: routing 用例 FAIL(`unsupported engine_properties fields: ['template']` —— create_flow:322 还在包 `{"template": ...}`)。

- [ ] **Step 3: 改 create_flow.py:322 一个词**

```python
        prepared = _prepare_with_engine_strategy(
            replace(
                spec, engine_properties={"template_config": spec.template_config}
            ),
            context,
        )
```

(原来是 `engine_properties={"template": spec.template_config}`;`comment 318-320 里的 “‘template’ key's presence” 改为 “‘template_config’ key's presence”。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && uv run pytest tests/community/core/bot_management/test_application_coding_create.py -v`
Expected: PASS 全绿。

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_management/create_flow.py \
        src/backend/tests/community/core/bot_management/test_application_coding_create.py
git commit -m "refactor(backend): wrap legacy application-coding pair as template_config"
```

---

### Task 3: HTTP schema 键名迁移 + 端到端用例

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/schemas.py:176-187`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py`(`_engine_properties_from_body` 无需改——`model_dump(exclude_none=True)` 自动跟随 pydantic 键名)
- Test: `src/backend/tests/community/adapters/http/openapi_v1/test_bots_endpoints.py`

- [ ] **Step 1: 迁移 HTTP 测试键名 + 新用例(RED)**

`test_bots_endpoints.py` 里所有 `"engine_properties": {"template": ...}`(约 680-1000 行区间,含 poll 面 945-974)改为 `"engine_properties": {"template_config": ...}`;`test_create_rejects_unknown_engine_properties_fields`(764)的 body 改成:

```python
            "engine_properties": {
                "template_config": {},
                "template_uid": "caller-controlled",
            },
```

`test_create_schema_nests_template_under_engine_properties`(1388-1402)改名 `test_create_schema_nests_template_config_under_engine_properties`,断言改:

```python
    assert set(engine_properties) == {"template_type", "template_config"}
    assert engine_properties["template_config"]["required"] == ["template_config"]
```

注:这条断言等 Task 5 的 gateway JSON patch 后才真正绿;本 Task 先改断言,Task 5 patch 后统一复验(Step 4 的运行命令先跑不含它的选择:`-k "not schema_nests"`)。

在 create 用例区追加(完整代码):

```python
_FACTORY_SNAPSHOT_BODY = {
    "template_type": "applicationCoding",
    "template_config": {
        "template_key": "applicationCoding",
        "template_uid": "aicoding_bot_template",
        "template_version": "V1",
        "template_version_id": 2800006,
        "template_name": "应用 Bot",
        "image": "reg.antgroup-inc.cn/aixcoding/arca:20260901140138",
        "resource_spec": {"cpu": "4", "memory": "8g", "disk": "50"},
        "envs": {"AIX_SKIP_DAEMON": "false"},
        "capabilities": {"channel_management": False},
        "bot_template_config": {"id": 2800006},
        "custom_field_values": {"field_a": "value_a"},
    },
}


def test_create_factory_snapshot_passthrough_persists_verbatim(
    client, svc, passport
):
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac",
    }
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        response = client.post(
            "/openapi/v1/bots",
            json={
                **_CREATE_BODY,
                "engine": "claude_code",
                "engine_properties": dict(_FACTORY_SNAPSHOT_BODY),
            },
        )
    assert response.status_code == 201, response.json()
    kwargs = svc.create_bot.call_args.kwargs
    assert kwargs["template_type"] == "applicationCoding"
    assert kwargs["template_config"] == _FACTORY_SNAPSHOT_BODY["template_config"]


def test_create_factory_snapshot_missing_template_type_is_422(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {
                "template_config": _FACTORY_SNAPSHOT_BODY["template_config"]
            },
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_factory_snapshot_server_managed_field_is_422(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {
                **_FACTORY_SNAPSHOT_BODY,
                "template_config": {
                    **_FACTORY_SNAPSHOT_BODY["template_config"],
                    "engine_form": "aicoding",
                },
            },
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_factory_snapshot_mix_is_422(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {
                **_FACTORY_SNAPSHOT_BODY,
                "template_config": {
                    **_FACTORY_SNAPSHOT_BODY["template_config"],
                    "devflow_workflow": "x",
                },
            },
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_handcrafted_with_foreign_template_type_is_422(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {
                "template_type": "architect",
                "template_config": {"devflow_workflow": "x"},
            },
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_rejects_legacy_template_key_name(client, svc):
    # 改名反向回归:v1 契约的 "template" 键已不存在
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "engine_properties": {"template": {"devflow_workflow": "x"}},
        },
    )
    assert response.status_code == 422, response.json()
    svc.create_bot.assert_not_called()


def test_create_factory_snapshot_for_service_bot_is_409(client, svc):
    response = client.post(
        "/openapi/v1/bots",
        json={
            **_CREATE_BODY,
            "engine": "claude_code",
            "bot_type": "service",
            "engine_properties": dict(_FACTORY_SNAPSHOT_BODY),
        },
    )
    assert response.status_code == 409, response.json()
    svc.create_bot.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run pytest tests/community/adapters/http/openapi_v1/test_bots_endpoints.py -k "not schema_nests" -v`
Expected: FAIL —— 迁移用例 422(`extra inputs not permitted`: pydantic 只有 `template` 键);工厂新用例 422/405 等未实现行为。

- [ ] **Step 3: 改 pydantic schema(GREEN)**

`schemas.py` 176-187 段整体替换为:

```python
class BotCreateEngineProperties(BaseModel):
    """Engine-specific properties used while creating a bot."""

    model_config = ConfigDict(extra="forbid")

    template_type: str | None = Field(
        description=(
            "Template type declared with the config. Required for template-"
            "factory snapshots (any value, echoed from available-t-templates); "
            "for hand-written application-coding configs omit it or pass "
            "'applicationCoding'."
        ),
        default=None,
    )
    template_config: dict[str, Any] = Field(
        description=(
            "Template configuration. Either hand-written application-coding "
            "properties, or a template-factory snapshot (identified by "
            "template_key + template_uid) echoed verbatim from "
            "bot-templates/available-tc-list; platform-managed identity and "
            "lifecycle fields are not accepted."
        ),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && uv run pytest tests/community/adapters/http/openapi_v1/test_bots_endpoints.py -k "not schema_nests" -v`
Expected: PASS 全绿(端点级契约由 pydantic + Task 1 的 strategy 组合达成)。

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/schemas.py \
        src/backend/tests/community/adapters/http/openapi_v1/test_bots_endpoints.py
git commit -m "feat(backend): rename openapi create template key and accept factory snapshots"
```

---

### Task 4: 查询投影 dispatch + 工厂快照透传

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_management/template_public_view.py`
- Test: `src/backend/tests/community/core/bot_management/test_template_public_view.py`
- Test: `src/backend/tests/community/core/bot_inventory/services/test_bot_inventory_service.py`

- [ ] **Step 1: 写投影用例(RED)**

`test_template_public_view.py` 追加(完整代码,`project_template_config_for_public` 已有的 import 风格随文件头):

```python
_FACTORY_SNAPSHOT = {
    "template_key": "applicationCoding",
    "template_uid": "aicoding_bot_template",
    "template_version_id": 2800006,
    "template_name": "应用 Bot",
    "image": "reg.antgroup-inc.cn/arca:20260901140138",
    "resource_spec": {"cpu": "4", "memory": "8g"},
    "envs": {"AIX_SKIP_DAEMON": "false"},
    "capabilities": {"channel_management": False},
    "bot_template_config": {
        "id": 2800006,
        "ext_config": {"thetaKey": "enc:v1:deadbeef", "other": "keep"},
    },
    "custom_field_values": {"field_a": "value_a"},
    "token": "enc:v1:tokenblob",
}


def test_factory_snapshot_passthrough_returns_everything_but_secrets() -> None:
    projected = project_template_config_for_public(_FACTORY_SNAPSHOT)
    assert projected is not None
    assert projected["template_key"] == "applicationCoding"
    assert projected["image"] == "reg.antgroup-inc.cn/arca:20260901140138"
    assert projected["resource_spec"] == {"cpu": "4", "memory": "8g"}
    assert projected["envs"] == {"AIX_SKIP_DAEMON": "false"}
    assert projected["custom_field_values"] == {"field_a": "value_a"}
    assert "token" not in projected
    assert "thetaKey" not in projected["bot_template_config"]["ext_config"]
    assert projected["bot_template_config"]["ext_config"]["other"] == "keep"


def test_factory_snapshot_projection_is_a_detached_copy() -> None:
    config = {"template_key": "k", "template_uid": "u", "envs": {"a": "b"}}
    projected = project_template_config_for_public(config)
    projected["envs"]["a"] = "mutated"
    assert config["envs"]["a"] == "b"


def test_snapshot_with_scattered_factory_key_only_stays_allowlisted() -> None:
    # 只带 template_key(无 uid)的非 applicationCoding config 不构成工厂快照:allowlist 判定照旧
    config = {"template_key": "applicationCoding"}
    # template_key 在 allowlist 里,回现有投影
    projected = project_template_config_for_public(config)
    assert projected == {"template_key": "applicationCoding"}


def test_handwritten_config_stays_allowlisted_after_dispatch() -> None:
    config = {"devflow_workflow": "w1", "code_repos": ["r1"]}
    assert project_template_config_for_public(config) == config
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run pytest tests/community/core/bot_management/test_template_public_view.py -v`
Expected: FAIL(工厂快照走 allowlist,`image` 等键丢失/None)。

- [ ] **Step 3: 实现投影(GREEN)**

`template_public_view.py` 在文件头 import 区追加:

```python
from copy import deepcopy

from agentclaw.community.core.bot_management.capabilities import (
    TEMPLATE_FACTORY_MARKER_KEYS,
)
```

文件规则注释块(第 9-14 行)追加一条:

```python
# - Template-factory snapshots are the documented exception: they pass
#   through verbatim minus their secret locations (token, thetaKey) — the
#   caller stored them, the caller reads them back.
```

在 `project_template_config_for_public` 之前加两个新成员,并给该函数加 dispatch 首行:

```python
#: Secret locations stripped from passthrough factory snapshots. The outer
#: ``token`` and ``bot_template_config.ext_config.thetaKey`` mirror the
#: provisioning strategy's encryption paths.
_FACTORY_SECRET_TOP_KEYS = ("token",)
_THETA_SECRET_PATH = ("bot_template_config", "ext_config", "thetaKey")


def _is_factory_snapshot(config: Mapping[str, Any]) -> bool:
    return any(key in config for key in TEMPLATE_FACTORY_MARKER_KEYS)


def project_factory_template_snapshot(
    config: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project a template-factory snapshot onto its passthrough subset.

    Everything the caller stored comes back except the secret locations.
    Factory snapshots always carry ``template_key``, so an empty result is
    not expected; return ``None`` anyway to keep the None contract uniform.
    """
    projected = deepcopy(dict(config))
    for key in _FACTORY_SECRET_TOP_KEYS:
        projected.pop(key, None)
    nested = projected.get(_THETA_SECRET_PATH[0])
    if isinstance(nested, dict):
        ext = nested.get(_THETA_SECRET_PATH[1])
        if isinstance(ext, dict):
            ext.pop(_THETA_SECRET_PATH[2], None)
    return projected or None


def project_template_config_for_public(
    config: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Project a stored template snapshot onto its public display subset."""
    if not isinstance(config, Mapping) or not config:
        return None
    if _is_factory_snapshot(config):
        return project_factory_template_snapshot(config)
    ...  # ← 现有 allowlist 主体(42-54 行)原样保留
```

现有主体保留不动(从 `if not any(key in config ...)` 到 return)。

- [ ] **Step 4: 跑投影测试确认通过**

Run: `cd src/backend && uv run pytest tests/community/core/bot_management/test_template_public_view.py -v`
Expected: PASS 全绿。

- [ ] **Step 5: /all attach 面用例(存量工厂快照切换 + 全量回显)**

`test_bot_inventory_service.py` 在 `test_page_slice_attaches_projected_template_config`(716)后追加(完整代码,复用该用例的 service/page 构造 helper 形态——以现有 716 用例的局部 setup 为模板,改 ext 内容):

```python
def test_page_slice_attaches_factory_snapshot_verbatim_minus_secrets(
    service,
) -> None:
    # 存量工厂快照(老 TC 链路落的)在 /all 切到透传投影(spec §6 有意变更)
    service_template = (
        _service_with_template_bot(
            "bot-factory-1",
            template_type="applicationCoding",
            ext={
                "template_key": "applicationCoding",
                "template_uid": "aicoding_bot_template",
                "image": "reg.antgroup-inc.cn/arca:1",
                "resource_spec": {"cpu": "4"},
                "bot_template_config": {
                    "ext_config": {"thetaKey": "enc:v1:x"}
                },
            },
        )
    )
    items, _ = service.list_items(page=1, page_size=20)
    item = next(i for i in items if i.bot_id == "bot-factory-1")
    assert item.template_config == {
        "template_key": "applicationCoding",
        "template_uid": "aicoding_bot_template",
        "image": "reg.antgroup-inc.cn/arca:1",
        "resource_spec": {"cpu": "4"},
        "bot_template_config": {"ext_config": {}},
    }
```

注:`_service_with_template_bot` 是按现有 716 用例的 setup 内联改写的示意 helper 名——执行时直接以现有用例的 service/bot 行构造方式内联,不要新造 helper(以 716 用例真实代码为准搬移)。

- [ ] **Step 6: 跑 attach 用例确认通过**

Run: `cd src/backend && uv run pytest tests/community/core/bot_inventory/services/test_bot_inventory_service.py -v`
Expected: PASS 全绿(716 老用例改键后也应保持:它的 ext 里 `{"template_key": "normalCC"}` 只带 key 无 uid → 到 allowlist 仍是 `{"template_key": "normalCC"}`,断言不变即验证了"散键不切投影")。

- [ ] **Step 7: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_management/template_public_view.py \
        src/backend/tests/community/core/bot_management/test_template_public_view.py \
        src/backend/tests/community/core/bot_inventory/services/test_bot_inventory_service.py
git commit -m "feat(backend): passthrough-project factory template snapshots on queries"
```

---

### Task 5: gateway 契约双写(avernet + ocb)

**Files:**
- Modify: `src/gateway/configs/schemas/bots.openapi.json`(BotCreateEngineProperties 段,约 877-895 行)
- Modify: `src/gateway/tests/fixtures/bots.openapi.json`(同段)
- Modify(ocb 仓库): `~/IdeaProjects/ocb/src/gateway/configs/schemas/bots.openapi.json` + `~/IdeaProjects/ocb/src/gateway/tests/fixtures/bots.openapi.json`(若存在对应段;ocb 双写是既有约定)

**手工精准 patch,禁止全量 regen**(golden 快照流程,记忆既有约定)。

- [ ] **Step 1: patch 两个 JSON 的 BotCreateEngineProperties 段**

把 `configs/schemas/bots.openapi.json` 中:

```json
      "BotCreateEngineProperties": {
        "additionalProperties": false,
        "description": "Engine-specific properties used while creating a bot.",
        "properties": {
          "template": {
            "additionalProperties": true,
            "description": "Application-coding template properties. Passed through unchanged to the template validator; platform-managed identity and lifecycle fields are not accepted.",
            "title": "Template",
            "type": "object"
          }
        },
        "required": ["template"],
        "title": "BotCreateEngineProperties",
        "type": "object"
      },
```

替换为(注意保持 JSON 键序、缩进与文件风格一致 —— properties 按字母序 `template_config` < `template_type`):

```json
      "BotCreateEngineProperties": {
        "additionalProperties": false,
        "description": "Engine-specific properties used while creating a bot.",
        "properties": {
          "template_config": {
            "additionalProperties": true,
            "description": "Template configuration. Either hand-written application-coding properties, or a template-factory snapshot (identified by template_key + template_uid) echoed verbatim from bot-templates/available-tc-list; platform-managed identity and lifecycle fields are not accepted.",
            "title": "Template Config",
            "type": "object"
          },
          "template_type": {
            "anyOf": [
              { "type": "string" },
              { "type": "null" }
            ],
            "default": null,
            "description": "Template type declared with the config. Required for template-factory snapshots (any value, echoed from available-tc-list); for hand-written application-coding configs omit it or pass 'applicationCoding'.",
            "title": "Template Type"
          }
        },
        "required": ["template_config"],
        "title": "BotCreateEngineProperties",
        "type": "object"
      },
```

`tests/fixtures/bots.openapi.json` 做完全相同的替换。顺手核对 `BotCreate`(843-853 行)里 `engine_properties` 字段的 description("Optional engine-specific properties. Omit for a plain bot; provide template for an application-coding bot.")改为:

```json
            "description": "Optional engine-specific properties. Omit for a plain bot; provide template_config (hand-written application-coding or a template-factory snapshot) for a template-backed bot.",
```

- [ ] **Step 2: 校验 JSON 合法 + backend schema 断言闭环**

Run: `cd src/backend && uv run pytest tests/community/adapters/http/openapi_v1/test_bots_endpoints.py -k "schema_nests" -v`
Expected: PASS(Task 3 预置的断言:`set(engine_properties) == {"template_type", "template_config"}`)。

Run: `python -c "import json; json.load(open('src/gateway/configs/schemas/bots.openapi.json')); json.load(open('src/gateway/tests/fixtures/bots.openapi.json')); print('ok')" && cd src/gateway && uv run pytest tests -k "bots" -v`
Expected: JSON ok;gateway 相关用例 PASS(注意 gateway 用例断言 spec 与 fixtures 一致的双份同步)。

- [ ] **Step 3: ocb 仓库双写**

把同样的两段 patch 应用到 `~/IdeaProjects/ocb/src/gateway/configs/schemas/bots.openapi.json` 与 `~/IdeaProjects/ocb/src/gateway/tests/fixtures/bots.openapi.json`(先 `grep -n "BotCreateEngineProperties" <file>` 确认段位置;ocb 侧文件头可能有差异,只替换等价段),随后在 ocb 仓库单独提交:

```bash
cd ~/IdeaProjects/ocb
python -c "import json; json.load(open('src/gateway/configs/schemas/bots.openapi.json')); print('ok')"
git add src/gateway/configs/schemas/bots.openapi.json src/gateway/tests/fixtures/bots.openapi.json
git commit -m "chore(gateway): sync BotCreateEngineProperties template_config contract"
```

(ocb 侧的 commit/push 时机如与用户当期 PR 冲突,先本地 commit 不 push,告知用户。)

- [ ] **Step 4: Commit(avernet 侧)**

```bash
git add src/gateway/configs/schemas/bots.openapi.json src/gateway/tests/fixtures/bots.openapi.json
git commit -m "feat(gateway): rename BotCreateEngineProperties template key and add template_type"
```

---

### Task 6: 接入指南改写

**Files:**
- Modify: `docs/bot-create-api-guide.zh-CN.md`

- [ ] **Step 1: 改写 §0 表格、§1.1、§1.3、§1.4、§3、§4**

按 spec §3 契约逐节改:

1. §0 "模板入参"行:新 openapi 面改为 ``engine_properties.template_type + template_config``(工厂快照透传,与 available-tc-list item 逐字段对应;或手填 application-coding)。
2. §1.1 请求表 `engine_properties` 行:改为"两键:template_type(工厂必传、手填省略/固定 applicationCoding)+ template_config";请求示例 B 改成 `{"template_type": null/省略..., "template_config": {...手填键...}}`;新增请求示例 C(tc-list 快照照抄体,含映射说明 `engine ← item.engine_type`、`custom_field_values` 追加)。
3. §1.3 校验规则改写:键域(两个键)、工厂判定(template_key+template_uid 双非空)、密钥落库/查询剔除(token 加密;thetaKey 由调用方密文传入)、混传 422、手填 template_type 冒充 422、组合 gates 409(工厂同样适用);server-managed 拒收清单不变(强调 template_uid **工厂形态放行**,区别于手填形态)。
4. §1.4 响应示例 `engine_properties` echo 改键名;`template_config` 说明改为"工厂快照:全量透传减 token/thetaKey;手填:白名单投影"。
5. §3 第三步:available-tc-list 快照可整段回传(工厂形态),带 `template_uid` 没关系(该形态放行);删除"由前端丢弃 template_uid"的限制(仅手填形态仍要求删除)。
6. §3.4/常见错误速查:删"模板工厂模板 openapi 面暂无创建入口"一条,改为已支持(工厂快照透传);错误表补 `422 template factory snapshot must not mix...`、`422 engine_properties.template_type is required...` 两行;`template`→`template_config` 全文替换。
7. 文头"适用版本"行补本设计链接 `docs/superpowers/specs/2026-09-01-openapi-template-config-passthrough-design.md`。

- [ ] **Step 2: 自查一致性**

全文 `grep -n "template" docs/bot-create-api-guide.zh-CN.md | grep -v template_config | grep -v template_type | grep -v template_key | grep -v template_uid | grep -v template_version`,Expected: 只剩"模板工厂/模板入参"类中文叙述,无 v1 契约的裸 `engine_properties.template` 引用。

- [ ] **Step 3: Commit**

```bash
git add docs/bot-create-api-guide.zh-CN.md
git commit -m "docs: rewrite bot-create guide for template_config passthrough contract"
```

---

### Task 7: 全量回归 + 覆盖率 gate + 批量终审

- [ ] **Step 1: backend 全量回归**

Run: `cd src/backend && uv run pytest tests/community -x -q`
Expected: 全绿(重点注视:`test_bots_endpoints.py`、`test_application_coding_create.py`、`test_template_public_view.py`、`test_bot_inventory_service.py`、engine registry 路由测试、`bot_service` 消费链路测试)。

- [ ] **Step 2: 变更行覆盖率 gate**

Run: `bash src/backend/scripts/ci_test.sh`
Expected: changed-line coverage ≥ 80%(阈值与 CI 一致);失败则按 report 补测试(工厂分支/投影分支必须全覆盖——注意 `_is_factory_snapshot`、`_prepare_factory_snapshot`、`project_factory_template_snapshot` 的每一 raise 分支都有用例)。

- [ ] **Step 3: gateway 契约回归**

Run: `cd src/gateway && uv run pytest tests -q`
Expected: 全绿(记忆提示:本机 perl 被杀跑不了 singlebox,相关用例如被收集失败按既有 skip 规则处理,不算新增失败)。

- [ ] **Step 4: 批量终审(用户约定:一次综合审查)**

对 `git diff origin/dev...HEAD` 跑一次 code review(重点:错误码映射镜像、`Bot`/`BotAuthStatusPoll` 响应 schema、`engine_properties` echo、“老公/存量工厂 bot 投影不变/切换”两面回归锚点、ocb 双写一致性)。CRITICAL/HIGH 修复后重跑 Step 1-2。

- [ ] **Step 5: 收尾**

```bash
git push -u origin feat/openapi-template-config-passthrough
```

(按用户当期指示决定是否提 PR;PR title/desc 遵循 `type(scope): outcome` + Problem/Solution/Validation 结构,Validation 里贴 ci_test.sh 的覆盖率输出。)

---

## Self-Review 记录

- **Spec 覆盖:** §3 契约→Task 3/5;§4/§5 行为与校验→Task 1(gates 矩阵每个 raise 分支都有用例);§6 查询投影→Task 4;§7 密钥(token 加密自动覆盖于既有门控、thetaKey 透传+查询剔除→Task 4);§8 文件清单→Task 1-5 全覆盖(router.py 无需改动、provisioning.py 零改动——已由 Task 1 实现体证明);§10 测试计划 1-15→Task 1/3/4 用例 + Task 7 gate;§11 分支/双写→执行约定 + Task 5。
- **类型一致性:** `_ENGINE_PROPERTIES_KEYS`/`_is_factory_snapshot`/`_prepare_factory_snapshot`/`project_factory_template_snapshot` 命名在 Task 1/4 间一致;pydantic 键序 `template_config`/`template_type` 与 JSON patch 一致。
- **占位符:** 无 TBD/TODO;Task 4 Step 5 的 helper 说明了"以内联为准"的实现指令,属于明确指令非占位。
