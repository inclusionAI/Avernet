# Engine 词汇表收口与 Template 拓展字段设计（2026-08-31）

> **状态：DRAFT，待评审。**
>
> **原则（用户拍板）：** `engine` 只有真实引擎（openclaw、claude_code、teclaw、hermes、moltis…）；
> `aicoding` 这类"形态/产品"概念进入 template 等拓展字段，不再作为引擎值创建新 bot。
> 存量业务零影响。
>
> **前提：** openapi 对外面**尚未上线**（测试中）——对外面可以直接拒绝 `aicoding`，
> 不存在外部调用方兼容包袱；兼容包袱只存在于内部老链路。

---

## 1. 现状事实（调研锚点，2026-08-31 dev HEAD 9147fe741）

### 1.1 两条创建链路早已汇合

```text
openapi:  POST /openapi/v1/bots        router.py:435-530
            └─ body.engine ∈ _get_engine_types()   ← 校验含 aicoding（SUPPORTED_ENGINE_TYPES）
老链路:   POST /api/bots               bot_management/router.py:922 + _bot_create_spec:119
            └─ engine_type 从 body 直取，template_type/template_config 直传，无校验
两条链路 ↓
core:     create_bot_with_authorization  create_flow.py:446
            └─ _prepare_create           create_flow.py:248   ← 兼容收口的天然单点
            └─ bot_service.create_bot    bot_service.py:1244
                 ├─ ac_bots.active_engine = engine_type 原样落库（:1312,:1397）
                 ├─ ac_bots.template_type 列（:1406）
                 └─ template_type and template_config → ac_templates.ext 行（:1433, TemplateService.create_template）
```

### 1.2 runtime 已实现"claude_code 为壳、aicoding 为内部实现"

- `workspace/runtime_identity.py:6` `claude_code_uses_aicoding_runtime`：
  `claude_code + template_type 非空且非 normalCC → aicoding 运行时`
- `engines/registry.py:236-237`：`AicodingProvisioningStrategy` 同时注册给 `aicoding` 与 `claude_code`；
  `resolve_bot_engine` / `resolve_baas_engine_bucket` 动态路由
- `bot_inventory/policies/combo_policy.py:57` 方向已写死：
  *"claude_code is the only external engine value: the runtime routing to the aicoding
  adapter is an internal concern, not an alternative engine"*
- openapi application-coding 创建已只认 claude_code（`strategy.py:177-182` engine≠claude_code 直接
  `BotCombinationUnsupportedError`）

### 1.3 词汇表与存量耦合点（`SUPPORTED_ENGINE_TYPES` 不能直接删 aicoding 的原因）

| 消费点 | 锚点 | 影响 |
| --- | --- | --- |
| bot 行 `engine_types` 列存量已存 aicoding | `implementations/bot/bot.py:110`、`plugin_api/models.py:69` | switch_engine 靠它校验 |
| `{engine}_conf` 资源目录名展开 | `services/resource_file_service.py:82` | 读路径出现 `aicoding_conf` |
| 引擎列表下发（desktop/内部路由/available-engines） | `desktop_bot_service.py:691`、`bot_management/router.py:1385` | 部署配置 |
| 引擎过滤查询 | `protocols/bot/bot.py:351` | active_engine='aicoding' 过滤 |
| notify / mcp / cli defaults bucket | `notify/constants.py:5` 等 | 读词汇 |
| singlebox 模板别名 | `di/.../singlebox/template_config.py:18-19` | local 测试形态 |
| manifest 能力表（未落地的设计文档） | `docs/.../engine-requirements` ARC_ENGINES | 待同步修订 |

**结论：读全动，写收口。**

### 1.4 查询接口现状（用户要求的展示能力，大部分已具备）

| 端点 | schema | 填充 | 现状 |
| --- | --- | --- | --- |
| `GET /openapi/v1/bots` | `Bot`（schemas.py:69，含 template_type:111 / template_config:116） | `_to_bot`（router.py:190-218） | ✅ 已返回（白名单投影） |
| `GET /openapi/v1/bots/all` | `BotInventoryItem`（schemas.py:670，含 :716/:721） | `_to_inventory_item`（router.py:273-318）+ 分页批量 attach（`bot_inventory_service.py:178-195`） | ✅ 已返回（template_type 非空的卡片才 attach） |
| `GET /openapi/v1/bots/{bot_id}` | `Bot` | `_to_bot` | ✅ 已返回 |

公共投影：`template_public_view.py` `_PUBLIC_TEMPLATE_KEYS = (code_repos, devflow_workflow,
template_key, template_uid, yuque_kb_repos)` —— 密钥（`token`、`bot_template_config.ext_config.thetaKey`）
永不露出。**缺口：form 标记不在白名单，查询面看不见 aicoding 形态。**

