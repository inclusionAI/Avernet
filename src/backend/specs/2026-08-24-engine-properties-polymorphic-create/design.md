# Bot Create `engine_properties` 与 Engine Strategy 创建预检设计

- **日期**：2026-08-24
- **状态**：已实现（2026-08-25，分支 `feat/engine-properties-create`，基于最新 `origin/dev`）
- **范围**：Backend OpenAPI Bot Create / Passport authorization completion
- **目标分支**：`dev`

---

## 0. 实现基线修正（2026-08-25）

实现时发现最新 `origin/dev` 的状态与本设计早前评审假设不同：**公共契约迁移已经发生**——
`engine_properties`（含必填 `template`）已发布为 `BotCreate`/`BotAuthStatusPoll` 的公开字段，
顶层 `template` 已删除，且 Router 中存在 `body.engine_properties.template` 解包与
`"applicationCoding"` 推断（即本设计要消除的反模式，也正是初稿背景描述的状态）。
另有 #1442（LEGACY/PUBLIC 模板校验模式）已合入。

因此本实现相对 §3 的 D2 做如下收敛：

1. **不引入兼容窗口**：公开契约保持 dev 现状（只有 `engine_properties`），不重新发布已删除的
   legacy `template` 字段；schema 零改动，公开 JSON schema（gateway artifact）零改动。
2. 本 PR 的范围回到纯内部架构：Router 透明化（删除解包与 applicationCoding 推断）、
   application-coding 创建策略下沉到 `AicodingProvisioningStrategy.prepare_create` 成为唯一实现、
   `{"template": None}` 键存在性兼容语义、Core 层来源互斥与未知键拒绝。
3. **#1442 的 LEGACY/PUBLIC 语义完整保留**并移植进新架构：
   `BotCreateSpec.template_validation_mode` 经 routing 传入 `prepare_create`，
   内部快照可携带 `template_uid`，OpenAPI 入口保持严格校验。
   `BotCreateTemplateValidationMode` 移至 `engines/provisioning.py`（create_flow 再导出），
   避免策略反向依赖 create_flow。
4. **错误码决策（已实现）**：Default Strategy 对含 `template` 键的 engine_properties 抛
   `BotCombinationUnsupportedError`，保持 openclaw/teclaw/hermes + applicationCoding 的历史 409
   映射（内部 409 / OpenAPI 409）；其余未知键抛 `BotTemplateInvalidError`。
5. gateway artifact 在 dev 上存在大量既有漂移（task-graph 等未 re-dump）；由于本 PR 契约零
   变化，不夹带 catch-up，建议单独 regeneration。

其余各节描述的目标架构（Strategy 契约、三路路由、测试计划）按原文落地。

---

## 1. 仓库现状（事实基线）

当前代码尚不存在 `engine_properties`。公开创建契约为：

```python
class BotCreate(BaseModel):
    template: BotCreateTemplate | None

class BotCreateTemplate(BaseModel):
    type: Literal["applicationCoding"]
    properties: dict[str, Any]
```

OpenAPI Router 当前执行的是协议拆包：

```python
template_type = body.template.type if body.template is not None else None
template_config = body.template.properties if body.template is not None else None
```

随后写入已有 Core DTO：

```python
BotCreateSpec(
    template_type=template_type,
    template_config=template_config,
)
```

`applicationCoding` 不是 Router 推断生成的值，而是 caller 通过
`template.type` 显式提交，并由 Pydantic `Literal` 约束。

创建前置策略也已经收敛到 Core：

- `create_bot_with_authorization()` 和 `complete_bot_authorization()` 都调用
  `_prepare_create()`；
- application-coding 的 cloud / engine / bot type / space 校验已经在
  `prepare_bot_create()` 中；
- reserved 字段、配置类型和 workspace hosting capability 校验均发生在
  Passport 和 persistence 之前。

因此，本次不是“把 Router 中现有的一坨 application-coding 业务逻辑下沉”。
现有 Router 主要承担 DTO 转换。本次真实改动目标是：

1. **新增并迁移公开请求字段**：`template` → `engine_properties`；
2. **将已经位于通用 Core create flow 的 application-coding 规则迁移到现有
   per-engine Strategy**，避免通用编排长期持有具体引擎规则。

---

## 2. 目标与非目标

### 2.1 目标

