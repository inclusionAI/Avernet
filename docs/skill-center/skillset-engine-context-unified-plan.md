# SkillSet Engine Context 统一方案：Skill / MCP / CLI 拉通

## 1. 背景

当前 `claude_code + non-normalCC` Bot 场景中，`bot.active_engine` 和实际运行时 engine 存在分叉：

```text
bot.active_engine = claude_code
template_type != normalCC

persisted/data engine = claude_code
runtime/layout engine = aicoding
```

现有代码里大量地方只传一个 `engine_type`，导致这个字段同时承担了多种语义：

```text
1. SkillSet 数据归属
2. active SkillSet 查询条件
3. MCP AgentPass 授权查询条件
4. Skill symlink source/target 路径生成
5. CLI 同步路径判断
6. build/config/runtime provider 选择
```

当 `resolve_engine_for_bot()` 被增强为支持：

```text
claude_code + non-normalCC => aicoding
```

后，SkillSet 链路中原本使用 `resolve_engine_for_bot()` 的地方也会拿到 `aicoding`，从而暴露出“数据归属 engine”和“运行时 engine”混用的问题。

## 2. 当前暴露的问题

### 2.1 Skill 加入 SkillSet 后误 clean

现象：

```text
POST /api/skillsets/{skill_set_id}/skills
预期：sync_symlinks([...]) => bindpath
实际：sync_symlinks([]) => clean
```

链路：

```text
Bot.active_engine = claude_code
Bot.template_type != normalCC
resolve_engine_for_bot() => aicoding
SkillSetService.engine_type = aicoding

目标 SkillSet.engine_type = claude_code
add_skill_to_set 按 id 写入成功
get_active_skills(engine_type=aicoding) 查不到 claude_code active SkillSet
get_symlink_mappings() 返回 []
sync_symlinks([]) 被设备侧解释为 clean
```

### 2.2 SkillSet 加 MCP 后 AgentPass 未授权

现象：

```text
创建 SkillSet 时 engine_type = aicoding
AgentPass 授权查询 active SkillSet 时传入 active_engine = claude_code
查询不到 active SkillSet
未进行 MCP 授权
```

本质：

```text
SkillSet 数据被写到了 aicoding 归属
但 AgentPass 按 claude_code 归属查询
```

### 2.3 SkillSet 改为 claude_code 后 CLI 不同步到 ~/.claude/skills

现象：

```text
如果把 SkillSet.engine_type 修为 claude_code，
MCP 授权查询能按 claude_code 查到了，
但 CLI 同步又没有落到 ~/.claude/skills/。
```

本质：

```text
CLI 同步逻辑把 SkillSet.engine_type 当成 runtime/layout engine 使用。

但 SkillSet.engine_type 应该只表示数据归属；
CLI 同步路径应该由 runtime_engine 决定。
```

## 3. 根因总结

根因不是某个 API 单独错了，而是一个字段承担了两个 engine 语义：

```text
SkillSet.engine_type / service.engine_type
既被当成 DB 数据归属 engine，
又被当成 runtime path/layout engine。
```

在普通场景：

```text
persisted_engine == runtime_engine
```

所以问题不明显。

但在特殊场景：

```text
persisted_engine = claude_code
runtime_engine   = aicoding
```

混用就会导致：

```text
DB 查不到
授权查不到
软链空快照
CLI 不同步
```

## 4. 最终方案

引入统一的 Bot Engine Context，显式区分两个 engine：

```python
@dataclass(frozen=True)
class BotEngineContext:
    persisted_engine: str
    runtime_engine: str
```

新增 resolver：

```python
resolve_persisted_engine_for_bot()
# 返回 bot.active_engine
# 用于 SkillSet / DB / active 查询 / AgentPass 授权查询 / NAS bucket 归属

resolve_runtime_engine_for_bot()
# 返回 resolve_bot_engine(bot) or bot.active_engine
# 用于 build / config / provider / runtime path / Skill source / CLI sync

resolve_engine_context_for_bot()
# 同时返回 persisted_engine + runtime_engine
```

兼容旧入口：

```python
resolve_engine_for_bot()
# 继续保持 runtime 语义
# 等价于 resolve_runtime_engine_for_bot()
```

注意：不要把现有 `resolve_engine_for_bot()` 全局改成 persisted 语义，否则会破坏 aicoding prepub build 相关逻辑。

## 5. Engine 使用矩阵

| 场景 | 应使用 engine | 说明 |
|---|---|---|
| 创建 SkillSet | persisted_engine | SkillSet 数据归属 |
| 更新 SkillSet | persisted_engine | DB 归属 |
| 删除 SkillSet | persisted_engine | DB 归属 |
| 激活/取消激活 SkillSet | persisted_engine | active 状态归属 |
| 查询 active SkillSet | persisted_engine | AgentPass/Skill/MCP/CLI 都应统一 |
| SkillSet 加 Skill | persisted_engine | membership 数据归属 |
| SkillSet 删 Skill | persisted_engine | membership 数据归属 |
| SkillSet 加 MCP | persisted_engine | MCP 关联数据归属 |
| SkillSet 删 MCP | persisted_engine | MCP 关联数据归属 |
| SkillSet 加 CLI | persisted_engine | CLI 关联数据归属 |
| SkillSet 删 CLI | persisted_engine | CLI 关联数据归属 |
| AgentPass 授权查询 active SkillSet | persisted_engine | 避免 aicoding/claude_code mismatch |
| get_active_skills | persisted_engine | 查 DB active SkillSet |
| get_symlink_mappings 查询 active skill | persisted_engine | 查询阶段用数据归属 |
| get_symlink_mappings 生成 source/target | runtime_engine | 路径布局阶段用运行时 |
| Skill git repo source path | runtime_engine | aicoding source 在 `.aicoding/skills-repo` |
| CLI runtime sync | runtime_engine | 决定同步到 `~/.claude/skills` 等 runtime 位置 |
| build provider/build plan | runtime_engine | 保持 aicoding prepub build 功能 |
| source NAS bucket | persisted_engine | 对应 6d7b79b 的 active_engine 归属 |

