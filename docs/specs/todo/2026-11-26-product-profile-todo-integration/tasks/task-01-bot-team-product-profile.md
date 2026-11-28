# Task 1：BotTeam Revision 发布为 ProductProfile

> 上级计划：[`../tasks.md`](../tasks.md)
> Spec：[`../spec.md`](../spec.md)
> Design：[`../design.md`](../design.md)
> 可独立交付：是
> 前置：现有 BotTeam/Template/Revision 创建和编辑能力
> 后续依赖者：Task 2

## 1. 背景和用户价值

用户在 Todo 中配置的是 BotTeam，而 Product Runtime 执行的是 ProductProfile。当前 BotTeam 已能创建不可变 Revision，但 Revision 没有绑定真实的 ProductProfile，因此后续 Todo 无法以稳定的运行定义创建 Collet。

本任务建立唯一转换边界：每个新发布 Revision 对应一个不可变 ProductProfile。用户继续只操作 BotTeam，协议细节由 Todo Backend 负责。

完成后应满足：

- 新 Revision 发布即具备稳定运行定义；
- 相同发布请求重试不会创建不同绑定；
- 后续 Todo 固定 Revision 后能直接取得 Profile ID；
- 历史 Revision 不受影响；
- Human approval 的产品语义不会因 Template 兼容展开而误入协议运行图。

## 2. 本任务范围

包含：

- 确认 ProductProfileService 五方法合同和 Profile 返回 ID；
- 为 Revision 增加 nullable `product_profile_id`；
- Repository 读写和 CAS；
- BotTeam Revision → composition/stages 的转换器；
- `create_product_profile()` 调用；
- BotTeam 创建/编辑发布流程集成；
- DI、错误映射、日志和测试；
- 更新 Todo README/Context Boundary（如行为发生变化）。

不包含：

- 创建 Collet；
- Dima 导入；
- Progress；
- Interaction/Notification；
- 协议层实现；
- 历史 Profile 回填；
- BotTeam DAG。

## 3. 开始前调查

执行并记录：

```bash
git status --short --branch
find apps/backend/src/agentclaw/community/core/task/todos -name AGENTS.md -o -name README.md
find apps/backend/src/agentclaw/community/core/repository -path '*todo*' -maxdepth 8 -type f
```

阅读：

```text
apps/backend/src/agentclaw/community/core/task/todos/services/todo_bot_team_service.py
apps/backend/src/agentclaw/community/core/task/todos/bot_team_models.py
apps/backend/src/agentclaw/community/core/task/todos/bot_team_service_protocol.py
apps/backend/src/agentclaw/community/core/repository/protocols/task/todo_templates.py
现有 template/version repository model 与 SQL
apps/backend/src/agentclaw/community/di/modules/task/todo_bindings.py
apps/backend/src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/contracts.py
ProductProfile/MethodCompositionTemplate 的公开类型定义
```

先搜索当前分支是否已经有：

```text
product_profile_id
create_product_profile
MethodCompositionTemplate
ProductProfileServiceProtocol
```

如已有同义实现，必须扩展，不得重复建字段、service 或 migration。

## 4. 输入和输出

### 输入

- 已校验 BotTeam draft；
- 即将发布的 immutable Revision ID；
- BotTeam 节点：code/name/bot_id/completion gate/approval flag；
- tenant/env/space；
- 已认证 Human；
- 产品运行 composition 配置。

### 输出

- 已创建 ProductProfile；
- 已发布 Revision；
- Revision 上稳定 `product_profile_id`；
- 对外 BotTeam Revision VO 可按需要返回“运行定义已就绪”，但不必暴露完整 YAML；
- 失败时 Revision 不得以“可用于 Product Runtime”的状态发布。

## 5. 候选修改文件

以实际代码树为准，优先增量修改：

```text
apps/backend/src/agentclaw/community/core/task/todos/services/todo_bot_team_service.py
apps/backend/src/agentclaw/community/core/task/todos/bot_team_models.py
apps/backend/src/agentclaw/community/core/task/todos/bot_team_service_protocol.py
apps/backend/src/agentclaw/community/core/task/todos/README.md
apps/backend/src/agentclaw/community/core/repository/protocols/task/todo_templates.py
apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/models.py
apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/
apps/backend/src/agentclaw/community/di/modules/task/todo_bindings.py
apps/backend/tests/community/core/task/todos/test_todo_bot_team_service.py
apps/backend/tests/community/repository/task/todos/
apps/backend/tests/community/architecture/
```