---

## 2. 词汇表设计：engine 与 form 分离

```text
engine（真实引擎，可创建/可切换）: openclaw | claude_code | teclaw | hermes | moltis
                                   （= SUPPORTED_ENGINE_TYPES − aicoding）
form（形态，进拓展字段，只读展示）: aicoding（后续可扩展）
```

- 常量落点：`core/workspace/constants.py` 追加
  `PUBLIC_CREATABLE_ENGINES = frozenset(SUPPORTED_ENGINE_TYPES) - {"aicoding"}`；
  **`SUPPORTED_ENGINE_TYPES` 本体不动**（存量读词汇，见 §1.3）。
- form 标记键：**`engine_form`**（顶层键，值如 `"aicoding"`）。
  - 显示层语义归平台（不是引擎私有），放 template_config 顶层而非 `bot_template_config.ext_config`
    （后者是引擎私有扩展先例 thetaKey，且不进公共投影）。
  - **server-managed**：只能由归一化/平台写入；PUBLIC 校验模式下用户在
    `engine_properties.template` 传 `engine_form` → 422（加入 server-managed 拒绝清单）。

## 3. 链路兼容设计

### 3.1 openapi 创建/切换（未上线 → 直接拒，不归一化）

- `create_bot`（router.py:498）与切换端点（router.py:1113）的校验由
  `engine in _get_engine_types()` 收紧为 **`engine in PUBLIC_CREATABLE_ENGINES`**
  （以部署 registry 为上限再交公共白名单）。
- `engine="aicoding"` → 400 `UnsupportedEngineError`。文档/`_ENGINE_DESC` 明确词汇表。
- openapi 一期**不提供**创建 aicoding form bot 的入口；aicoding 形态 bot 仍走内部链路。

### 3.2 老链路归一化（汇合点，单点收口）

位置：`create_flow._prepare_create`（create_flow.py:248）——openapi 已在 router 层
被 400 拦截，到这里的 `aicoding` 只来自内部面，归一化只服务老链路。

规则（`engine=="aicoding"` 时）：

```text
1. engine               → "claude_code"
2. form 标记写位置（保运行时等价的唯一依据）：
   a. template_type 非空且 template_config 非空：template_config 合并 {"engine_form": "aicoding"}
      （随 ac_templates.ext 落库）
   b. template_type 为空（plain bot）：不写任何标记——归一后就是干净的
      claude_code plain bot（见下方修订记录）
3. 其余字段（bot_type/space/template_type 本体）不动。
```

> **实现期修订（2026-08-31）**：原 §3.2-b 设计为 plain bot 写 `ac_bots.ext` 标记。
> 修订为 **plain bot 不写标记**：form 是模板属性（"aicoding 的类型只在 template 的
> 拓展字段"），无模板即无形态；且 ext 标记若只被部分消费点读取，会造成
> "运行时=aicoding、开通 bucket=claude_code"的半吊子不一致。plain 老 aicoding
> bot 归一后统一按 claude_code plain bot 运行（runtime/bucket/layout 一致），
> 读路径存量 `active_engine='aicoding'` 的 bot 完全不受影响。

归一化幂等：engine 已是 claude_code 则零改动——正例回归。

### 3.3 运行时判定升级（判定函数唯一化）

`workspace/runtime_identity.py` 升级为单一判定入口：

```python
def uses_aicoding_runtime(
    *, active_engine: str | None,
    template_type: str | None,
    template_config: Mapping | None = None,
    bot_ext: Mapping | None = None,
) -> bool:
    # 1) 存量短路：active_engine == "aicoding" → True（读路径不动）
    # 2) form 标记：active_engine == "claude_code" 且
    #    template_config.engine_form == "aicoding" 或 bot_ext.engine_form == "aicoding" → True
    # 3) 既有语义保留：claude_code + template_type 非空且非 normalCC → True
```

存量 `claude_code_uses_aicoding_runtime` 保留为薄包装（或有调用方逐步迁移）；消费锚点
（改造成传全量输入，一期不强制全量替换）：

- `engines/registry.py` `resolve_bot_engine` / `AicodingProvisioningStrategy.resolve_bot_engine`
- `resolve_baas_engine_bucket`（BaaS bucket 与 default MCP/CLI bucket 的 resolver 签名需能吃到
  form 标记——调用方传入，registry 不新增查库能力）
- `engine_resolver.resolve_runtime_engine_for_bot`
- `AicodingProvisioningStrategy.resolve_bot_engine`（strategy.py:224）

### 3.4 存量兼容红线（一条都不能踩）

1. `SUPPORTED_ENGINE_TYPES` 不删 aicoding；`AicodingProvisioningStrategy(AICODING_ENGINE_TYPE)`
   注册保留——存量 `active_engine='aicoding'` bot 的策略解析/bucket/identity/skill 布局全部照旧。