## 6. 目标行为

对于：

```text
bot.active_engine = claude_code
template_type != normalCC
```

统一得到：

```text
persisted_engine = claude_code
runtime_engine   = aicoding
```

### 6.1 SkillSet 创建

应写入：

```text
SkillSet.engine_type = claude_code
```

不应写入：

```text
SkillSet.engine_type = aicoding
```

### 6.2 MCP AgentPass 授权

AgentPass 查询 active SkillSet 时应使用：

```text
engine_type = persisted_engine = claude_code
```

这样可以查到 active SkillSet，并继续做 MCP 授权。

### 6.3 Skill symlink

查询 active skills：

```text
engine_type = persisted_engine = claude_code
```

生成 symlink path：

```text
engine_type = runtime_engine = aicoding
```

示例：

```text
git_path = git://infra/pmo/agent-coding-process-skill
```

生成：

```json
{
  "source": "/home/admin/.aicoding/skills-repo/infra/pmo/agent-coding-process-skill",
  "target": "/home/admin/.claude/skills/agent-coding-process-skill"
}
```

### 6.4 CLI 同步

CLI 关联数据归属：

```text
SkillSet.engine_type = claude_code
```

CLI runtime 同步路径：

```text
runtime_engine = aicoding
```

因此不应该通过把 SkillSet 写成 `aicoding` 来触发 CLI 同步；CLI 是否同步到 `~/.claude/skills/` 应该由 runtime engine 决定。

## 7. 推荐代码结构

### 7.1 engine_resolver.py

建议提供：

```python
resolve_persisted_engine_for_bot(...)
resolve_runtime_engine_for_bot(...)
resolve_engine_context_for_bot(...)
resolve_engine_for_bot(...)  # backward compatible wrapper to runtime resolver
```

### 7.2 SkillSet Router

SkillSet 相关 `_get_path_params()` 不应只返回一个 engine，建议返回：

```text
effective_entity_id
effective_bot_id
persisted_engine
runtime_engine
effective_entity_type
is_desktop
```

其中：

```text
SkillSetService.engine_type = persisted_engine
SkillSetService.runtime_engine_type = runtime_engine
```

### 7.3 SkillSetService

建议明确字段语义：

```python
self.engine_type = persisted_engine
self.runtime_engine_type = runtime_engine_type or self.engine_type
```

DB 查询类方法继续用：

```python
self.engine_type
```

例如：

```text
get_active_skills
get_all_active_skill_sets
get_active_skill_set
add_skill_to_set
add_mcp_to_skill_set
add_cli_to_skill_set
```

runtime/path 类方法用：

```python
self.runtime_engine_type
```

例如：

```text
get_symlink_mappings 的 source/target 生成
CLI runtime sync
local/repo path layout
```

### 7.4 AgentPass 授权查询

不要直接信外部传入的 `active_engine` 作为 SkillSet 查询 engine。

推荐：

```text
通过 bot_id resolve persisted_engine
用 persisted_engine 查询 active SkillSet
```

如果历史调用暂时只能传 engine，则应该保证传入的是：

```text
persisted_engine = bot.active_engine
```

而不是 runtime engine。

## 8. 历史数据兼容

因为已有逻辑可能已经创建了：

```text
Bot.active_engine = claude_code
Bot.template_type != normalCC
SkillSet.engine_type = aicoding
```

需要处理历史数据。

### 推荐：迁移为主，fallback 为辅

迁移条件：

```text
Bot.active_engine = claude_code
Bot.template_type != normalCC
SkillSet.bolt_id = Bot.bot_id
SkillSet.engine_type = aicoding
```

迁移结果：

```text
SkillSet.engine_type = claude_code
```

短期 fallback：

```text
查询 active SkillSet 时：
1. 先查 persisted_engine
2. 查不到时 fallback runtime_engine
3. 命中 fallback 时打 warning log，提示存在历史脏数据
```

fallback 不建议长期保留，避免继续模糊数据归属。

## 9. 防误 clean 保护

建议增加显式 clean 保护：

```text
add Skill / add MCP / add CLI / activate SkillSet 后触发同步时，
如果本次操作成功，但 get_symlink_mappings() 返回空，
不要直接 sync_symlinks([])。
```

可选策略：

```text
1. skip sync，并记录 error log
2. 返回 sync_failed，但不回滚已写 DB
3. 区分 explicit clean 和 empty snapshot
```

原则：

```text
clean 只能由显式清理动作触发，不能由异常空快照隐式触发。
```

## 10. 对相关 PR 的影响

### 10.1 对 732cd33 的影响

commit：

