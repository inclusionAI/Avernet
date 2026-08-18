# Skill Center Engine Resolver 拆分方案

## 背景

在给 Skill Set 添加 Skill 后，预期链路应该生成软链映射并调用设备侧 `bindpath`：

```text
POST /api/skillsets/{skill_set_id}/skills
→ add skill association
→ activate skill if Skill Set active
→ get_symlink_mappings()
→ device_sync.sync_symlinks([...])
→ POST /api/skills/symlink/bindpath
```

但在 `claude_code + non-normalCC` Bot 场景中，实际出现了：

```text
POST /api/skills/symlink/clean
```

根因是当前 `resolve_engine_for_bot()` 同时承担了两个不同语义：

1. **数据归属 engine**：Skill Set 属于哪个 engine，DB 查询和写入应该用哪个 `engine_type`。
2. **运行时 engine**：构建、配置、运行时目录布局、sandbox provider 应该走哪个 engine。

在 `claude_code + non-normalCC` 场景下，运行时需要路由到 `aicoding`，但 Skill Set 数据仍然归属于 Bot 的 `active_engine = claude_code`。当前逻辑把 Skill Center 的数据操作也路由成了 `aicoding`，导致：

```text
Skill Set 本身 engine_type = claude_code
请求创建的 SkillSetService.engine_type = aicoding
add_skill_to_set 按 id 写入成功
get_active_skills(engine_type=aicoding) 查不到 claude_code active Skill Set
get_symlink_mappings() 返回 []
sync_symlinks([]) 被解释为 clean
```

## 最终确认方案

拆分两个 resolver，明确语义边界：

```python
resolve_persisted_engine_for_bot()
# 直接返回 bot.active_engine
# 用于 Skill Set / DB / 数据归属 / NAS bucket 归属

resolve_runtime_engine_for_bot()
# 应用 claude_code non-normalCC -> aicoding 路由
# 用于 build / config / sandbox provider / runtime path layout
```

保留当前 `resolve_engine_for_bot()` 的 runtime 语义，作为兼容入口，避免破坏已有调用方：

```text
resolve_engine_for_bot == resolve_runtime_engine_for_bot
```

也就是说，不要把现有 `resolve_engine_for_bot()` 全局改成 persisted 语义。

## Engine 语义定义

### persisted/data engine

表示 Bot 数据在 DB 和存储上的声明归属。

来源：

```text
bot.active_engine
```

用于：

```text
Skill Set 创建/查询/添加/删除/激活
Skill Set active 状态查询
Skill Set 与 Skill 关联数据归属校验
NAS bucket/source bucket 归属
需要按 Bot 原始 active_engine 隔离的数据
```

示例：

```text
bot.active_engine = claude_code
template_type = generalCC

persisted_engine = claude_code
```

### runtime/layout engine

表示实际运行时、构建、配置、设备目录布局所使用的 engine。

来源：

```text
resolve_bot_engine(bot) or bot.active_engine
```

其中：

```text
claude_code + non-normalCC => aicoding
claude_code + normalCC     => claude_code
```

用于：

```text
sandbox provider 选择
build plan 选择
config compose
runtime path layout
Skill source repo path 生成
```

示例：

```text
bot.active_engine = claude_code
template_type = generalCC

runtime_engine = aicoding
```

## Skill Center 使用原则

Skill Center 里需要区分两个 engine：

```text
data_engine_type     = resolve_persisted_engine_for_bot(...)
runtime_engine_type  = resolve_runtime_engine_for_bot(...)
```

### Skill Set 数据操作使用 persisted engine

以下操作使用 `data_engine_type`：

```text
list Skill Sets
get Skill Set detail
create Skill Set
update Skill Set
delete Skill Set
add Skill to Skill Set
remove Skill from Skill Set
activate/deactivate Skill Set
get active Skill Sets
get active Skills
```

原因：这些操作本质上是在读写 DB 中的 Skill Set 归属，应该以 Bot 的 `active_engine` 为准。

### 软链 mapping 生成需要 runtime engine