2. 读路径（查询响应的 `engine` 字段、bucket 解析、`{engine}_conf` 目录、notify/身份文件白名单）
   对存量 aicoding bot **返回原值 `aicoding`**——读不归一，schema 本就是开放 `str`。
3. 不做存量数据迁移（active_engine 批量改写是二期可选项，须只读窗口+全量回归，本期不做）。

---

## 4. 查询接口补齐（all / bots / bot_id 展示 template_type + template_config）

三个端点已具备返回能力（§1.4），本设计只补三件事：

1. **`_PUBLIC_TEMPLATE_KEYS` 白名单追加 `"engine_form"`**（template_public_view.py:21）——
   `project_template_config_for_public` 是三个端点共用的唯一投影，加一处三个接口同时露出，
   前端由此判定"这是 aicoding 形态卡片"。
2. **`/all` 的 attach 语义确认**：现状只对 `template_type` 非空的卡片 attach template_config
   （bot_inventory_service.py:178-195）。plain aicoding 归一 bot（form 标记在 bot.ext）
   的 template_config 为 null、template_type 为 null——**这是预期行为**，前端对这类卡片读
   `bot.ext` 侧标记（见 §5 响应面）或在 bot 详情补 `engine_form`。
3. **响应描述更新**：`Bot`/`BotInventoryItem` 的 template_config 字段 description 补一句
   form 标记的说明（docstring 进 OpenAPI 文档，走既有发布链）。

> 说明：查询接口**不按 form 过滤**（engine 查询参数语义保持"真实引擎"）；
> form 是展示维度，过滤需求出现时再加独立查询参数。

---

## 5. 改动锚点清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `core/workspace/constants.py` | `PUBLIC_CREATABLE_ENGINES` 常量 |
| 2 | `adapters/http/openapi_v1/bots/router.py:498,1113` | 创建/切换校验收紧（400 拒 aicoding） |
| 3 | `core/bot_management/create_flow.py:248` | `_prepare_create` 归一化（aicoding→claude_code + form 标记） |
| 4 | `core/workspace/runtime_identity.py` | `uses_aicoding_runtime`（三元输入 + 存量短路） |
| 5 | `core/bot_management/engines/aicoding/strategy.py` | `to_internal_template_config` server-managed 清单 + `engine_form`；`resolve_bot_engine` 吃 form |
| 6 | `core/bot_management/template_public_view.py:21` | 白名单 + `engine_form` |
| 7 | `adapters/http/openapi_v1/bots/schemas.py` | 两个 response schema 的 template_config description |
| 8 | `bots.openapi.json` + ocb 仓 `application.yaml` | 手工增量（若 router 行为变化影响文档措辞；§0.3 硬规矩） |

无新表、无 DDL、无数据迁移。

## 6. 钉死测试清单

1. openapi `POST /bots` engine=aicoding → 400；engine=claude_code/openclaw → 不受影响。
2. openapi 切换引擎至 aicoding → 400；存量 aicoding bot 切出到其它引擎 → 照旧允许。
3. 老链路 aicoding + applicationCoding → 落库 active_engine=claude_code、
   ac_templates.ext 含 `engine_form: aicoding`；BaaS bucket 与改造前同 bucket（等价性断言）。
4. 老链路 plain aicoding → active_engine=claude_code + `ac_bots.ext.engine_form=aicoding`；
   不插 ac_templates 行、不触发 workspace hosting。
5. 归一化幂等：engine=claude_code 输入零改动。
6. 存量回归：`active_engine='aicoding'` 的 bot —— 查询返回 `engine:'aicoding'`、
   runtime/bucket/identity 布局逐项不变。
7. 查询三接口：template_type/template_config 返回、`engine_form` 露出、
   `token`/`bot_template_config`（thetaKey）绝不出现（扫描断言）。
8. PUBLIC 校验模式：`engine_properties.template` 用户传 `engine_form` → 422。

## 7. 分期与开放问题

- **一期**（本设计）：写入收口 + 判定函数 + 白名单露出。老链路归一化让新行不再出现 engine='aicoding'。
- **二期**（另立决策）：存量数据迁移（a­ctive_engine 批量改写 + 标记回填）、读路径收紧、
  manifest `ARC_ENGINES` 改为引擎×形态二维、openapi 面是否开放 aicoding form 创建。
- **待拍板**：
  1. form 标记键名 `engine_form` 是否与前端/模板工厂契约对齐（当前拍板权在后端，越早定越好）。
  2. 老链路是否仍有"engine=aicoding 无模板"的真实流量——决定 §3.2-b 分支是否只是防御性代码
     （不影响设计形状，但影响测试投入）。