```text
732cd33cc59019d0803814f662ec51e112461082
fix: sync aicoding local skills in prepub builds (#1024)
```

该改动需要保持：

```text
claude_code + non-normalCC => runtime_engine = aicoding
```

本方案不会破坏它，前提是：

```text
build/config/runtime 继续使用 runtime_engine
resolve_engine_for_bot 继续保持 runtime 语义
```

风险：

```text
如果把 resolve_engine_for_bot 全局改成 persisted_engine，
则会破坏该 PR。
```

### 10.2 对 6d7b79b 的影响

commit：

```text
6d7b79b9dcede471d0ff4e24696efbbea9d43799
fix: sync aicoding local skills in prepub builds (#1037)
```

该改动本身已经隐含双 engine 语义：

```text
runtime/build_plan engine = aicoding
source NAS bucket engine  = bot.active_engine = claude_code
```

本方案与它一致：

```text
build provider/build_plan 用 runtime_engine
source_nas_engine_type 用 persisted_engine / bot.active_engine
```

## 11. 为什么 SkillSet 会受你的 PR 影响

你的 PR 没有直接改 SkillSet 代码，但 SkillSet 代码本来就调用了公共 resolver：

```text
skillsets.py::_get_path_params()
→ resolve_engine_for_bot(...)
→ SkillSetService(engine_type=effective_engine)
```

在 PR 前：

```text
resolve_engine_for_bot(bot.active_engine=claude_code) => claude_code
```

在 PR 后：

```text
resolve_engine_for_bot(bot.active_engine=claude_code, template_type!=normalCC) => aicoding
```

所以虽然 PR 没改 SkillSet 文件，但它改变了 SkillSet 依赖的公共函数返回值。SkillSet 链路因此从：

```text
SkillSetService.engine_type = claude_code
```

变成：

```text
SkillSetService.engine_type = aicoding
```

这就是为什么 SkillSet 创建、添加 Skill、添加 MCP、active 查询、CLI 同步都会受到影响。

准确说：

```text
PR 没有引入 SkillSet 的混用设计，
但 PR 改变了公共 resolver 的语义，
暴露了 SkillSet 之前把 runtime engine 当 data engine 使用的隐患。
```

## 12. 推荐落地顺序

### P0：统一 SkillSet 数据归属

所有 SkillSet DB 操作切到：

```text
persisted_engine = bot.active_engine
```

覆盖：

```text
create/update/delete SkillSet
activate/deactivate SkillSet
list/get active SkillSet
add/remove Skill
add/remove MCP
add/remove CLI
AgentPass active SkillSet 查询
```

### P1：runtime 同步显式使用 runtime_engine

覆盖：

```text
Skill symlink mapping
CLI sync
runtime resource sync
source/target path generation
```

### P2：防误 clean

避免异常空 snapshot 触发 clean。

### P3：历史数据迁移

将错误写入 `aicoding` 的 SkillSet 迁回 `claude_code`。

## 13. 最终结论

统一规则：

```text
SkillSet.engine_type 永远表示数据归属，使用 bot.active_engine。
runtime 路径和同步永远使用 runtime_engine。
```

对于 `claude_code + non-normalCC`：

```text
SkillSet.engine_type = claude_code
AgentPass 查 claude_code
Skill/MCP/CLI 关联数据查 claude_code
Skill/CLI runtime 同步走 aicoding
Skill source = /home/admin/.aicoding/skills-repo/...
Skill/CLI target = /home/admin/.claude/skills/...
```

这个方案可以同时解决：

```text
1. Skill 加入 SkillSet 后误 clean
2. MCP AgentPass 授权查不到 active SkillSet
3. SkillSet 改成 claude_code 后 CLI 不同步到 ~/.claude/skills
```

并且不破坏 aicoding prepub build 相关 PR。

## 14. `resolve_engine_for_bot` 当前调用点影响面

当前 `resolve_engine_for_bot` 已经不是单一 build/runtime helper，而是被多个模块复用。拆分 resolver 时不能全局把它改成 persisted 语义，否则会破坏大量 runtime/path/provider 链路。

### 14.1 直接调用点总览

非测试代码里的主要直接调用点：

```text
core/bot_management/services/engine_resolver.py
  resolve_engine_for_bot 定义本身

core/service_bot/services/baas_service.py
  _resolve_sandbox_provider

core/service_bot/services/bot_build_service.py
  _resolve_sandbox_provider fallback

core/services/identity.py
  read_identity_file
  write_identity_file
  get_bot_file
  list_bot_files
  update_bot_file

plugins/local/local_device_lifecycle.py
  _restore_local_symlinks

adapters/http/bot_management/router.py
  get_bot_work_dir
  get_bot_config_dir

adapters/http/resources/file_router.py
  _resolve_params

adapters/http/resources/router.py
  _get_path_params

adapters/http/openapi_v1/resources/router.py
  _file_coords

adapters/http/service_bot/router_build.py
  get_read_only_rules
  get_build_rsync_excludes

adapters/http/service_bot/router_publish.py
  get_publish_engine_config

adapters/http/skill_center/skillsets.py
  _get_path_params

adapters/http/skill_center/skills.py
  _get_path_params
  get_skill_readme 中的 read_engine
```

其中 `skillsets.py::_get_path_params` 和 `skills.py::_get_path_params` 虽然各自只有一个直接调用 resolver 的位置，但它们是公共 helper，会影响大量 Skill Center API。

