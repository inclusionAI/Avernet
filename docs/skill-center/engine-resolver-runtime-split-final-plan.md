# Engine Resolver 最终方案：默认 data engine，显式 runtime engine

## 1. 背景

当前 `claude_code + non-normalCC` Bot 场景中存在两类 engine 语义：

```text
bot.active_engine = claude_code
runtime/layout engine = aicoding
```

之前的改动让公共 resolver 在该场景下返回 `aicoding`：

```text
resolve_engine_for_bot() => aicoding
```

这解决了 aicoding runtime/build/local skill sync 的问题，但也导致 SkillSet / MCP / AgentPass 等数据归属链路被误导为 `aicoding`。

典型问题：

```text
SkillSet.engine_type 被写成 aicoding
AgentPass / MCP scope 按 bot.active_engine = claude_code 查询 active SkillSet
结果查不到，导致 MCP 未授权、mcporter.json 缺 MCP
```

以及：

```text
SkillSet.engine_type = claude_code
但 add skill 后 symlink 同步如果继续用 claude_code runtime path，CLI/Skill 又无法同步到 ~/.claude/skills/
```

根因是：

```text
resolve_engine_for_bot 这个名字被当作默认 engine 使用，
但实际返回的是 runtime engine。
```

这导致调用方无法区分：

```text
数据归属 engine
运行时布局 engine
```

## 2. 最终结论

不新增、不暴露 `resolve_persisted_engine_for_bot` 这个名字。

最终对外语义统一为：

```python
resolve_engine_for_bot()
# 默认 engine resolver
# 返回 bot.active_engine
# 用于 data / persisted / SkillSet / MCP / AgentPass 查询

resolve_runtime_engine_for_bot()
# runtime engine resolver
# 处理 claude_code + non-normalCC => aicoding
# 用于 build / provider / runtime path / workspace config / symlink path
```

也就是说：

```text
resolve_engine_for_bot = 默认数据归属 engine
resolve_runtime_engine_for_bot = 显式 runtime engine
```

对于：

```text
bot.active_engine = claude_code
template_type != normalCC
```

期望结果：

```text
resolve_engine_for_bot()         => claude_code
resolve_runtime_engine_for_bot() => aicoding
```

## 3. 为什么不暴露 resolve_persisted_engine_for_bot

虽然内部语义上存在 persisted/data engine，但不建议对外暴露：

```python
resolve_persisted_engine_for_bot()
```

原因：

```text
1. persisted 这个词对大部分调用方偏实现细节
2. 系统默认 engine 本来就应该是 bot.active_engine
3. SkillSet.engine_type、MCP 查询 engine、AgentPass engine 都是默认数据归属语义
4. 只有 runtime 是特殊分支，应该显式命名
```

因此命名上更自然的是：

```text
默认：resolve_engine_for_bot
特殊：resolve_runtime_engine_for_bot
```

## 4. Resolver 语义设计

### 4.1 `resolve_engine_for_bot`

语义：

```text
返回 Bot 的 active_engine。
```

用途：

```text
1. SkillSet.engine_type
2. SkillSet 创建/查询/更新/删除
3. active SkillSet 查询
4. SkillSet-Skill 关联
5. SkillSet-MCP 关联
6. SkillSet-CLI 关联
7. collect_bot_active_mcps 查询 active SkillSet
8. AgentPass / passport 查询数据归属
9. source NAS bucket 归属
```

行为：

```text
如果传入 override，则尊重 override；
否则查 bot.active_engine；
查不到再 fallback DEFAULT_ENGINE_TYPE。
```

对于目标场景：

```text
bot.active_engine = claude_code
template_type != normalCC
```

返回：

```text
claude_code
```

### 4.2 `resolve_runtime_engine_for_bot`

语义：

```text
返回实际运行时 engine。
```

用途：

```text
1. sandbox provider 选择
2. build provider / build plan
3. work dir / config dir
4. workspace / resource file path
5. identity file I/O runtime path
6. skill readme/runtime skill 文件读取
7. skill symlink source/target path
8. CLI runtime sync path
9. local device restore symlink path
```

行为：

```text
如果 bot.active_engine = claude_code
且 template_type 非空且不是 normalCC
则返回 aicoding；
否则返回 resolve_engine_for_bot() 的结果。
```

对于目标场景：

```text
bot.active_engine = claude_code
template_type != normalCC
```

返回：

```text
aicoding
```

## 5. 核心使用原则

### 5.1 默认使用 `resolve_engine_for_bot`

凡是不能明确判断为 runtime/path/provider 的地方，都应该默认使用：