如果 migrations/SQL 位于其他目录，按 Backend README 和现有迁移方式落地，不自行创造第二套迁移机制。

## 6. TDD 场景清单

实现前先写失败测试。

### 6.1 转换器

- 一个节点生成一个协议 stage；
- 两个节点按顺序生成 `depends_on`；
- node code 用作稳定 stage identity；
- candidate 使用正确 Bot ID；
- completion gate 转为 acceptance；
- 重复 code 在协议调用前失败；
- 空节点失败；
- 不可用 Bot 失败；
- 合成 `<node>__approval` Human Stage 不进入 ProductProfile；
- 相同 Revision 输入生成稳定相同 YAML。

### 6.2 Profile 调用

- 调用 `create_product_profile()` 的 name/composition/stages 正确；
- ActorContext 使用当前已认证 Human；
- request ID 确定且重试相同；
- composition 配置缺失 fail closed；
- 协议明确失败时 Revision 不发布；
- 协议超时后相同发布重试复用 request ID。

### 6.3 Repository

- 新 Revision 可保存 Profile ID；
- `NULL → id` 成功；
- `id → same id` 幂等成功；
- `id A → id B` 冲突且 A 不变；
- tenant/env 不能串读或串写；
- 历史 NULL Revision 可读；
- 并发绑定只有一个最终值。

### 6.4 发布流程

- 创建 BotTeam 自动发布首 Revision并绑定 Profile；
- 编辑 BotTeam 创建新 Revision和新 Profile，旧 Revision 不变；
- 本地发布失败后重试可绑定协议已创建的同一个 Profile；
- 已发布 Revision 不可修改 Profile ID；
- archive 不删除 Profile binding；
- API/VO 不泄漏协议内部 payload。

## 7. 实施步骤

### Step 1：建立公开产品模型字段

在 Revision 的 Core model/record/VO 中增加：

```python
product_profile_id: int | None
```

注意：

- Repository record 可暴露此字段；
- 面向 Frontend 的 VO 是否直接返回 ID取决于现有安全/产品合同；若无用户价值，可只返回 `runtime_ready: bool`；
- 不要将完整 composition/stages YAML存入 BotTeam DTO，除非现有审计合同要求；
- 不更名 Template/Version 物理字段。

### Step 2：数据库迁移和 Repository

增加 nullable column 和索引。然后扩展：

- ORM model；
- row mapper；
- create/read/list；
- CAS bind 方法；
- real database tests。

建议结果类型：

```python
@dataclass(frozen=True)
class ProductBindingResult:
    state: Literal["bound", "already_bound", "conflict"]
    value: int
```

不要用裸 `bool` 隐藏冲突原因。

### Step 3：composition 配置边界

定义 Todo 产品层自己的配置值对象或 factory protocol，例如：

```python
class TodoProductCompositionFactoryProtocol(Protocol):
    def build(self, *, tenant: str, env: str) -> MethodCompositionTemplate: ...
```

生产来源必须来自项目已有配置系统。配置缺失时抛稳定领域错误，例如：

```text
product_profile_composition_unavailable
```

测试注入固定 Fake。不得在 service 中硬编码测试 capability ref。

### Step 4：实现 Revision → Profile 转换器

转换器职责仅为纯转换和验证：

```python
build_product_profile_spec(revision, nodes) -> ProductProfileSpec
```

`ProductProfileSpec` 至少包含：

```text
name
composition
stages_yaml
```

转换器不得：

- 调数据库；
- 调 ProductProfileService；
- 查询当前用户；
- 修改 Revision；
- 根据 Runtime 状态改变 stage。

对 `approval_required`：基于 BotTeam 原始 node 转换；如果只能取得展开后的 Template Stages，必须显式识别 synthetic Human stage 并有测试证明不会误传。

### Step 5：实现 request ID builder

集中函数：

```python
build_profile_request_id(tenant, env, revision_id) -> str
```

建议格式：

```text
todo:profile:{tenant}:{env}:{revision_id}:v1
```

如果 request ID 有长度/字符限制，使用稳定 hash，但测试必须验证同输入同输出、不同 tenant/env/revision 不碰撞。

### Step 6：集成发布流程

推荐拆出 orchestration service，避免让现有 BotTeam service 同时承担纯转换和外部调用：

```text
TodoBotTeamService
→ validate/build revision
→ ProductProfile publisher
→ repository publish transaction
```

完整顺序：