### 14.2 调用点分类原则

按语义分三类：

```text
A. 应继续使用 runtime_engine 的调用
B. 应改成 persisted_engine 的调用
C. 需要同时使用 persisted_engine + runtime_engine 的混合调用
```

判断标准：

```text
如果用于 provider / build plan / config / runtime file path / workspace path：
  使用 runtime_engine

如果用于 SkillSet DB 数据归属 / active SkillSet 查询 / MCP 授权查询：
  使用 persisted_engine

如果同时涉及 DB 查询和 runtime 同步：
  同时传 persisted_engine + runtime_engine
```

### 14.3 A 类：应继续使用 runtime_engine

这些调用点本质是选择 provider、读取运行时配置、读写 runtime 文件或计算 runtime path。对于：

```text
bot.active_engine = claude_code
template_type != normalCC
```

它们应该继续得到：

```text
runtime_engine = aicoding
```

不能改成 `claude_code`。

#### 14.3.1 `baas_service.py::_resolve_sandbox_provider`

位置：

```text
src/backend/src/agentclaw/community/core/service_bot/services/baas_service.py
```

用途：

```text
解析 sandbox provider
```

当前形态：

```python
engine_type = engine or resolve_engine_for_bot(bot_id, owner_id, bot_repo=self._bot_repo)
provider = self._sandbox_registry.resolve(engine_type)
```

建议：

```text
继续使用 runtime_engine
```

原因：

```text
claude_code + non-normalCC 应该选择 aicoding provider。
```

#### 14.3.2 `bot_build_service.py::_resolve_sandbox_provider fallback`

位置：

```text
src/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py
```

用途：

```text
build provider fallback
```

建议：

```text
继续使用 runtime_engine
```

原因：

```text
build provider/build_plan 应走 aicoding。
```

这与 6d7b79b 的双 engine 语义一致：

```text
runtime/build_plan engine = aicoding
source NAS bucket engine  = bot.active_engine = claude_code
```

#### 14.3.3 `service_bot/router_build.py`

位置：

```text
src/backend/src/agentclaw/community/adapters/http/service_bot/router_build.py
```

调用点：

```text
get_read_only_rules
get_build_rsync_excludes
```

用途：

```text
查询 engine provider 的默认只读规则、build rsync excludes
```

建议：

```text
继续使用 runtime_engine
```

原因：

```text
这些配置来自实际 runtime/build provider。
claude_code + non-normalCC 应取 aicoding provider 的 build plan。
```

#### 14.3.4 `service_bot/router_publish.py`

位置：

```text
src/backend/src/agentclaw/community/adapters/http/service_bot/router_publish.py
```

调用点：

```text
get_publish_engine_config
```

用途：

```text
读取发布阶段 engine config
```

建议：

```text
倾向继续使用 runtime_engine
```

原因：

```text
读取的是发布 runtime config，不是 SkillSet DB 数据归属。
```

补充风险：

```text
如果 publish record 自身已经固化了 engine/runtime 信息，长期更稳的是优先使用 publish record 上的 engine，避免 Bot 当前状态变化影响历史发布记录读取。
```

#### 14.3.5 `bot_management/router.py`

位置：

```text
src/backend/src/agentclaw/community/adapters/http/bot_management/router.py
```

调用点：

```text
get_bot_work_dir
get_bot_config_dir
```

用途：

```text
查询 Bot work-dir / config-dir runtime 路径
```

建议：

```text
继续使用 runtime_engine
```

原因：

```text
work-dir/config-dir 是运行时路径。
claude_code + non-normalCC 应返回 aicoding runtime 视角目录。
```

#### 14.3.6 Resource 文件类接口

位置：

```text
src/backend/src/agentclaw/community/adapters/http/resources/file_router.py
src/backend/src/agentclaw/community/adapters/http/resources/router.py
src/backend/src/agentclaw/community/adapters/http/openapi_v1/resources/router.py
src/backend/src/agentclaw/community/core/resources/dependencies/resource.py
```

用途：

```text
文件下载、预览、资源目录列表、ResourceFileService workspace coords
```

建议：

```text
倾向继续使用 runtime_engine
```

原因：

```text
这些是 workspace/file path 访问，不是 SkillSet DB 数据归属。
对于 aicoding runtime，应访问 aicoding runtime workspace。
```

边界风险：

```text
如果某些资源记录本身是 DB tenant 数据，并且 DB 上按 engine_type 隔离，
则查询 DB 时应使用 persisted_engine，读写文件路径时使用 runtime_engine。
```

#### 14.3.7 `identity.py`

位置：

```text
src/backend/src/agentclaw/community/core/services/identity.py
```

调用点：

```text
read_identity_file
write_identity_file
get_bot_file
list_bot_files
update_bot_file
```

用途：

```text
读写 identity 文件，通过 engine_type 定位 device/local/runtime path
```

建议：

```text
倾向继续使用 runtime_engine
```

原因：

```text
这是运行时文件 I/O，不是 SkillSet 数据归属。
```

边界风险：

```text
如果 identity 文件被定义为 Bot 持久数据，而不是 runtime 文件，
也应进一步拆成 persisted coordinate + runtime file layout。
当前代码注释更偏 provider-blind device/local file I/O，因此继续 runtime 更合理。
```