```python
resolve_engine_for_bot()
```

也就是：

```text
bot.active_engine
```

原则：

```text
数据归属不能被 runtime 特殊逻辑影响。
```

### 5.2 只有 runtime 场景使用 `resolve_runtime_engine_for_bot`

只有当代码明确处理以下内容时，才使用 runtime resolver：

```text
provider
build plan
workspace path
config path
runtime file path
skill source/target path
CLI/Skill 软链路径
local device restore path
```

原则：

```text
runtime 特殊逻辑必须显式可见。
```

## 6. SkillSetService 字段语义

`SkillSetService` 保留原有字段：

```python
self.engine_type
```

但语义固定为：

```text
data engine / SkillSet.engine_type / bot.active_engine
```

新增字段：

```python
self.runtime_engine_type
```

语义：

```text
runtime/layout engine
```

初始化建议：

```python
self.engine_type = engine_type
self.runtime_engine_type = runtime_engine_type or engine_type
```

对于目标场景：

```text
self.engine_type = claude_code
self.runtime_engine_type = aicoding
```

## 7. SkillSet 链路调整

### 7.1 Router 层

文件：

```text
src/backend/src/agentclaw/community/adapters/http/skill_center/skillsets.py
```

`_get_path_params()` 中同时解析两个 engine：

```python
effective_engine = resolve_engine_for_bot(...)
runtime_engine = resolve_runtime_engine_for_bot(...)
```

返回值建议包含：

```text
effective_entity_id
effective_bot_id
effective_engine        # data engine，通常是 bot.active_engine
runtime_engine          # runtime/layout engine
effective_entity_type
is_desktop
```

创建 `SkillSetService` 时：

```python
SkillSetService(
    engine_type=effective_engine,
    runtime_engine_type=runtime_engine,
)
```

其中：

```text
engine_type 用于 DB 查询/写入
runtime_engine_type 用于路径/软链/CLI runtime 同步
```

### 7.2 SkillSet DB 操作

以下操作继续使用：

```text
self.engine_type
```

也就是：

```text
claude_code
```

覆盖：

```text
create_skill_set
list_skill_sets
get_skill_set
update_skill_set
delete_skill_set
activate/deactivate skill set
add/remove skill
add/remove mcp
add/remove cli
get_active_skills
get_active_skill_set
get_all_active_skill_sets
collect_bot_active_mcps 查询 active SkillSet
```

### 7.3 Skill symlink 生成

查询阶段：

```text
使用 self.engine_type = claude_code
```

路径生成阶段：

```text
使用 self.runtime_engine_type = aicoding
```

示例：

```text
git_path = git://infra/pmo/agent-coding-process-skill
```

生成 mapping：

```json
{
  "source": "/home/admin/.aicoding/skills-repo/infra/pmo/agent-coding-process-skill",
  "target": "/home/admin/.claude/skills/agent-coding-process-skill"
}
```

## 8. MCP / AgentPass / mcporter 链路调整

### 8.1 MCP active 数据收集

`collect_bot_active_mcps()` 查询 active SkillSet 时必须使用：

```text
resolve_engine_for_bot() / self.engine_type
```

也就是：

```text
claude_code
```

不能使用：

```text
runtime_engine_type = aicoding
```

否则会出现：

```text
SkillSet.engine_type = claude_code
collect_bot_active_mcps(engine_type=aicoding)
查不到 active SkillSet
```

### 8.2 AgentPass / passport 授权查询

AgentPass 查询 active SkillSet 时也应使用：

```text
bot.active_engine / resolve_engine_for_bot()
```

目标：

```text
SkillSet.engine_type = claude_code
AgentPass 查询 engine = claude_code
```

避免：

```text
SkillSet 写 claude_code
AgentPass 查 aicoding
```

或：

```text
SkillSet 写 aicoding
AgentPass 查 claude_code
```

### 8.3 mcporter.json

`mcporter.json` 本身路径/格式不是主要风险。

当前 `claude_code` 和 `aicoding` 都使用：

```text
workspace/config/mcporter.json
/home/admin/.mcporter/mcporter.json
```

真正风险是输入 MCP 数据是否能被正确收集。

链路：

```text
mcporter.json
  <- collect_bot_active_mcps(engine_type=...)
  <- active SkillSet 查询
  <- SkillSet.engine_type
```

因此：

```text
收集用户配置的 MCP：使用 data engine = claude_code
生成 runtime 配置/默认 headers 时：可按需要使用 runtime engine = aicoding
```

