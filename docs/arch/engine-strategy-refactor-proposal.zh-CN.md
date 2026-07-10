# 重构提案：让 bot_service 保持“引擎无关”，把引擎特有逻辑收进策略

> 面向对象：初级同学
> 关联 PR：[#61 fix(bot): personalCoding 模板同 applicationCoding 下发 model/runtime/token](https://github.com/inclusionAI/Avernet/pull/61)
> 关键词：策略模式 / 扩展点（Extension Point）/ 引擎无关（engine-agnostic）

---

## 0. 一句话结论

PR #61 想做的事情是**对的**（让 `personalCoding` 和 `applicationCoding` 一样能下发
`model / runtime / token`），但**改错了地方**。它把“aicoding 引擎才懂的规则”一条条塞进了
本该“引擎无关”的公共服务里，而且**同一条判断复制在了 8 个地方**。

正确的做法是：**公共服务不认识任何具体引擎，只调用一个策略接口；每个引擎把自己的特殊
规则封装在自己的策略类里。** 这样这次的改动就会**只落在 aicoding 一个类里**，改一处、
测一处、只影响一个引擎，风险最小。

这套方法不是只为 `bot_service` 服务的，而是**整个代码库通用的**：凡是看到
`if xxx_type == "..."` 在公共代码里散落多处，都可以用同样的方式收敛。

---

## 1. PR #61 到底改了什么

需求本身很简单：以前只有 `applicationCoding` 能把 `model / runtime / token` 下发进容器，
`personalCoding` 携带了这些字段却被静默丢弃。PR 想让 `personalCoding` 也生效。

但看它的 diff，一个简单需求改了 **10 个文件、6 处生产代码、4 个测试文件**，每一处都是同一
个动作——把 `== "applicationCoding"` 放宽成 `in ("applicationCoding", "personalCoding")`：

| 文件 | 改动 |
| --- | --- |
| `bot_management/utils.py` | `build_aix_extra_envs`：`== "applicationCoding"` → `in (...)` |
| `bot_management/services/bot_service.py` | `update_bot` token 刷新门控放宽 |
| `bot_management/services/template_service.py` | `_encrypt_token_field` 门控放宽 |
| `devices/services/baas_device_service.py` | `run_create_init_once` token 门控放宽 |
| `devices/services/baas_publish_task_handlers.py` | `_read_codefuse_token` 门控放宽 |
| `devices/services/device_service.py` | `apply_device` token 门控放宽 |

这就是典型的 **“霰弹式修改”（Shotgun Surgery）**：一个概念上的小改动，被迫在很多文件里
重复同一处修改。它的坏处很直接：

1. **容易漏改**。这次一共 8 处 `applicationCoding` 门控，只要漏掉一处，就会出现
   “token 加密了但下发时被过滤掉”这类难查的半截 bug。
2. **公共服务被“污染”**。`bot_service` / `device_service` / `template_service` 本应对所有
   引擎一视同仁，现在却越来越懂 aicoding 的内部细节（`codefuse` token、`RELAY_DEFAULT_MODEL`、
   `applicationCoding` 与 `personalCoding` 的区别）。
3. **牵一发动全身**。一个只该影响 aicoding 的改动，却要动到所有引擎都会走的主干代码——
   审查者无法一眼确认“别的引擎不受影响”，风险被放大。

> 这正是你（Reviewer）拒绝它的原因：**aicoding 特有的逻辑侵入了引擎无关的服务。**

---

## 2. 问题的本质：在公共代码里“按类型分叉”

把散落各处的门控收集起来看，它们其实是同一个模式（下面是 PR 之前的样子）：

```python
# utils.py
if (template_type or "") == "applicationCoding":
    ... 写 RELAY_DEFAULT_MODEL / RELAY_DEFAULT_RUNTIME ...

# template_service.py
if template_type != "applicationCoding":
    return template_config          # 不加密 token

# device_service.py / baas_device_service.py / baas_publish_task_handlers.py
raw_token = template_config.get("token") if template_type == "applicationCoding" else None

# bot_service.py
if bot.get("template_type") == "applicationCoding" and "token" in template_config:
    self._maybe_refresh_codefuse_token_async(...)
```

这些 `applicationCoding` / `personalCoding` / `codefuse` / `RELAY_DEFAULT_*` 全都是
**aicoding 引擎的领域知识**。它们被硬编码进了对所有引擎通用的服务里。

一旦这样写，规则就“碎”成了 N 份副本，散落在 N 个文件。任何调整（比如这次多支持一种模板）
都必须同时改 N 份。**问题不在于这次改得对不对，而在于“规则被复制了 N 份”这件事本身。**

---

## 3. 目标

我们希望重构后达到：

- ✅ **公共服务引擎无关**：`bot_service` / `device_service` / `template_service` 里不再出现
  `applicationCoding` / `codefuse` / `RELAY_DEFAULT_*` 这类具体引擎词汇。
- ✅ **引擎规则单点封装**：aicoding 的所有特殊规则集中在一个（或一组）aicoding 策略类里。
- ✅ **改动可局部化**：像 PR #61 这种需求，最终**只改 aicoding 策略一个文件**，其它引擎代码
  一行都不用动，审查者一眼就能确认影响范围。
- ✅ **通用**：同样的手法能套用到代码库其它“按 `engine_type` / `template_type` / `bot_type`
  分叉”的地方。
- ✅ **所有权可自治**：每个引擎的策略独占一个目录，配上自己的 `CODEOWNERS`，引擎负责人
  即可自审自己引擎的改动，不必再请公共服务（TC）负责人 approve（见 §6）。

---

## 4. 方案：用“引擎策略（Extension Point）”替代散落的 if

### 4.1 好消息：这个模式代码库里已经有了

我们不需要发明新东西。仓库里**已经存在**一个标准的“按引擎分派”的扩展点，就在
`core/workspace/`：

```python
# core/workspace/engine_sandbox.py
@runtime_checkable
class EngineSandboxProvider(Protocol):
    @property
    def engine_type(self) -> str: ...
    def get_base_path(self) -> str: ...
    def get_build_plan(self) -> EngineBuildPlan: ...
    ...

class EngineSandboxRegistry:
    def register(self, provider: EngineSandboxProvider) -> None:
        self._providers[provider.engine_type] = provider
    def resolve(self, engine_type: str) -> EngineSandboxProvider:
        ...
```

```python
# core/workspace/engines/__init__.py  —— 组装根（Composition Root）
registry = EngineSandboxRegistry()
registry.register(OpenClawSandboxProvider(workspace=workspace))
registry.register(ClaudeCodeSandboxProvider(workspace=workspace))
```

这正是架构宪法（`docs/arch/arch.rules.md`）里说的 **Plugin API / Extension Point**：
内核/核心定义一个能力接口，具体引擎作为 Provider 去实现，在**组装根**处注册；核心代码只面向
接口调用，永远不认识具体是哪个引擎。

> 本提案要做的，就是把这个**已经成立**的模式，从 “sandbox 路径” 扩展到 “容器下发参数 /
> token / 创建后钩子” 这些目前还在用 `if template_type == ...` 硬判的地方。

### 4.2 定义引擎的“下发能力”接口（Plugin API）

新增一个引擎侧的能力接口，把当前散落的门控收敛成几个语义清晰的方法：

```python
# core/bot_management/engines/provisioning.py（新增）
from __future__ import annotations
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class EngineProvisioningStrategy(Protocol):
    """引擎的“容器下发/落库”能力。核心服务只面向本接口，不认识具体引擎。"""

    @property
    def engine_type(self) -> str: ...

    def build_container_envs(
        self, *, template_type: Optional[str], template_config: Optional[Dict[str, Any]]
    ) -> Dict[str, str]:
        """返回要注入容器的额外环境变量（如 BOT_TYPE / RELAY_DEFAULT_MODEL）。
        默认引擎返回空 dict。"""

    def deploy_token(
        self, *, template_type: Optional[str], template_config: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """返回需要下发进容器的密钥明文（如 codefuse token）；不适用则返回 None。"""

    def should_persist_encrypted_token(self, *, template_type: Optional[str]) -> bool:
        """落库前是否需要加密 template_config['token']。"""

    def on_bot_created(self, ctx: "BotCreatedContext") -> None:
        """创建成功后的引擎钩子（如 DIMA workspace、memoryOS 初始化）。默认 no-op。"""
```

### 4.3 默认实现：什么都不做（保证“引擎无关”）

```python
# core/bot_management/engines/default.py（新增）
class DefaultProvisioningStrategy:
    """所有不需要特殊下发的引擎（openclaw / moltis / hermes ...）共用。"""

    def __init__(self, engine_type: str) -> None:
        self._engine_type = engine_type

    @property
    def engine_type(self) -> str:
        return self._engine_type

    def build_container_envs(self, *, template_type, template_config) -> dict:
        return {}

    def deploy_token(self, *, template_type, template_config):
        return None

    def should_persist_encrypted_token(self, *, template_type) -> bool:
        return False

    def on_bot_created(self, ctx) -> None:
        return None
```

### 4.4 aicoding 策略：把所有 coding 规则集中到这里

**PR #61 想改的所有语义，最终只应存在于这一个类里。** 注意这里也是唯一一处需要知道
`applicationCoding` / `personalCoding` 区别、`codefuse`、`RELAY_DEFAULT_*` 的地方：

```python
# core/bot_management/engines/aicoding.py（新增）
CODING_TEMPLATE_TYPES = {"applicationCoding", "personalCoding"}  # 单一事实来源


class AicodingProvisioningStrategy:
    """aicoding / claude_code 引擎的下发规则。所有 coding 特有知识集中于此。"""

    def __init__(self, engine_type: str, ...deps...) -> None:
        self._engine_type = engine_type
        ...

    @property
    def engine_type(self) -> str:
        return self._engine_type

    def build_container_envs(self, *, template_type, template_config) -> dict:
        # 原 build_aix_extra_envs 的逻辑整体搬进来
        envs = _build_bot_type_and_repo_envs(template_type, template_config)
        if template_type in CODING_TEMPLATE_TYPES and template_config:   # ← 唯一门控
            model = template_config.get("model")
            if isinstance(model, str) and model.strip():
                envs["RELAY_DEFAULT_MODEL"] = model.strip()
            runtime = template_config.get("runtime")
            if isinstance(runtime, str) and runtime.strip():
                envs["RELAY_DEFAULT_RUNTIME"] = runtime.strip()
        return envs

    def deploy_token(self, *, template_type, template_config):
        if template_type not in CODING_TEMPLATE_TYPES:
            return None
        return (template_config or {}).get("token")

    def should_persist_encrypted_token(self, *, template_type) -> bool:
        return template_type in CODING_TEMPLATE_TYPES

    def on_bot_created(self, ctx) -> None:
        if ctx.template_type == "applicationCoding":
            self._ensure_dima_workspace(ctx)
            self._trigger_memory_init(ctx)
```

> 关键：**`in ("applicationCoding", "personalCoding")` 这一句，从此只存在于 aicoding 策略里，
> 而不是散落在 6 个公共文件中。** 这就是 PR #61 应该改的地方——一处，而不是八处。

### 4.5 目录布局：一个引擎一个目录

按引擎分目录存放（而不是所有引擎挤在一个文件里），这样每个引擎都能独立演进、独立配
`CODEOWNERS`（见 §6）：

```text
core/bot_management/engines/
├── provisioning.py          # 接口 + Registry（公共契约，TC 维护）
├── default.py               # 默认 no-op（公共）
├── aicoding/                # ← aicoding 引擎自己的目录
│   ├── __init__.py
│   └── strategy.py          # AicodingProvisioningStrategy + CODING_TEMPLATE_TYPES
├── openclaw/                # ← openclaw 引擎自己的目录（如需特化）
│   └── strategy.py
└── registry.py              # 组装根：把各引擎策略注册进来
```

### 4.6 在组装根注册（Composition Root）

```python
# core/bot_management/engines/registry.py（新增）
def create_provisioning_registry(...deps...) -> EngineProvisioningRegistry:
    reg = EngineProvisioningRegistry()
    for eng in ("openclaw", "moltis", "hermes", "teclaw"):
        reg.register(DefaultProvisioningStrategy(eng))
    reg.register(AicodingProvisioningStrategy("aicoding", ...))
    reg.register(AicodingProvisioningStrategy("claude_code", ...))
    return reg
```

`EngineProvisioningRegistry` 直接复用现成 `EngineSandboxRegistry` 的写法即可（`register` /
`resolve`），`resolve` 未命中时回退到 `DefaultProvisioningStrategy`，保证新引擎默认“引擎无关”。

---

## 5. Before / After 对照

### 公共服务从“认识 aicoding”变成“只调接口”

**Before（PR #61 之后，公共服务里）：**

```python
# bot_service.py —— 服务自己判断引擎细节
if resolved_active_engine in ("claude_code", "aicoding") and \
        template_type in ("applicationCoding", "personalCoding"):
    from ...utils import build_aix_extra_envs
    extra_envs = build_aix_extra_envs(template_config, template_type=template_type)
```

**After（引擎无关）：**

```python
# bot_service.py —— 服务不认识任何引擎，只调策略
strategy = self._provisioning_registry.resolve(resolved_active_engine)
extra_envs = strategy.build_container_envs(
    template_type=template_type, template_config=template_config
) or None
```

`template_service` / `device_service` / `baas_*` 同理：

```python
# 加密门控
if strategy.should_persist_encrypted_token(template_type=template_type):
    ...

# token 下发
raw_token = strategy.deploy_token(template_type=template_type, template_config=template_config)
```

### PR #61 的需求在 After 世界里长什么样

只需在 `AicodingProvisioningStrategy.build_container_envs` 里，把门控从
`== "applicationCoding"` 改成 `in CODING_TEMPLATE_TYPES`——**一个文件、一处改动、一份新增
单测**。公共服务、其它引擎、device 层**一行都不用动**。

| | PR #61 现状 | 重构后 |
| --- | --- | --- |
| 生产代码改动文件数 | 6 | **1** |
| 门控副本数量 | 8 处散落 | **1 处集中** |
| 影响范围 | 所有引擎的主干代码 | **仅 aicoding 策略** |
| 漏改风险 | 高（易漏门控） | 低（只有一处） |
| 审查成本 | 需通读多文件确认无副作用 | 看一个类即可 |

---

## 6. 附带收益：按引擎分目录 + CODEOWNERS，引擎负责人自审

这一点对研发效率很关键，值得单独说。

抽出来之后，每个引擎的策略实现都独占一个目录（§4.5）。我们的仓库**已经在用**
`.github/CODEOWNERS` 做按目录的所有权管理：

```text
# .github/CODEOWNERS（现状节选）
/src/backend/   @totalfrank @xianmuyq        # 整个 backend 由公共/TC 负责人兜底
/src/engine/    @totalfrank @xianmuyq
/src/baas/      @cassiuscai @pfmiles @phoenixliu ...
```

现状的问题是：**aicoding 引擎的下发规则今天散落在 `bot_service` / `device_service` /
`template_service` 里，这些文件都归 `/src/backend/` 的公共/TC 负责人管。** 于是每次只改
aicoding 一个引擎的行为（比如 PR #61），也必须请 TC 负责人 approve——TC 成了瓶颈，引擎负责人
反而不能对自己的引擎拍板。

抽成“一个引擎一个目录”之后，只要给每个引擎目录**加一行 CODEOWNERS**：

```text
# .github/CODEOWNERS（重构后新增）
# 公共契约（接口 / Registry / 默认实现）仍由 TC 把关
/src/backend/src/agentclaw/community/core/bot_management/engines/provisioning.py   @totalfrank @xianmuyq
/src/backend/src/agentclaw/community/core/bot_management/engines/default.py        @totalfrank @xianmuyq

# 各引擎目录交给各自负责人自治
/src/backend/src/agentclaw/community/core/bot_management/engines/aicoding/   @aicoding-owner-a @aicoding-owner-b
/src/backend/src/agentclaw/community/core/bot_management/engines/openclaw/   @openclaw-owner
```

> CODEOWNERS 的匹配规则是**后面的行覆盖前面的行**，所以更精确的引擎目录行会覆盖
> `/src/backend/` 的兜底行——这套“module-specific owners override 兜底”的写法，我们仓库现有
> CODEOWNERS 里已经在用。

带来的效果：

- ✅ **引擎负责人自审自己的引擎**。像 PR #61 这种“只改 aicoding”的改动，diff 完全落在
  `engines/aicoding/` 目录里，由 aicoding 负责人 review + approve 即可合入，**不需要 TC 负责人
  参与**。
- ✅ **公共契约仍由 TC 把关**。只有当有人动到 `provisioning.py`（接口本身）或公共服务的调用
  方式时，才会触发 `/src/backend/` 或接口文件的 TC owner——该管的地方管住，不该管的地方放手。
- ✅ **权责一致**。谁负责这个引擎，谁就对这个引擎的目录负责，出问题也定位清晰。

**注意前提**：这条收益能成立，恰恰是因为 §4 把引擎逻辑**物理隔离**到了独立目录。只要逻辑还
散落在公共文件里，CODEOWNERS 就无从按引擎切分——这也是“先抽出来”最实际的回报之一。

---

## 7. 安全的迁移步骤（不要一次推倒重来）

对初级同学最重要的一点：**这是“搬家”，不是“重写”。** 用“绞杀者”（Strangler Fig）方式，
小步替换、每步测试全绿，把风险压到最低：

1. **先补“定性测试”**。为现有 8 处门控的现状行为补齐单测（哪些写、哪些不写），把当前行为
   钉死。后续每一步都必须让这些测试保持绿色——这是安全网。
2. **只搬代码，不改语义**。新增接口 + 默认实现 + aicoding 策略，把现有逻辑**原样**搬进去
   （此时 aicoding 策略仍然只认 `applicationCoding`，行为与今天完全一致）。
3. **逐个改造调用点**。一次只把一个调用点（先从 `build_aix_extra_envs` 开始）改成走
   `strategy.xxx()`，跑测试，绿了再改下一个。切忌一次全改。
4. **合并单一事实来源**。把重复的 `CODING_TEMPLATE_TYPES`（现在 `baas_template_resolver.py`
   和 `arca_bot_create_baas_rollout_policy.py` 各定义了一份）统一到策略模块。
5. **最后才做行为变更**。所有调用点都走策略之后，再实现 PR #61 的真正需求：在 aicoding
   策略里把门控放宽到 `personalCoding`。这一步的 diff 会小到令人安心。

> 前 4 步是**纯重构**（行为不变，可独立合入、独立回滚）；第 5 步才是**功能变更**。
> 把两者拆成不同 PR，审查和回滚都更从容。

---

## 8. 通用化：这套方法适用于整个代码库

这份提案虽然从 `bot_service` 出发，但给出的是一个**通用规则**，请在日常开发中作为准绳：

> **公共/核心代码里，不要按 `engine_type` / `template_type` / `bot_type` 等“类型码”写
> `if / elif` 分叉去调用特有逻辑。把该逻辑定义成一个能力接口（Plugin API / Extension
> Point），由各类型的 Provider/Strategy 实现，在组装根注册，核心只面向接口调用。**

识别信号（闻到这些“坏味道”就该考虑用策略收敛）：

- 同一个 `if type == "X"` 判断出现在多个文件里（本例的 8 处门控）。
- 公共服务里出现某个具体引擎/模板的专有名词（`codefuse`、`RELAY_DEFAULT_*`、`DIMA`、
  `memoryOS`）。
- 加一种新类型时，要“到处找地方加分支”。

代码库里已经这样做的正面例子，可直接参照抄作业：

- `core/workspace/engine_sandbox.py` + `engines/`：按引擎分派 sandbox 能力。
- `core/devices/services/baas_codefuse_writer.py`：codefuse 写入已从公共 device 服务里拆出。
- `core/bot_management/services/engine_resolver.py`：引擎类型解析集中一处。

架构宪法里对应的条款（可引用给团队）：

- **Plugin API / Extension Point**：核心通过接口调用外部能力（`arch.rules.md` Part I）。
- **Composition Root**：具体实现只在组装根处注册，核心不 `import` 具体引擎。
- **Single Authority / 一致术语**：`CODING_TEMPLATE_TYPES` 这类定义应有单一事实来源。

---

## 9. 给作者的落地清单（Checklist）

- [ ] 新增 `core/bot_management/engines/`：`provisioning.py`（接口 + Registry）、`default.py`、
      `registry.py`（组装根），并**按引擎分目录**放策略（`engines/aicoding/`、`engines/openclaw/`…）。
- [ ] 把 `build_aix_extra_envs` / token 加密 / token 下发 / 创建后钩子的逻辑**原样搬入**策略，
      公共服务改为 `resolve(engine).xxx(...)`。
- [ ] 公共服务（`bot_service` / `device_service` / `template_service` / `baas_*`）中不再出现
      `applicationCoding` / `personalCoding` / `codefuse` / `RELAY_DEFAULT_*` 字样。
- [ ] `CODING_TEMPLATE_TYPES` 合并为单一定义。
- [ ] 给各引擎目录补 `.github/CODEOWNERS` 行（引擎负责人自治，接口/默认实现仍归 TC）。
- [ ] 保留并跑绿全部现有单测；策略类补齐自己的单测。
- [ ] **纯重构**与 **personalCoding 功能变更**拆成两个 PR。

---

**核心思想再重复一遍：** 让公共服务对引擎“一无所知”，把“懂 aicoding 的那部分知识”收进
aicoding 自己的策略类。这样每次引擎特有的改动都只落在一个地方——**改一处、测一处、
只影响一个引擎**。