1. 新 OpenAPI 请求使用：

   ```json
   {
     "engine": "claude_code",
     "engine_properties": {
       "template": {
         "devflow_workflow": "app-flow",
         "code_repos": []
       }
     }
   }
   ```

2. Router 将 `engine_properties` 转成 plain `dict` 后透传，不读取
   `engine_properties.template` 的业务字段。
3. 复用现有 `EngineProvisioningStrategy`，由具体 Strategy 解析和校验自己
   拥有的创建参数。
4. 同步创建与 Passport 授权完成继续经过同一个 `_prepare_create()`。
5. 所有校验继续发生在 Passport、Bot persistence 和 workspace 创建之前。
6. legacy `template_type/template_config` 调用不被清空或静默降级。
7. unsupported 或冲突输入明确失败，不静默忽略。

### 2.2 非目标

1. 不重写 `BotService.create_bot()`。
2. 不新增第二套 engine registry。
3. 不新增 pending intent 表、Saga 或 reconcile 机制。
4. 不重构 update/restart/delete 的全部模板逻辑。
5. 不改变已有错误码和 HTTP 状态映射。

---

## 3. 公共契约决策

### D1. 这是公开契约迁移，不描述成既有事实

`engine_properties` 是本次要新增的字段，不是仓库当前已经发布的字段。
本次必须同时修改：

- `adapters/http/openapi_v1/bots/schemas.py`；
- Create Router；
- auth-completion echo schema 和 Router；
- OpenAPI 文档与 gateway JSON schema；
- 兼容性/契约测试。

### D2. 采用一个兼容窗口，不在同一 PR 直接删除 `template`

当前 `template` 已经是公开 schema 字段。为避免未确认调用方直接收到 422，
本次确定：

```python
class BotCreateEngineProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # engine_properties 一旦出现，template 必须是非空对象。
    template: dict[str, Any] = Field(min_length=1)


class BotCreate(BaseModel):
    engine_properties: BotCreateEngineProperties | None = None
    template: BotCreateTemplate | None = Field(
        default=None,
        deprecated=True,
    )
```

规则：

| 输入 | 行为 |
| --- | --- |
| 两者都不传 | plain Bot 创建 |
| 只传 `engine_properties` | 走新 Strategy 路径 |
| 只传 legacy `template` | 走兼容路径，但 application-coding 仍路由到同一 Strategy |
| 两者同时传 | 422/领域参数错误，不定义覆盖顺序 |

后续删除 `template` 必须单独做 breaking-change PR，并明确版本或下线窗口；
本次实现不得再以“可能没有外部 caller”为由隐式改成一次性删除。

### D3. 接受新结构暂时失去显式 template discriminator 的代价

目标命名由调用方要求确定为：

```text
body.template.properties -> body.engine_properties.template
```

因此新结构不再携带 `template.type`，其语义由 `engine` 选择出的 Strategy 决定。
代价是：同一 engine 将来若支持第二种 template 类型，当前结构没有显式
 discriminator。届时需要新增类型字段或版本化扩展结构。

本次确认接受这一 tradeoff，不为尚不存在的第二种类型提前增加抽象。

---

## 4. Core DTO 决策

### D4. 将现有未消费的 `extra_properties` 明确改为 `engine_properties`

当前 `BotCreateSpec` 已有：

```python
extra_properties: dict[str, Any] = field(default_factory=dict)
```

它目前没有公共 schema 映射，也没有创建逻辑消费。本次应明确选择一种做法，
不能同时保留两个含义相同的 opaque bag。

推荐直接改名：

```python
engine_properties: dict[str, Any] = field(default_factory=dict)
```

并更新所有 `BotCreateSpec` 构造点。该字段是 Core 内部 DTO，不表示当前公共 API
已经存在同名字段。

历史字段暂时保留：

```python
template_type: str | None = None
template_config: dict[str, Any] | None = None
```

它们服务于 internal `/api/bots` 和其他存量调用者。

### D5. 新旧输入互斥在 `_prepare_create()` 入口统一校验

规则必须落实在目标代码中，而不是只写在兼容章节：

```python
has_legacy_template = (
    spec.template_type is not None or spec.template_config is not None
)
if spec.engine_properties and has_legacy_template:
    raise BotTemplateInvalidError(
        "engine_properties cannot be combined with legacy template fields"
    )
```

Router/Pydantic 可以提前提供更友好的 422，但 Core 仍保留该不变量，避免内部调用
绕过 HTTP 层。

---

## 5. Strategy 契约

