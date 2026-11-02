# Bot MCP override lifecycle and Manifest configuration intent

Status: implementation in progress. Base: dev with #3220 merged. Target: dev.

## Contract

Bot overrides belong to the Bot and MCP identity, independently of SkillSet membership and effective supply. SkillSet activation/deactivation, membership changes, deletion, Default exclusion, Installation reconciliation and capability compensation preserve overrides. Re-enabling reads the current saved override, with existing permission, Center and engine validation. Preserving configuration does not enable a capability.

Explicit Direct uninstall and Bot deletion retain their existing cleanup contract.

The runtime contract covers discovery and tool exposure: when no effective
supply remains, `mcporter list`, normal Agent tool discovery and
`listPolicy.allowServers` exclude that MCP. Normal Agent use discovers tools
before calling them; this does not require a fresh list before every call.
An explicit CLI call using an already known server name can still execute
against retained runtime configuration. Blocking that existing path is not
part of this change and requires no new runtime format or mcporter upgrade.

Manifest configuration intent is layered:

| Declaration | Effect |
| --- | --- |
| MCP category absent | Leave supply and configuration unchanged. |
| MCP entry present, config absent | Preserve its current Bot override. |
| Explicit empty config object | Clear the override and inherit existing configuration sources. |
| Nonempty config object | Replace the complete override. |
| MCP parent removed by Replace | Remove managed supply and its override. |

Parent cleanup retains scheme A and the Direct-only scope merged in #3220. Current Set supply is outside the declaration: omission leaves it untouched, explicit overlap refuses the whole Apply before writes. Transactional claim/remove supply guards remain mandatory. Non-Set override-only entries remain cleanup candidates.

Genome is a complete snapshot of the existing Direct capture scope: absent override still means clear. It is not a backup of every dormant override. Manifest preserve must not change Genome restoration or expand capture/drift scope.

## Implementation boundaries

Remove only implicit lifecycle cleanup. Carry explicit configuration intent through compiler, desired-state model, validation, planning and transactional claims. Preserve performs no configuration write and does not replay a previously read value. Keep existing single-MCP transactions, runtime best-effort delivery and failure reporting.

No database migration, new configuration CRUD, SkillSet configuration snapshot or public YAML shape is needed. Header merging, custom-URL credential isolation, authorization and source exclusivity remain unchanged. Callers that used a bare MCP entry to reset configuration must send an explicit empty config object after this change.

### Required SQLite transaction assembly correction

The Direct MCP claim's Installation and explicit configuration write must roll
back together on a persistence failure. Real failure injection exposed Python
SQLite's legacy transaction mode releasing an Installation SAVEPOINT before
an outer transaction had begun, leaving that Installation committed after a
later configuration INSERT failed. The composition root selects a dedicated community SQLite plugin; it and the
local SQLite plugin
explicitly start `BEGIN IMMEDIATE` when entering `transactional_orm_session`,
before application reads or SAVEPOINTs. Ordinary `orm_session` keeps its prior
read behavior, and existing guarded lifecycle callers retain their own explicit
transaction setup. Foreign-key enforcement and non-SQLite assembly remain
unchanged. This honors the existing single-MCP supply/config atomicity and
write-serialization guarantees without a new API or schema.

## Validation

Use existing service/Apply Engine entry points, real persistence and existing Runtime/Center ports. Validate compiler effects against Genome restoration results rather than equating preserve and clear structures. Verify Direct-only whole-Apply conflict handling and transactional supply checks. Validate actual whole-artifact contents, not only delivery calls.

SQLite regressions exercise the actual database plugins as well as the MCP
claim seam: inject a configuration write failure, verify both writes roll
back, and retain nested-savepoint recovery and foreign-key enforcement.

Enterprise validation uses matching OCB simulator sources and records the running image/source identity. OCB integration tests must preserve Set/default supply after Direct Replace; a temporary source override does not replace a recorded gitlink update. Enterprise mirror synchronization is manual, so its integration PR remains WIP until available.

## Acceptance matrix