第一阶段最小修复建议：

```text
MCP active 查询全部使用 data engine
默认 MCP/CLI 是否拆 runtime_engine 后续单独确认
```

## 9. CLI / Skill 软链链路

CLI / Skill 的数据归属仍然是：

```text
SkillSet.engine_type = claude_code
```

但 runtime 同步路径使用：

```text
runtime_engine_type = aicoding
```

目标：

```text
用户在 claude_code 归属的 SkillSet 上添加 Skill/CLI
设备 runtime 按 aicoding 布局同步到 ~/.claude/skills/
```

不要通过把 SkillSet 写成 `aicoding` 来触发 runtime 同步。

## 10. 需要替换成 resolve_runtime_engine_for_bot 的调用点

因为 `resolve_engine_for_bot()` 要恢复为 `active_engine` 语义，所以所有原本依赖 runtime 语义的调用点必须显式替换为：

```python
resolve_runtime_engine_for_bot(...)
```

### 10.1 build / provider

```text
src/backend/src/agentclaw/community/core/service_bot/services/baas_service.py
  _resolve_sandbox_provider

src/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py
  _resolve_sandbox_provider fallback

src/backend/src/agentclaw/community/adapters/http/service_bot/router_build.py
  get_read_only_rules
  get_build_rsync_excludes

src/backend/src/agentclaw/community/adapters/http/service_bot/router_publish.py
  get_publish_engine_config
```

原因：

```text
这些场景需要选择 runtime provider/build plan。
claude_code + non-normalCC 必须得到 aicoding。
```

### 10.2 work/config dir

```text
src/backend/src/agentclaw/community/adapters/http/bot_management/router.py
  get_bot_work_dir
  get_bot_config_dir
```

原因：

```text
work dir / config dir 是 runtime 路径。
```

### 10.3 resources / file path

```text
src/backend/src/agentclaw/community/adapters/http/resources/file_router.py
src/backend/src/agentclaw/community/adapters/http/resources/router.py
src/backend/src/agentclaw/community/adapters/http/openapi_v1/resources/router.py
src/backend/src/agentclaw/community/core/resources/dependencies/resource.py
```

原因：

```text
这些接口处理 workspace/resource/file path，应按 runtime workspace 解析。
```

### 10.4 identity 文件 I/O

```text
src/backend/src/agentclaw/community/core/services/identity.py
  read_identity_file
  write_identity_file
  get_bot_file
  list_bot_files
  update_bot_file
```

原因：

```text
这些是 runtime/device/workspace 文件 I/O。
```

### 10.5 skill readme / runtime skill 文件

```text
src/backend/src/agentclaw/community/adapters/http/skill_center/skills.py
  get_skill_readme
```

原因：

```text
读取 skill 文件内容应使用 runtime/repo path。
```

### 10.6 local device lifecycle

```text
src/backend/src/agentclaw/community/plugins/local/local_device_lifecycle.py
  _restore_local_symlinks
```

原则：

```text
查询 active SkillSet：使用 resolve_engine_for_bot()
生成 symlink path：使用 resolve_runtime_engine_for_bot()
```

如果该链路复用 `SkillSetService.get_symlink_mappings()`，则由 service 内部负责双 engine。

## 11. 继续使用 resolve_engine_for_bot 的调用点

以下场景继续使用还原后的：

```python
resolve_engine_for_bot()
```

也就是：

```text
bot.active_engine
```

### 11.1 SkillSet CRUD / membership

```text
src/backend/src/agentclaw/community/adapters/http/skill_center/skillsets.py
  list/create/get/update/delete skill set
  add/remove skill
  add/remove mcp
  add/remove cli
  activate/deactivate
```

### 11.2 SkillSet active 相关

```text
src/backend/src/agentclaw/community/adapters/http/skill_center/skills.py
  get_active_skills
  get_current_skill_set
  switch_skill_set
  sync_skill_set
  activate_skill_set
  deactivate_skill_set
  get_active_skill_sets
  activate_skill
  deactivate_skill
  deactivate_all_skills
  activate_skills_batch
```

### 11.3 MCP / AgentPass 查询

```text
core/skill_center/services/skill_set_service.py
  collect_bot_active_mcps
  refresh_mcp_scope 中 active SkillSet 查询

core/mcp/services/sync_service.py
  refresh_mcp_scope 收集 active MCP
  _declare_mcp_scope 收集 active MCP

core/caller_identity/service.py
  collect_bot_active_mcps 查询授权 MCP

core/config_compose/services/collector.py
  collect_bot_active_mcps 作为 mcporter 输入
```