复用现有 `EngineProvisioningStrategy`，增加唯一一个创建预检 hook：

```python
@dataclass(frozen=True)
class PreparedBotCreate:
    template_type: str | None = None
    template_config: dict[str, Any] | None = None
    requires_workspace_hosting: bool = False


class EngineProvisioningStrategy(ABC):
    @abstractmethod
    def prepare_create(
        self,
        *,
        engine_properties: dict[str, Any],
        bot_type: str,
        deployment_mode: str,
        space_kind: str,
    ) -> PreparedBotCreate:
        """Validate engine-owned create input before side effects."""
```

`PreparedBotCreate` 放在 Strategy contract 所在的 Core 中性模块，避免
`provisioning.py` 反向导入 `create_flow.py`。

本次不新增 `EnginePropertiesHandlerRegistry`、`EngineCreatePlan` 或新的 Context
DTO。

---

## 6. Strategy 行为

### 6.1 DefaultProvisioningStrategy

默认 Strategy 不支持非空创建扩展。输入按语义分两类拒绝：

```python
if not engine_properties:
    return PreparedBotCreate()
if "template" in engine_properties:
    # application-coding intent（新公共契约或 legacy 归一化）落到非 coding 引擎：
    # 组合错误保持历史 409 映射（内部 409 / OpenAPI 409），不得变成 422。
    raise BotCombinationUnsupportedError(
        f"application coding does not support engine: {self.engine_type}"
    )
raise BotTemplateInvalidError(
    f"engine {self.engine_type} does not support engine_properties: ..."
)
```

**实现确定的错误码决策**（2026-08-24 评审遗留决策）：legacy
`applicationCoding` + 非 claude_code 引擎（openclaw/teclaw/hermes）现状是
`BotCombinationUnsupportedError`（内部 409 / OpenAPI 409）。若 Default 只抛
`BotTemplateInvalidError`（内部 400 / OpenAPI 422），同一请求的映射就变了，
违反 §2.2"不改变已有错误码"。因此 Default 对含 `template` 键的输入抛组合错误，
其余未知键抛模板无效错误。这样 caller 输入不会被静默忽略，历史映射也保持不变。

### 6.2 AicodingProvisioningStrategy

Registry 当前分别注册：

```python
AicodingProvisioningStrategy("aicoding")
AicodingProvisioningStrategy("claude_code")
```

但现有 application-coding 创建门禁只允许 `claude_code`。因此创建 hook 必须检查
Strategy 实例自己的 `engine_type`，不能因为复用同一个类而放宽行为：

```python
if engine_properties and self.engine_type != "claude_code":
    raise BotCombinationUnsupportedError(
        f"application coding does not support engine: {self.engine_type}"
    )
```

随后先校验 Core 不变量：

```python
unknown_keys = set(engine_properties) - {"template"}
if unknown_keys:
    raise BotTemplateInvalidError(
        f"unsupported engine_properties fields: {sorted(unknown_keys)}"
    )
if "template" not in engine_properties:
    raise BotTemplateInvalidError("engine_properties.template is required")

template = engine_properties["template"]
if template is None:
    # Only produced by the legacy template_type path in _prepare_create().
    sanitized_template = None
elif not isinstance(template, dict) or not template:
    raise BotTemplateInvalidError(
        "engine_properties.template must be a non-empty object"
    )
else:
    sanitized_template = _validate_and_detach_template(template)
```

这里必须按 **键是否存在** 判断 intent，不能使用 `get()` 后按值的 truthiness
判断。`{"template": None}` 与 `{}` 在 Core 中语义不同：前者是 legacy
application-coding 无配置，后者是没有 engine properties。

不能只依赖 HTTP schema 的 `extra="forbid"`，因为 Core 也可能被内部调用者直接
构造。`claude_code + {"other": ...}` 必须明确失败，不能静默忽略。

随后执行现有等价校验：

1. 只允许 cloud deployment；
2. 只允许 personal Bot；
3. 只允许 personal space；
4. 新公共契约传入的 `template` 必须是非空对象；
5. 拒绝 server-managed 字段；
6. 校验已声明字段类型；
7. 深拷贝 caller 输入；
8. 返回：

   ```python
   PreparedBotCreate(
       template_type="applicationCoding",
       template_config=sanitized_template,
       requires_workspace_hosting=True,
   )
   ```

`aicoding + engine_properties.template` 必须保持拒绝，与当前行为一致。

---