软链同步链路中，应拆成两段：

```text
1. 查询 active Skill Sets / active Skills
   使用 data_engine_type

2. 生成 source / target 映射
   使用 runtime_engine_type
```

对于 `claude_code + non-normalCC`：

```text
data_engine_type    = claude_code
runtime_engine_type = aicoding
```

因此应该查到 `claude_code` 的 active Skill Set，但生成 aicoding runtime 的 source 路径。

## aicoding 软链路径规则

当前 aicoding 的路径语义：

```text
source repo dir:
  /home/admin/.aicoding/skills-repo

target skills dir:
  /home/admin/.claude/skills
```

例如 Skill：

```text
git://infra/pmo/agent-coding-process-skill
```

对应 mapping：

```json
{
  "source": "/home/admin/.aicoding/skills-repo/infra/pmo/agent-coding-process-skill",
  "target": "/home/admin/.claude/skills/agent-coding-process-skill"
}
```

注意：`claude_code` 和 `aicoding` 的 target 都可能是 `/home/admin/.claude/skills`，但这不代表两个 engine 的 Skill Set 数据可以混用，因为 active Skill Set 查询和 source repo 路径仍然依赖 engine 语义。

## 对已有关联 PR 的影响

### 对 732cd33 的影响

PR/commit：

```text
732cd33cc59019d0803814f662ec51e112461082
fix: sync aicoding local skills in prepub builds (#1024)
```

该改动核心语义是：

```text
claude_code + non-normalCC
=> runtime engine = aicoding
```

拆 resolver 后，只要保持：

```text
resolve_runtime_engine_for_bot 仍然执行 claude_code non-normalCC -> aicoding
resolve_engine_for_bot 兼容 runtime 语义
```

就不会破坏该 PR。

风险点：

```text
如果把现有 resolve_engine_for_bot 全局改成只返回 bot.active_engine，
则 runtime/build/config 不再路由到 aicoding，会破坏 732cd33 的功能。
```

### 对 6d7b79b 的影响

PR/commit：

```text
6d7b79b9dcede471d0ff4e24696efbbea9d43799
fix: sync aicoding local skills in prepub builds (#1037)
```

该改动本身已经隐含了双 engine 语义：

```text
runtime/build_plan engine = aicoding
source NAS bucket engine  = bot.active_engine = claude_code
```

拆 resolver 后，应该对应为：

```text
build provider / build_plan:
  resolve_runtime_engine_for_bot() => aicoding

source_nas_engine_type:
  resolve_persisted_engine_for_bot() 或 bot.active_engine => claude_code
```

因此正确拆分不会影响 6d7b79b，反而会让它的语义更清晰。

风险点：

```text
如果 BotBuildService 的 provider/build_plan 误用 persisted resolver，
则 non-normalCC 不会走 aicoding build plan，会破坏该 PR。
```

## 推荐落地策略

### 阶段一：新增 resolver，保持兼容

新增：

```python
resolve_persisted_engine_for_bot()
resolve_runtime_engine_for_bot()
```

并保持：

```python
resolve_engine_for_bot = resolve_runtime_engine_for_bot
```

这样可以最大限度降低对现有调用方的影响。

### 阶段二：Skill Center 数据入口切 persisted engine

将 Skill Center 中与 Skill Set 数据归属相关的 `_get_path_params()` 切到 persisted resolver。

重点覆盖：

```text
/api/skillsets/*
/api/skills/* 中涉及 Skill Set active/list/detail/switch 的接口
```

尤其是：

```text
POST /api/skillsets/{skill_set_id}/skills
DELETE /api/skillsets/{skill_set_id}/skills/{skill_id}
activate/deactivate Skill Set
```

### 阶段三：SkillSetService 支持 runtime_engine_type

在 `SkillSetService` 中保留：

```text
self.engine_type
```

作为 data engine。

新增概念字段：

```text
self.runtime_engine_type
```

用于 mapping/path layout。

推荐语义：