### 14.4 B 类：应改成 persisted_engine

这类是本次问题的核心。凡是用于：

```text
SkillSet 创建
SkillSet 查询
SkillSet active 查询
SkillSet membership
MCP/CLI 关联数据
AgentPass 授权查询 active SkillSet
```

都应该用：

```text
persisted_engine = bot.active_engine
```

不能用 runtime engine。

#### 14.4.1 `skill_center/skillsets.py::_get_path_params`

位置：

```text
src/backend/src/agentclaw/community/adapters/http/skill_center/skillsets.py
```

当前：

```python
effective_engine = resolve_engine_for_bot(...)
```

影响的 API：

```text
GET    /api/skillsets
POST   /api/skillsets
GET    /api/skillsets/with-mcps
GET    /api/skillsets/resources
GET    /api/skillsets/{skill_set_id}
PUT    /api/skillsets/{skill_set_id}
DELETE /api/skillsets/{skill_set_id}

GET    /api/skillsets/{skill_set_id}/skills
POST   /api/skillsets/{skill_set_id}/skills
DELETE /api/skillsets/{skill_set_id}/skills/{skill_id}

GET    /api/skillsets/{skill_set_id}/mcps
POST   /api/skillsets/{skill_set_id}/mcps
DELETE /api/skillsets/{skill_set_id}/mcps/{server_code}

DELETE /api/skillsets/{skill_set_id}/clis/{resource_code}

default skill set 相关 admin/ensure/current/sync 接口
```

建议：

```text
这里的 SkillSetService.engine_type 应使用 persisted_engine。
```

原因：

```text
这些 API 大部分都是 SkillSet DB 数据归属操作。
```

当前问题链路：

```text
bot.active_engine = claude_code
template_type != normalCC
resolve_engine_for_bot => aicoding
SkillSetService.engine_type = aicoding

创建 SkillSet 写成 aicoding
或操作 claude_code SkillSet 时 active 查询查不到
MCP AgentPass 授权查询 mismatch
```

### 14.5 C 类：需要同时使用 persisted_engine + runtime_engine

这些调用既涉及 DB 数据，又涉及运行时路径/同步。不能简单改成 persisted，也不能继续纯 runtime。

#### 14.5.1 `skill_center/skillsets.py` 的 Skill / MCP / CLI 操作

典型接口：

```text
POST /api/skillsets/{skill_set_id}/skills
POST /api/skillsets/{skill_set_id}/mcps
DELETE /api/skillsets/{skill_set_id}/clis/{resource_code}
activate/deactivate SkillSet
```

建议：

```text
SkillSetService.engine_type = persisted_engine
SkillSetService.runtime_engine_type = runtime_engine
```

具体用法：

```text
DB 查询 / DB 写入 / active SkillSet 查询:
  persisted_engine

symlink mapping / CLI runtime sync / source path / target path:
  runtime_engine
```

否则会出现两种反向问题：

```text
如果纯 runtime：
  SkillSetService.engine_type = aicoding
  查不到 claude_code active SkillSet
  symlink empty -> clean
  MCP AgentPass 查不到 active SkillSet

如果纯 persisted：
  SkillSetService.engine_type = claude_code
  DB 归属对了
  但 runtime sync/CLI path 也按 claude_code 走
  CLI 可能不再同步到 ~/.claude/skills/
  Skill source path 可能不再使用 /home/admin/.aicoding/skills-repo
```

#### 14.5.2 `skill_center/skills.py::_get_path_params`

位置：

```text
src/backend/src/agentclaw/community/adapters/http/skill_center/skills.py
```

影响的 API：

```text
upload_skill
GET  /api/skills/active/list
GET  /api/skills/skillset/current
POST /api/skills/skillset/switch
POST /api/skills/skillset/sync
GET  /api/skills/skillset/active

POST /api/skills/{skill_id}/activate
POST /api/skills/{skill_id}/deactivate
POST /api/skills/deactivate-all

GET  /api/skills/market/local
GET  /api/skills/market/tree
GET  /api/skills/market/list
POST /api/skills/market/activate-batch
POST /api/skills/market/search
POST /api/skills/market/sync
GET  /api/skills/market/sync-status
```

建议：

```text
不能一刀切，需要按接口语义拆。
```

应使用 persisted_engine 的部分：

```text
GET  /api/skills/active/list
GET  /api/skills/skillset/current
POST /api/skills/skillset/switch
POST /api/skills/skillset/sync
GET  /api/skills/skillset/active
```

原因：

```text
这些和 SkillSet active / DB 查询强相关。
```

应使用 runtime_engine 的部分：

```text
upload_skill 的文件落盘路径
direct activate/deactivate skill 的 runtime symlink
market sync 到本地 repo/local path
market local/tree/list 如果读的是 runtime workspace
```

需要混合处理的部分：

```text
market activate-batch
direct activate skill
```

如果它们同时写 DB 状态和同步 runtime symlink，则应拆成：

```text
DB state/membership/query:
  persisted_engine

runtime mapping/path:
  runtime_engine
```

#### 14.5.3 `skills.py` 的 `get_skill_readme`

位置：

```text
src/backend/src/agentclaw/community/adapters/http/skill_center/skills.py
```

当前用途：

```text
根据 Skill 所属 Bot 读取本地 Skill README 文件路径
```

建议：

```text
读取文件路径使用 runtime_engine
```