| 编号 | 场景 | 必须成立 |
| --- | --- | --- |
| AC01 | 停用普通集，重复对账，再启用 | override 保留且重启后仍在；Membership/Installation 各按合同变化；无有效供应时 mcporter list、正常工具发现及 allowServers 不含停用项；启用读取当前值。 |
| AC02 | 从 active/inactive 集移除 MCP，或停用后删除集 | override 不丢失；删除集不恢复或清理无关配置。 |
| AC03 | 停用期间通过已有配置入口更新，再启用 | 新保存值生效；无旧快照回写；保存本身不启用能力。 |
| AC04 | Default exclusion/unexclude | 配置保留，来源冲突规则有效；无其他来源时 scope 撤销，有其他来源时仍按实际供应可用。 |
| AC05 | 同一码兼有显式集供应与 Skill dependency，撤销前者 | 有效依赖继续使用保存 override；不静默退回继承配置。 |
| AC06 | 能力事务失败及既有 restore 补偿 | 能力事实按原合同回滚/恢复；独立配置不被清理或旧快照覆盖。 |
| AC07 | Direct 显式卸载、Bot 删除 | 继续清配置；必要写入失败不返回虚假成功。 |
| AC08 | Manifest 类别缺席、条目 config 缺席、空对象、非空对象 | 分别满足不操作、保持、清除、完整替换；重复应用有正确幂等报告。 |
| AC09 | 保存 override 与继承配置的有效性不同，再应用 bare entry | 校验保存 override；覆盖“保存值有效/继承值无效”和反向组合，避免误拒绝或误放过。 |
| AC10 | Manifest 移除 MCP 父条目或 mcp: [] | 按 Direct 与非 Set override 候选级联清理，包括 override-only、纯 dependency 仍供应场景；后者能力保留且报告反映配置更新，不伪报能力移除。 |
| AC11 | 集停用保留 override 后，显式 Manifest Replace 清理，再启用 | 被明确重置的值不恢复；未重新配置时按当前继承来源处理。 |
| AC12 | Genome 恢复无 override 快照 | 能清除后来新增 override；恢复有 override 快照仍完整替换。 |
| AC13 | 当前权限拒绝、Center 元数据/协议/引擎失效 | 区分提交前拒绝与提交后未收敛；无配置静默丢弃、默认回退或 scope 越权。 |
| AC14 | 另一 Bot、另一隔离作用域及用户级配置 | 全部不受目标 Bot 配置保留/清除影响。 |
| AC15 | 省略活跃普通 Set、Default、平台默认供应的 MCP | 供应、成员关系、exclusion、override 均不变；企业默认能力仍在设备与 allow-list 中。 |
| AC16 | 显式声明当前 Set 供应的 MCP，或非空能力声明的 Set 冲突预检查不可可靠完成 | 包括 bare/空 config/非空 config；整次 Apply 在任何类别写入前拒绝，无 script、资源等其他类别副作用。 |
| AC17 | 预检后 Set 开始供应，随后 claim/remove | 既有事务内持锁复核拒绝当前单 MCP 操作，不抢占或清其配置；按原部分提交合同报告，不承诺全 Apply 回滚。 |
| AC18 | Manifest → capture → Genome restore 的结果等价 | 初始有/无 override 均覆盖；bare Manifest 保留真实值，capture 记录 Direct 的实际值，扰动后恢复到捕获状态；不抹平 preserve/clear 意图。 |
| AC19 | 停用集留下暂存配置 → capture → 修改该值 → drift → Apply | 暂存值不成为 Direct、不进入 capture、不单独触发 drift；完整 Apply 按非 Set 候选清理，且不恢复被明确重置的值。 |
| AC20 | Teclaw 首次 artifact 与后续 redeliver | 验证实际 artifact：preserve 使用当前值、clear 使用继承值、replace 使用新值，未供应暂存配置不泄漏进 artifact；活动供应范围符合 Direct-only Replace 后的全量有效集合。 |

## Stable configuration addressing and legacy compatibility

Bot overrides use the Bot's immutable `entity_id` in the legacy `owner_id`
storage column. Human user IDs continue to select permissions, ownership,
audit identities and user-level defaults. Core override reads accept an explicit
entity coordinate. Scoped commands resolve the authenticated human owner's live
Bots and recheck their ownership under the write transaction; synchronization
resolves legacy locators only at its public compatibility boundary, never in the
core configuration read path.

A canonical row wins as a complete value, including an empty object. Only when
that row is absent may a read use the existing Bot's current-owner legacy row,
and only when the tenant/env-qualified locator uniquely identifies that same
Bot. Reads do not migrate data. A shared Bot ID, another Bot's owner/entity
collision or unrelated tenant/env must never make an alias authoritative.
A candidate key already assigned as another Bot's explicit entity is excluded
from compatibility, including when that Bot is soft-deleted; its configuration
is never read or removed for the current owner. Otherwise ambiguous legacy human
ownership is rejected. Unknown orphan rows from past handoffs are not reconstructed by scanning Bot IDs.

Explicit replacement, clear, Direct uninstall, Manifest parent removal and Bot
deletion consume attributable aliases in the same transaction as the canonical
write. An explicit Manifest configuration still reaches that transaction when
the reported effective outcome is unchanged; omitted configuration performs no
configuration write. Scoped Header/URL operations retain their existing snapshot
and partial-field semantics, including preservation of undeclared URL fields.

The existing owner-handoff finish transaction normalizes still-recognizable
current-owner MCP rows before changing responsibility, preserving canonical
values and rolling back normalization, owner and journal changes together on
failure. Ordinary configuration reads do not query the optional handoff schema;
this does not enable the handoff feature or add a product entrypoint.

`McpConfigWriteIntent[T]` is the shared internal preserve/clear/replace contract:
a value of T replaces, None clears, and an immutable `PreserveMcpConfig` marker
performs no write. T is McpOverride in Desired State and a validated mapping at
the persistence port. McpOverride describes the configuration value, not an
operation. No single-member action enum, new entity, persisted action or
YAML/Genome field is introduced. Copying an intent preserves its meaning.

A publicly unchanged Apply entry may still claim configuration to consume safe
legacy aliases. Only an effective supply/configuration change needs MCP
projection. Other commands retain repeated reconciliation after failed Runtime
delivery even when the stored Desired State has not changed.