## 7. Legacy 路由：application-coding 只保留一份实现

不能让新输入走 Strategy、legacy application-coding 继续走
`prepare_bot_create()` 的旧实现，否则会形成两份校验规则。

`_prepare_create()` 应先判定输入来源，再统一路由：

```python
def _prepare_create(...):
    _reject_mixed_sources(spec)

    if spec.engine_properties:
        prepared = _prepare_with_engine_strategy(spec, context)
    elif spec.template_type == "applicationCoding":
        # Core-only compatibility representation. Key presence preserves the
        # legacy intent even when the legacy caller intentionally omitted config.
        prepared = _prepare_with_engine_strategy(
            replace(
                spec,
                engine_properties={"template": spec.template_config},
            ),
            context,
        )
    else:
        # plain Bot 或其他历史 template 类型，保持现有通用兼容行为；返回值
        # 必须同时保留 template_type 和经过 sanitation 的 template_config。
        prepared = _prepare_legacy_non_application_template(spec)

    if (
        prepared.requires_workspace_hosting
        and not bot_service.is_workspace_hosting_available()
    ):
        raise ApplicationCodingUnavailableError()

    return replace(
        spec,
        template_type=prepared.template_type,
        template_config=prepared.template_config,
    )
```

关键约束：

- legacy `applicationCoding` 和新 `engine_properties.template` 都由同一具体 Strategy
  校验；
- `{"template": None}` 是 **Core-only 的 legacy compatibility representation**：
  `"template"` 键存在表示 application-coding intent，值为 `None` 表示 legacy caller
  有意省略配置。Strategy 必须返回
  `PreparedBotCreate(template_type="applicationCoding", template_config=None,
  requires_workspace_hosting=True)`，并照常执行 engine/deployment/bot/space 门禁；
- 新 HTTP schema 不允许 caller 提交 `template: null`，所以该兼容语义不会扩展为新的
  公共契约；空字典表示没有新 engine properties，而不是 application-coding intent；
- legacy 非 application-coding template 保持原行为；
- plain Bot 不会因空 `PreparedBotCreate` 被意外清空已有字段；
- 迁移后 `create_flow.py` 可以删除 application-coding 专属常量和校验函数，但仍可
  保留中性的 legacy template sanitation；
- 不接受“双份实现只是临时过渡”的隐式状态。

---

## 8. Router 与授权完成路径

### 8.1 新 Create 请求

Router 只做 DTO 转换：

```python
engine_properties = (
    body.engine_properties.model_dump(exclude_none=True)
    if body.engine_properties is not None
    else {}
)
```

它不读取 `engine_properties.template`，也不推断 application-coding 语义。

### 8.2 Legacy `template` 请求

兼容窗口内，Router 可以把公开 legacy DTO 映射到 Core 的历史字段：

```python
template_type = body.template.type
template_config = body.template.properties
```

这是协议兼容转换。application-coding 的组合校验、reserved 字段和 capability
判断仍只在 Strategy 中执行。

### 8.3 Passport authorization completion

`BotAuthStatusPoll` 必须同步增加 `engine_properties`，并在兼容窗口保留 deprecated
`template` echo。两者互斥规则与 Create 完全一致。

同步创建和授权完成都构造同一种 `BotCreateSpec`，并调用同一个
`_prepare_create()`。本次仍依赖 caller echo，不新增 pending intent persistence；
缺失或非法输入返回明确错误并记录日志，由 caller 携带原请求重试。

---

## 9. 文件级改动计划

| 文件 | 改动 |
| --- | --- |
| `adapters/http/openapi_v1/bots/schemas.py` | 新增 `BotCreateEngineProperties`；Create/Poll 增加 `engine_properties`；legacy `template` 标记 deprecated；增加互斥校验 |
| `adapters/http/openapi_v1/bots/router.py` | 新字段转 plain dict；兼容旧 `template` DTO；不读取新字段内部业务内容 |
| `core/bot_management/create_flow.py` | `BotCreateSpec.extra_properties` 改名；统一新旧来源路由；移除 application-coding 专属校验实现，保留通用编排和 capability check |
| `core/bot_management/engines/provisioning.py` | 增加 `PreparedBotCreate` 和 `prepare_create` contract |
| `core/bot_management/engines/default.py` | 非空 unsupported properties 明确失败 |
| `core/bot_management/engines/aicoding/strategy.py` | application-coding 唯一校验实现；保留 `claude_code` engine gate |
| `docs/superpowers/specs/2026-08-12-bot-workshop-openapi-inventory.md`（如端点清单涉及创建契约） | 更新真实存在的端点清单；仓库当前没有 `docs/openapi-v1/` |
| `src/gateway/configs/schemas/bots.openapi.json` | 同步 Avernet 侧公开 JSON schema |
| `src/gateway/tests/fixtures/bots.openapi.json` | 若该 fixture 继续镜像公开 schema，则与配置 schema 同步更新 |
| `~/IdeaProjects/ocb/src/gateway/configs/schemas/bots.openapi.json` | 按双仓约束同步 OCB 侧同一公开 JSON schema；两侧内容必须一致 |
| 相关 OpenAPI/Core/Strategy tests | 覆盖新字段、legacy、互斥、engine gate 和副作用顺序 |