原因：

```text
这里是在读 device/runtime 文件路径，不是在查 SkillSet 数据归属。
```

边界：

```text
如果 README 对象来自 Skill DB 记录，查询 Skill 记录本身仍应按数据归属/tenant 规则走；读取文件路径用 runtime_engine。
```

#### 14.5.4 `plugins/local/local_device_lifecycle.py::_restore_local_symlinks`

位置：

```text
src/backend/src/agentclaw/community/plugins/local/local_device_lifecycle.py
```

当前逻辑：

```python
active_sets = self._skill_set_repo.get_all_active_skill_sets()
for skill_set in active_sets:
    engine_type = resolve_engine_for_bot(...)
    skills_dir, repo_dir, local_dir = _get_bot_paths(..., engine_type=engine_type)
    set_service = skill_set_factory.create(...)
    mappings = set_service.get_symlink_mappings(...)
```

用途：

```text
本地启动时恢复 active SkillSet 的 symlinks
```

建议：

```text
使用双 engine。
```

推荐逻辑：

```text
遍历 active_sets:
  data_engine = skill_set["engine_type"] 或 resolve_persisted_engine_for_bot(...)
  runtime_engine = resolve_runtime_engine_for_bot(...)

创建 SkillSetService:
  engine_type = data_engine
  runtime_engine_type = runtime_engine
```

原因：

```text
active_sets 已经从 DB 查出来了；
后续 mapping/path 应使用 runtime_engine；
但 get_symlink_mappings 内部如果重新查询 active skills，必须仍使用 data_engine，避免查不到当前 active SkillSet。
```

### 14.6 总体影响矩阵

| 模块 | 调用点 | 当前用途 | 建议 |
|---|---|---|---|
| `baas_service.py` | `_resolve_sandbox_provider` | sandbox provider | 保持 runtime |
| `bot_build_service.py` | `_resolve_sandbox_provider fallback` | build provider | 保持 runtime |
| `router_build.py` | read-only rules / rsync excludes | build/provider config | 保持 runtime |
| `router_publish.py` | publish engine config | runtime config | 保持 runtime，长期可用 publish 固化 engine |
| `bot_management/router.py` | work-dir/config-dir | runtime path | 保持 runtime |
| `resources/*` | file/resource workspace | runtime file path | 保持 runtime |
| `openapi_v1/resources` | file coords | runtime file path | 保持 runtime |
| `identity.py` | identity file I/O | runtime file path | 保持 runtime |
| `skillsets.py::_get_path_params` | SkillSet CRUD/MCP/CLI | SkillSet DB 归属 | 改 persisted + 同时传 runtime |
| `skills.py::_get_path_params` | Skill/SkillSet/market 混合 | 混合 | 拆 context，按接口分别用 |
| `skills.py get_skill_readme` | 读 runtime 文件 | runtime path | 保持 runtime |
| `local_device_lifecycle.py` | restore symlinks | DB active + runtime mapping | 双 engine |

### 14.7 修复策略

不要全局改 `resolve_engine_for_bot`：

```text
不要把它直接改回 bot.active_engine。
```

原因：

```text
大量 provider/path/build/resource/identity 调用合理依赖 runtime 语义。
```

推荐：

```python
resolve_persisted_engine_for_bot()
# bot.active_engine

resolve_runtime_engine_for_bot()
# resolve_bot_engine(bot) or bot.active_engine

resolve_engine_context_for_bot()
# 返回 persisted_engine + runtime_engine

resolve_engine_for_bot()
# 继续作为 runtime resolver 兼容入口
```

优先修复：

```text
1. skill_center/skillsets.py::_get_path_params
2. skill_center/skills.py::_get_path_params 中 SkillSet active/DB 查询类接口
3. AgentPass active SkillSet 授权查询
4. SkillSetService 增加 runtime_engine_type，runtime path/sync 改用 runtime_engine
```

## 15. 关于“全部使用 aicoding”的兼容性分析

### 15.1 结论

`claude_code + non-normalCC` 场景下，“全部使用 `aicoding`”不是完全不能做，但它不是一个局部修复，而是一次完整的引擎身份迁移。

如果只是把 SkillSet 创建、查询或部分同步链路改成 `aicoding`，而 Bot 元数据仍然保持：

```text
bot.active_engine = claude_code
```

则会继续产生 engine 不一致问题，尤其会影响：

```text
1. active SkillSet 查询
2. MCP 授权范围收集
3. AgentPass / passport resource scope 更新
4. mcporter.json 的 MCP 内容生成
5. 默认 MCP / 默认 CLI 合并
6. NAS / build source 归属
7. 历史 SkillSet 数据兼容
```

因此推荐结论是：

```text
不要做“局部全 aicoding”。
如果要全 aicoding，必须把 Bot persisted identity 也迁成 aicoding，并配套迁移历史数据和所有查询/授权/build 链路。
当前更稳妥的方案仍然是 persisted_engine 和 runtime_engine 拆分。
```

### 15.2 “全 aicoding”有两种含义

#### 15.2.1 局部全 aicoding

即：

```text
bot.active_engine 仍然是 claude_code
SkillSet.engine_type 改成 aicoding
runtime/build/CLI/symlink 也用 aicoding
```

这种方案不推荐。

原因是系统里仍然存在很多地方会从 Bot 读取：

```text
bot.active_engine = claude_code
```