```text
self.engine_type = persisted/data engine
self.runtime_engine_type = runtime/layout engine
```

`get_active_skills()` / `get_all_active_skill_sets()` 使用：

```text
self.engine_type
```

`get_symlink_mappings()` 生成 source/target 时使用：

```text
self.runtime_engine_type
```

### 阶段四：增加保护，避免误 clean

即使 resolver 修复，也建议增加保护：

```text
在 add Skill 成功、Skill Set active、activate_skill 成功的链路中，
如果 get_symlink_mappings() 返回空，不要直接 sync_symlinks([])。
```

可选策略：

```text
1. skip sync，并记录 error log
2. 返回 sync_failed，但不抛异常回滚已写 DB
3. 显式区分 clean 操作和 bindpath 操作，避免空 snapshot 被解释为 clean
```

这个保护可以防止类似 engine mismatch、查询条件异常、数据损坏等问题再次触发清空远端软链。

## 推荐实现边界

### engine_resolver.py

建议提供三个入口：

```python
resolve_persisted_engine_for_bot(...)
resolve_runtime_engine_for_bot(...)
resolve_engine_for_bot(...)  # backward compatible alias/wrapper to runtime resolver
```

### Skill Center Router

Skill Set 数据操作：

```text
use resolve_persisted_engine_for_bot
```

需要生成 runtime mapping 的地方，同时传入：

```text
data_engine_type
runtime_engine_type
```

### SkillSetServiceFactory / SkillSetService

Factory create 参数建议逐步扩展为：

```python
engine_type: persisted/data engine
runtime_engine_type: runtime/layout engine | None = None
```

默认：

```python
runtime_engine_type = engine_type
```

这样对现有测试和调用方兼容。

### BotBuildService

保持当前双 engine 语义：

```text
provider/build_plan 使用 runtime engine
source_nas_engine_type 使用 persisted engine / bot.active_engine
```

不要改成全 persisted，也不要改成全 runtime。

## 验证用例

### resolver 单测

覆盖：

```text
bot.active_engine = claude_code, template_type = generalCC
resolve_persisted_engine_for_bot => claude_code
resolve_runtime_engine_for_bot   => aicoding

bot.active_engine = claude_code, template_type = normalCC
resolve_persisted_engine_for_bot => claude_code
resolve_runtime_engine_for_bot   => claude_code

bot.active_engine = aicoding
resolve_persisted_engine_for_bot => aicoding
resolve_runtime_engine_for_bot   => aicoding
```

### Skill Center 链路测试

构造：

```text
Bot.active_engine = claude_code
Bot.template_type = generalCC
SkillSet.engine_type = claude_code
SkillSet.is_active = true
Skill.git_path = git://infra/pmo/agent-coding-process-skill
```

执行：

```text
POST /api/skillsets/{skill_set_id}/skills
```

期望：

```text
SkillSetService data engine = claude_code
active Skill Set 能被查到
get_symlink_mappings() 非空
sync_symlinks([...]) 走 bindpath，不走 clean
```

mapping 期望：

```json
{
  "source": "/home/admin/.aicoding/skills-repo/infra/pmo/agent-coding-process-skill",
  "target": "/home/admin/.claude/skills/agent-coding-process-skill"
}
```

### BuildService 回归测试

覆盖 6d7b79b 语义：

```text
active_engine = claude_code
template_type = generalCC
runtime/build_plan engine = aicoding
source_nas_engine_type = claude_code
source_dir = /home/admin/.merge_nas/..._claude_code_.../.aicoding
```

## 总结

最终方案不是简单地把 engine 从 `aicoding` 改回 `claude_code`，而是明确拆分：

```text
数据归属：claude_code
运行时布局：aicoding
```

对于本次问题：

```text
Skill Set 查询/写入应该用 claude_code
软链 source path 应该用 aicoding
软链 target path 仍然是 /home/admin/.claude/skills
```

这样既能修复 Skill Center 添加 Skill 后误 clean 的问题，又不会破坏前面 aicoding prepub build 相关 PR 的功能。