Context Boundary metadata 仅在 provides/requires 实际变化时更新，不为字段重命名制造
无意义边界变更。

---

## 10. 测试与验收

### 10.1 公共契约

- 新 `engine_properties.template` 非空对象请求通过 schema；
- 新 HTTP 请求中的 `template: null`、空对象和未知键明确失败；
- legacy `template` 在兼容窗口继续通过并标记 deprecated；
- 两者同时出现明确失败；
- Create 与 auth-completion echo schema 一致；
- OpenAPI 文档和 gateway schema 同步。

### 10.2 Strategy/Core

- `claude_code + engine_properties.template` 合法输入通过；
- `aicoding + engine_properties.template` 保持拒绝；
- openclaw/teclaw 等 default 引擎收到含 `template` 的 properties 时保持
  `BotCombinationUnsupportedError`（历史 409 映射），不变成 422；
- default engine 收到其他非空 properties 键明确失败；
- cloud/personal/personal-space、reserved 字段和类型校验保持现有行为；
- legacy application-coding 与新输入命中同一 Strategy；
- legacy application-coding 省略 config 时仍保留 `template_type="applicationCoding"`、`template_config=None` 和 `requires_workspace_hosting=True`；
- legacy 非 application-coding template 的 `template_type` 和 `template_config` 都不被清空；
- Core 直接收到未知 `engine_properties` 键时明确失败；
- workspace hosting 校验在 Passport 前；
- 同步和授权完成路径使用同一 preflight。

### 10.3 回归与架构

至少运行：

```bash
cd src/backend
uv run pytest <affected bot create tests>
uv run pytest tests/community/architecture

cd ../..
scripts/ci/python_sast_local.sh src/backend
```

PR Validation 必须记录实际执行结果；无法执行的 gate 需说明原因。

---

## 11. 风险与回滚

### R1. 公共字段迁移影响已有 caller

控制：已决定采用 deprecated 兼容窗口；删除旧字段单独提 breaking-change PR。
回滚：保留 legacy schema 和 Router 映射即可，不影响 Core Strategy。Avernet 与 OCB 的 gateway schema 必须同步修改或同步回滚。

### R2. 新结构没有 template discriminator

控制：明确限定当前一个模板语义；出现第二种真实类型时再版本化扩展。

### R3. 共享 Strategy 类意外放宽 aicoding engine

控制：`prepare_create()` 显式检查 `self.engine_type == "claude_code"`，增加回归测试。

### R4. 新旧来源覆盖顺序不明确

控制：Schema 和 Core 双层互斥校验，不提供覆盖或 merge 规则。

### R5. legacy 校验出现双份实现

控制：legacy application-coding 先标准化，再进入同一 Strategy；通用 create flow 不再
保存 application-coding 专属规则。

---

## 12. 已确认决策

本轮评审已确认：

1. 接受 D2 的 deprecated 兼容窗口。本次不直接删除已发布的 `template` 字段；
2. 接受 D3 的无 discriminator 结构，出现同一 engine 的第二种真实模板类型时再进行
   版本化扩展。

实现约束如下：

- 新旧来源互斥；
- legacy application-coding 省略 config 的既有契约保持不变；
- legacy application-coding 和新输入都由同一 Strategy 执行组合门禁和配置校验；
- application-coding 仍只允许 `claude_code`；
- Strategy 在 Core 层拒绝未知 `engine_properties` 键；
- legacy 非 application-coding 路径必须保留 `template_type`；
- `schemas.py`、Poll echo、Avernet/OCB 两侧 gateway schema 和契约测试均属于本次
  改动范围。