然后拿这个值作为 SkillSet 查询、MCP scope 刷新或 passport 更新的 engine。

这会形成：

```text
写入：SkillSet.engine_type = aicoding
查询：engine_type = claude_code
结果：查不到 active SkillSet
```

#### 15.2.2 完整全 aicoding

即产品语义上重新定义：

```text
claude_code + non-normalCC Bot 本质上就是 aicoding Bot
```

则需要把 Bot 自身也迁移为：

```text
bot.active_engine = aicoding
```

并同步迁移：

```text
SkillSet.engine_type
active SkillSet 查询条件
MCP scope 查询条件
AgentPass / passport engine_type
默认 MCP / 默认 CLI 选择
NAS bucket / build source 归属
历史数据
```

这种方案可以成立，但成本和影响面明显大于 resolver 拆分方案。

### 15.3 对 MCP / mcporter.json 的具体影响

`mcporter.json` 文件本身对 `claude_code` 和 `aicoding` 没有明显格式差异。

当前 `claude_code` 和 `aicoding` 的 build plan 都使用：

```text
workspace/config/mcporter.json
```

容器内也都会关注：

```text
/home/admin/.mcporter/mcporter.json
```

所以问题不在于：

```text
aicoding 是否能写 mcporter.json
```

而在于：

```text
mcporter.json 的 MCP 输入数据能不能被正确收集到
```

MCP 数据收集链路大致是：

```text
mcporter.json / MCP sync payload
  <- collect_bot_active_mcps(engine_type=...)
  <- get_all_active_skill_sets(engine_type=...)
  <- SkillSet.engine_type
```

如果出现：

```text
SkillSet.engine_type = aicoding
collect_bot_active_mcps(engine_type=claude_code)
```

则 active SkillSet 查不到，最终表现为：

```text
active_mcps = []
mcporter.json 缺少用户添加的 MCP
filter-servers 白名单为空或不完整
passport resource_scope 中 mcp_codes 为空或不完整
```

也就是说：

```text
mcporter.json 不是格式不兼容，而是内容来源因为 engine mismatch 被过滤掉了。
```

### 15.4 MCP scope 刷新的不兼容点

当前 `SkillSetService.refresh_mcp_scope()` 在未显式传入 engine_type 时，会 fallback 到 Bot 的 active engine：

```text
effective_engine = bot.active_engine or openclaw
```

对于目标场景：

```text
bot.active_engine = claude_code
SkillSet.engine_type = aicoding
```

则刷新 MCP scope 时会变成：

```text
refresh_mcp_scope(engine_type=claude_code)
  -> collect_bot_active_mcps(engine_type=claude_code)
  -> get_all_active_skill_sets(engine_type=claude_code)
  -> 查不到 aicoding SkillSet
```

这就是“SkillSet 创建为 aicoding，但 AgentPass 授权查询 active skill set 时传入 claude_code，导致不匹配查不到”的根因。

如果坚持全 aicoding，则这里必须同步改为：

```text
refresh_mcp_scope(engine_type=aicoding)
```

并且不能在后续链路里再被 `bot.active_engine=claude_code` 覆盖回去。

### 15.5 passport / resourceManifest 的不兼容点

`MCPSyncService._update_passport()` 会更新 passport 的 resource scope：

```text
resource_scope = {
  "mcp_codes": synced_server_codes,
  "cli_items": cli_items,
}
```

其中 CLI 会合并默认 CLI：

```text
default_cli_items = get_default_cli_items(engine_type, template_type)
```

当前代码里 `_update_passport()` 会重新读取 Bot，并可能把传入的 engine 覆盖为：

```text
engine_type = bot.active_engine or bot.engine_type or engine_type
```

因此在局部全 aicoding 方案下，即使上游传了 `aicoding`，这里也可能因为：

```text
bot.active_engine = claude_code
```

重新变成 `claude_code`。

带来的风险：

```text
1. passport 记录的 engine_type 与 SkillSet.engine_type 不一致
2. 默认 CLI 按 claude_code 合并，而不是 aicoding
3. resourceManifest 中 CLI/MCP 授权快照与 runtime 预期不一致
```

如果完整全 aicoding，则必须保证 passport 更新链路也统一使用 `aicoding`，不能再回退到 `claude_code`。

### 15.6 默认 MCP / 默认 CLI 的行为变化

`engine_type` 还会影响默认能力选择：

```text
get_default_mcp_servers(engine_type, ...)
get_default_cli_items(engine_type, template_type)
```

如果全部改成 `aicoding`，则默认 MCP、默认 CLI 都会按 `aicoding` 取。

这可能是符合 aicoding runtime 预期的，但需要单独验收：

```text
1. aicoding 默认 MCP 是否覆盖 claude_code non-normalCC 原本需要的默认 MCP
2. aicoding 默认 CLI 是否覆盖当前 claude_code Bot 需要的默认 CLI
3. passport resourceManifest 是否会因为默认 CLI 差异导致权限变化
4. mcporter.json 中默认 headers / endpoint_env / transportProtocol 是否符合预期
```

因此，“全 aicoding”不是只改一个查询条件，还会改变默认能力集合。

### 15.7 Skill / CLI 软链层面的收益与边界

从 Skill / CLI 软链角度看，`aicoding` runtime 是正确方向。

对于 aicoding，当前 source/target 语义是：