## 12. 与前面 PR 的关系

### 12.1 对 732cd33 的影响

commit：

```text
732cd33cc59019d0803814f662ec51e112461082
fix: sync aicoding local skills in prepub builds (#1024)
```

该 PR 需要保留的能力是：

```text
claude_code + non-normalCC 在 runtime 场景下解析为 aicoding
```

本方案通过：

```text
resolve_runtime_engine_for_bot()
```

保留该能力。

注意：

```text
所有原本依赖 runtime 行为的调用点必须替换为 resolve_runtime_engine_for_bot，
否则会回归该 PR 修复的问题。
```

### 12.2 对 6d7b79b 的影响

commit：

```text
6d7b79b9dcede471d0ff4e24696efbbea9d43799
fix: sync aicoding local skills in prepub builds (#1037)
```

该 PR 隐含双 engine 语义：

```text
runtime/build_plan engine = aicoding
source NAS bucket engine  = bot.active_engine = claude_code
```

本方案与其一致：

```text
build/provider 用 resolve_runtime_engine_for_bot()
source NAS/data 归属用 resolve_engine_for_bot()
```

## 13. 历史数据兼容

可能已经存在错误写入的数据：

```text
Bot.active_engine = claude_code
Bot.template_type != normalCC
SkillSet.engine_type = aicoding
```

修复后目标数据应为：

```text
SkillSet.engine_type = claude_code
```

建议：

```text
1. 短期查询 active SkillSet 可考虑 fallback runtime_engine，但必须打 warning log
2. 中期通过迁移脚本把错误的 aicoding SkillSet 迁回 claude_code
3. 长期禁止该场景继续写入 aicoding SkillSet
```

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

## 14. 防误 clean 保护

本次问题中有一个危险表现：

```text
add skill 成功
但 get_symlink_mappings 因 engine mismatch 返回 []
sync_symlinks([]) 被设备侧解释为 clean
```

建议增加保护：

```text
非显式 clean 场景下，如果本次 DB 操作成功但 mapping 为空，
不要直接下发 clean。
```

可选策略：

```text
1. skip sync，记录 error log
2. 返回 sync_failed，但保留 DB 写入
3. 区分 explicit clean 和 abnormal empty snapshot
```

原则：

```text
clean 只能由显式清理动作触发，不能由异常空快照隐式触发。
```

## 15. 推荐落地顺序

### P0：Resolver 语义修正

```text
1. 新增 resolve_runtime_engine_for_bot()
2. 修改 resolve_engine_for_bot() 返回 bot.active_engine
3. 保留 override / fallback 行为
```

### P1：替换 runtime 调用点

把以下类别全部替换为：

```python
resolve_runtime_engine_for_bot()
```

```text
build/provider
work/config dir
resource/file path
identity file I/O
skill readme
local symlink path
```

### P2：SkillSetService 双 engine

```text
1. SkillSetService.engine_type = data engine
2. SkillSetService.runtime_engine_type = runtime engine
3. DB 查询继续用 engine_type
4. symlink/source/target/CLI runtime path 用 runtime_engine_type
```

### P3：MCP / AgentPass / mcporter

```text
1. collect_bot_active_mcps 查询 active SkillSet 用 data engine
2. refresh_mcp_scope 收集 active MCP 用 data engine
3. _declare_mcp_scope 二次收集也用 data engine
4. mcporter 输入 MCP 收集用 data engine
5. 默认 MCP/CLI 是否用 runtime engine 单独验收
```

### P4：历史数据迁移与防误 clean

```text
1. 迁移错误写入 aicoding 的 SkillSet
2. 增加 abnormal empty snapshot 防护
```

## 16. 最终目标状态

对于：

```text
bot.active_engine = claude_code
template_type != normalCC
```

最终行为：

```text
resolve_engine_for_bot()         = claude_code
resolve_runtime_engine_for_bot() = aicoding

SkillSet.engine_type             = claude_code
active SkillSet 查询             = claude_code
MCP active 查询                  = claude_code
AgentPass 查询                   = claude_code
mcporter 输入 MCP 收集           = claude_code

build/provider                   = aicoding
work/config/runtime path          = aicoding
Skill source path                 = /home/admin/.aicoding/skills-repo/...
Skill target path                 = /home/admin/.claude/skills/...
CLI runtime sync                  = aicoding layout
source NAS bucket                 = claude_code
```

一句话总结：

```text
默认 engine 就是 bot.active_engine；只有运行时路径和 provider 场景才显式 resolve runtime engine。
```