1. 校验 draft 和 Bot eligibility；
2. 确定 immutable revision ID；
3. 构造 Profile spec；
4. 构造 ActorContext；
5. 调 `create_product_profile()`；
6. 获取稳定 Profile ID；
7. 本地 transaction 创建/确认 Revision、CAS 绑定、发布；
8. 返回 Revision。

若现有 Revision ID 只能在 insert 后产生：

- 优先检查是否可在应用层预生成；
- 若必须先 insert，创建未发布 draft revision，并保证它不可被 Todo 选择；
- Profile 创建成功后同一事务绑定并发布；
- 失败 draft 可以补偿/重试，但不能展示为 published；
- 不要为绕过问题先发布再补 Profile。

### Step 7：DI 和 Adapter

- DI 注入 `ProductProfileServiceProtocol`；
- 不 import 具体实现；
- HTTP adapter 只传认证 actor 和产品请求；
- ProductProfileService 错误映射成稳定 Todo 错误码；
- 5xx/timeout 不暴露内部堆栈或私密 payload。

### Step 8：文档更新

若实现改变 Todo durable boundary：

- 更新 Todo README 对 BotTeam Revision 的描述；
- 更新 Context Boundary provides/requires；
- 如新增配置，更新配置文档；
- 不修改旧 Spec，只在实现文档引用本 Spec。

## 8. 幂等、并发和失败补偿

### 幂等

```text
request_id = revision identity + operation version
```

协议成功、本地失败后重试：

1. 使用同 request ID调用；
2. 预期协议返回同 Profile 或幂等结果；
3. CAS 绑定；
4. 相同 ID成功；
5. 不同 ID冲突并报警。

### 并发

两个请求同时发布同一 draft：

- 现有 Revision version fence先阻止重复 Revision；
- 即便两者均调到协议，确定 request ID必须相同；
- 本地只允许一个 Profile ID绑定；
- 冲突不能 last-write-wins。

### 失败补偿

| 失败点 | 处理 |
| --- | --- |
| 输入校验 | 不调用协议，不创建 Revision |
| composition 缺失 | fail closed，不调用协议 |
| create Profile 明确失败 | Revision 不发布，可人工/自动重试 |
| create Profile timeout | 标记结果未知；相同 request ID重试 |
| Profile 成功、本地事务失败 | 相同 request ID重试并 CAS |
| CAS 发现不同 ID | 返回 binding conflict，停止自动处理 |

## 9. 测试执行

最低 focused：

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/todos/test_todo_bot_team_service.py \
  tests/community/repository/task/todos \
  tests/community/architecture
```

再执行受影响 HTTP 测试和完整 Backend CI：

```bash
cd apps/backend
bash scripts/ci_test.sh
```

最后：

```bash
git diff --check
git diff --check --cached
```

## 10. 验收 Checklist

- [ ] 协议目录无修改；
- [ ] 新字段 nullable，不回填历史 Revision；
- [ ] 新发布 Revision 均绑定 Profile；
- [ ] 历史 Revision 仍可读；
- [ ] BotTeam create/edit 都接入；
- [ ] Profile input 对相同 Revision 稳定；
- [ ] `approval_required` 合成 Human Stage 未误入协议 stages；
- [ ] composition 生产配置缺失时 fail closed；
- [ ] request ID确定且可重试；
- [ ] CAS 三种结果均有测试；
- [ ] 协议成功、本地失败的恢复有测试；
- [ ] ActorContext 来自认证用户；
- [ ] DI 依赖 Protocol 而非具体实现；
- [ ] focused tests、架构测试、完整 CI 状态已报告。

## 11. 与其他任务的边界

Task 1 只保证：

```text
Revision.product_profile_id 可用
```

Task 2 才负责：

```text
Todo.product_collet_id
Dima source atomic creation
Scheduler runtime split
```

不得在 Task 1 顺手创建 Collet 或修改 Todo dispatch。

## 12. Agent 停止条件

发现以下任一情况，停止实施并提交证据：

- `ProductProfile` 返回值没有稳定 ID；
- composition 没有合法生产配置来源；
- 当前 Revision 发布模型无法提供“未发布但可补偿”的中间态，且先发布会产生错误可见性；
- 合并代码已有另一个 Profile binding 字段/表；
- 必须修改 ProductProfileService/L5 才能转换或创建；
- `approval_required` 无法区分原始节点和合成 Human Stage；
- 数据库迁移可能强制回填或锁表风险超出项目规则。