```text
source: /home/admin/.aicoding/skills-repo/...
target: /home/admin/.claude/skills/...
```

例如：

```text
git://infra/pmo/agent-coding-process-skill
```

应生成：

```json
{
  "source": "/home/admin/.aicoding/skills-repo/infra/pmo/agent-coding-process-skill",
  "target": "/home/admin/.claude/skills/agent-coding-process-skill"
}
```

所以 runtime 层统一用 `aicoding` 可以解决：

```text
CLI / Skill 同步到 ~/.claude/skills/
```

但这个收益不要求 SkillSet 数据归属也写成 `aicoding`。

更合理的是：

```text
SkillSet.engine_type = persisted_engine = claude_code
symlink/runtime engine = runtime_engine = aicoding
```

### 15.8 与 6d7b79b 的潜在冲突

commit：

```text
6d7b79b9dcede471d0ff4e24696efbbea9d43799
fix: sync aicoding local skills in prepub builds (#1037)
```

该改动隐含的语义是：

```text
runtime/build_plan engine = aicoding
source NAS bucket engine  = bot.active_engine = claude_code
```

也就是：

```text
build/runtime 布局走 aicoding
Bot 数据/NAS 归属仍然走 claude_code
```

这与双 resolver 方案一致。

如果改成完整全 aicoding，则需要重新确认：

```text
1. NAS bucket 是否也迁成 aicoding
2. 旧 claude_code NAS 数据是否需要迁移
3. build source_dir 是否会从错误目录读取
4. 历史 prepub build 产物是否仍可读取
```

如果只做局部全 aicoding，则更危险：

```text
SkillSet/runtime 认为自己是 aicoding
build/NAS 仍可能按 claude_code 找源数据
```

会导致数据归属和 runtime 归属继续分裂，但分裂点不可控。

### 15.9 历史数据风险

当前线上或测试环境可能已经存在两类数据：

```text
SkillSet.engine_type = claude_code
SkillSet.engine_type = aicoding
```

如果全 aicoding，需要处理历史数据迁移：

```text
claude_code -> aicoding
```

同时要避免同一个 Bot 下同时存在两套 active SkillSet：

```text
Bot A:
  active SkillSet 1: engine_type = claude_code
  active SkillSet 2: engine_type = aicoding
```

否则可能带来：

```text
1. active SkillSet 冲突
2. MCP 重复或遗漏
3. Skill/CLI 重复同步
4. 用户页面看到的数据和 AgentPass 授权数据不一致
```

完整全 aicoding 至少需要：

```text
1. 数据迁移脚本
2. 迁移前冲突检测
3. 双读 fallback 或灰度期兼容
4. 迁移后禁止再写 claude_code SkillSet
```

### 15.10 如果坚持全 aicoding，推荐迁移条件

只有在产品和架构明确接受以下定义时，才建议全 aicoding：

```text
对于 claude_code + non-normalCC：
  Bot persisted identity = aicoding
  Bot runtime identity   = aicoding
```

对应改造清单：

```text
1. 将 bot.active_engine 迁为 aicoding，或引入明确的 persisted_engine_override
2. 将历史 SkillSet.engine_type 从 claude_code 迁为 aicoding
3. SkillSet 创建/查询/激活/删除全部使用 aicoding
4. collect_bot_active_mcps 全部使用 aicoding
5. refresh_mcp_scope 全部使用 aicoding
6. _update_passport 不再覆盖回 claude_code
7. AgentPass active SkillSet 查询统一使用 aicoding
8. get_default_mcp_servers / get_default_cli_items 按 aicoding 验收
9. NAS / build source 归属按 aicoding 重新确认或迁移
10. 清理或兼容旧 claude_code SkillSet 数据
```

如果做不到以上闭环，则不建议全 aicoding。

### 15.11 推荐方案对比

| 方案 | 优点 | 风险 | 结论 |
|---|---|---|---|
| 局部全 aicoding | CLI/symlink runtime 容易走通 | MCP/AgentPass/active SkillSet/NAS 继续 mismatch | 不推荐 |
| 完整全 aicoding | 语义单一，长期简单 | 需要迁 Bot、SkillSet、NAS、passport、历史数据，成本高 | 只有产品确认迁移时可选 |
| persisted/runtime 拆分 | 保留历史数据归属，runtime 走 aicoding，兼容前面 PR | 代码里要显式区分两个 engine | 推荐 |

### 15.12 最终建议

当前问题的最小正确修复不是“全部用 aicoding”，而是：

```text
SkillSet / MCP / AgentPass / active 查询：使用 persisted_engine = bot.active_engine = claude_code
Skill / CLI / build / runtime path：使用 runtime_engine = aicoding
```

这样可以同时保证：

```text
1. SkillSet.engine_type 与 AgentPass 查询 engine 一致
2. collect_bot_active_mcps 能查到用户激活的 MCP
3. mcporter.json 能拿到完整 MCP 内容
4. passport resource_scope 能正确写入 mcp_codes / cli_items
5. CLI / Skill 软链仍然同步到 ~/.claude/skills/
6. 不破坏 6d7b79b 中 source NAS bucket 使用 bot.active_engine 的语义
```

一句话总结：

```text
mcporter.json 不排斥 aicoding；排斥的是 SkillSet 写 aicoding、MCP/AgentPass 却按 claude_code 查这种 engine mismatch。
```
