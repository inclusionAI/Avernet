# Skill 能力升级 Spec × 产品 PRD Mock 对照

> 状态：产品一致性 Review 证据，不是第二份领域 Spec
>
> Backend 基线：`github/dev@b68ec64f1698a931585612801f2db6529c8ec4aa`
>
> 产品基线：`Teamclaw_PRD_new origin/master@72de5d1c12ea1b7e51a00f36a56f6ab718ee26a3`
>
> 正式领域权威：同目录 `spec.md`

## 1. 证据边界

产品仓库是 React/TypeScript Mock，没有 Backend 请求、失败恢复或持久状态实现。本文只把可见
页面、按钮、字段和 Mock state transition 当作产品证据；原型没有画出的超时、并发、幂等、
回滚、Runtime、Artifact 行为不能反推为产品结论。

结论分为：

- **满足**：正式 Spec 已提供完成该产品动作所需的领域与 OpenAPI。
- **前端需对齐**：最终领域决策已明确，Mock 仍保留被替换的旧交互。
- **已由产品确认**：Mock 与最终决定存在差异，PD/前端将按正式 Spec 修订。
- **原型未定义**：Spec 为可靠性补齐的行为，不能说与 PRD 冲突。

## 2. 能力工坊

| 产品动作 | Mock 证据 | 正式 Spec | 结论 |
| --- | --- | --- | --- |
| 在 Personal/Team Space 查看 Skill 卡片 | `CapabilityWorkshop.tsx:385-456,680-923` | `SpaceSkillSummary` 返回生命周期、Version、Draft、Attempt、Owner、Actor permissions、Lease summary | 满足 |
| 在能力工坊同时展示当前 Space 的市场收藏 | `CapabilityWorkshop.tsx:900-909` | Space Skill list 与 market-favorites search 分别读取，由前端组合分区 | 满足；收藏不是 Space-owned Skill，不能混入 SpaceSkillSummary |
| 搜索能力工坊 Skill | `CapabilityWorkshop.tsx:821` | Backend `keyword + page/page_size`，过滤后分页，稳定倒序 | 满足；前端不全量拉取后本地过滤 |
| 文件夹创建 V1 | `CapabilityWorkshop.tsx:201-240,928-979` | multipart `files + file_paths`，持久 Identity/Owner/V1 Draft | 满足；Mock 只模拟选择，Spec 补齐持久化 |
| Git snapshot 创建 | `CapabilityWorkshop.tsx:241-270` | Git URL/branch/subdir、确定性第一 `SKILL.md`、commit/source_subdir 固化 | 满足；多 Skill/失败行为属于原型未定义 |
| 保存 Draft | `CapabilityWorkshop.tsx:928-979` 的“保存/草稿已保存” | immutable ZIP Revision + expected revision CAS；V1 保存后即有 Draft | 满足 |
| 文件树、读取、编辑、保存单文件 | `CapabilityWorkshop.tsx:273-383` | Draft files tree、file GET/PUT | 满足 |
| Git 手动更新 | `CapabilityWorkshop.tsx:343-357` | `POST .../draft/refresh-from-git`，只读冻结 source_subdir | 满足 |
| 发布前规范校验和安全扫描 | `CapabilityWorkshop.tsx:1128-1248` | Publication Attempt + SC checks + Materializer；PUBLISHED 才成功 | 满足；原型没有画 materialization/失败/恢复 |
| 发布失败/RESULT_UNKNOWN/物化失败恢复 | 原型未画 | Attempt Retry + recovery summary | 原型未定义，Spec 有完备合同 |
| 查看发布影响 | Mock 在“确认升级”时展示 Bot，`CapabilityWorkshop.tsx:1046-1076` | 最终接口为 `publication-impact`，在真正发布前提示；upgrade 仅创建 Draft | **已由产品确认**：PD 后续把影响弹窗从 upgrade 移到 publish confirm |
| 升级 Vn→Vn+1 | `CapabilityWorkshop.tsx:1046-1076` | 从 exact Published Vn 创建 Vn+1 EDITING Draft | 满足，除影响弹窗时机 |
| 下线 | Mock 无引用时把 running 改回 draft，`CapabilityWorkshop.tsx:1078-1125` | 不可逆 TeamClaw-local Retirement；Versions/Draft 历史保留，不回 Draft | **前端需对齐**：旧“下线后回草稿”已被最终决策替换 |
| 下线前引用阻断 | 同上，只展示 Bot refs | Retirement impact 覆盖 Membership、Installation、Attempt、Artifact、UNKNOWN_ARTIFACT | Backend 更严格且满足安全需求；前端需支持非 Bot blocker 分类/counts |
| 删除未发布 Skill/放弃升级 Draft | Draft 卡片“删除”，`CapabilityWorkshop.tsx:762-770` | `DELETE .../draft` 返回 `deleted_scope=SKILL|DRAFT`，FROZEN 拒绝 | 满足 |
| Team 编辑锁、抢占、关闭释放 | `CapabilityWorkshop.tsx:572-662,742-751,928-947` | 永久 Lease、fencing、acquire/release/takeover，无 TTL | 满足 |
| 普通成员申请编辑权限 | `CapabilityWorkshop.tsx:554-569,746-760` | `editor-requests` + `SKILL_COLLABORATOR` Work Order | 满足 |
| Owner/Manager 授权 | `SkillAuthDrawer`, `CapabilityWorkshop.tsx:55-189` | 唯一 Owner + 多 Manager；Owner PUT/DELETE managers | 领域能力满足；前端 `admin/member` 术语需映射为 Owner/Manager |
| Owner 转移与原因 | `CapabilityWorkshop.tsx:465-502,1251-1321` | 原子 Owner transfer、reason、旧 Lease 失效、原 Owner 不自动保留 Manager | 满足 |
| 历史版本下拉与文件查看 | `SkillDetail.tsx:18-179` | Versions list/detail/tree/file GET | 满足 |
| 编辑“展示名称/描述/图标”并即时生效 | `CapabilityWorkshop.tsx:984-1036` | Published SKILL.md 不可修改；Draft 可编辑但 name 永久不变、description 随 Version；本期无独立 presentation mutation | **已确认移除即时编辑语义**；图标使用默认展示 |

### 2.1 元信息冲突必须显式关闭

Mock 的“编辑 Skill 信息”同时表达：

```text
Skill 名称只读
Skill ID 只读
展示名称可改
功能描述可改
图标可改
修改后即时生效、无需重新发布
```

最终决定为：Published `SKILL.md` 不可原地修改；Draft 中可以编辑文件并发布新 Version，但
`name` 永久不可变，`description` 只随 Version 生效。本期不实现该即时编辑弹窗的 Backend
写接口，图标使用默认展示。未来若需要独立展示名称/图标，只能 additive 增加 Asset
presentation metadata，不能映射到 `ac_skill.name` 或绕过 Version。

## 3. 能力市场

| 产品动作 | Mock 证据 | 正式 Spec | 结论 |
| --- | --- | --- | --- |
| SkillCenter/TeamClaw 两个来源 Tab | `PublicMarket.tsx:15-18,151-243` | 两个独立查询：SC Public search、TeamClaw Repository Catalog | 满足 |
| SkillCenter 分类筛选 | `PublicMarket.tsx:182-198` | SC search tags/official/recommended/belongTo | 满足 |
| TeamClaw 业务目录 | `PublicMarket.tsx:229-241` | Repository tree/path | 满足 |
| 手动同步最新 Skill | `PublicMarket.tsx:177-179` | TeamClaw Repo sync 与 SC materialized-assets sync 两个接口 | 满足；前端必须按当前来源分流 |
| 收藏到当前 Space | `PublicMarket.tsx:32-53,207-218` | 冻结的 Space market-favorites add/cancel/search/status | 满足 |
| 从市场选择 Bot 和 SkillSet | `PublicMarket.tsx:55-90,328-423` | Bot/SkillSet ACL + Membership/SC Reference | 满足 |
| TeamClaw 市场添加 | 同上 | 已有 `skill_id`，普通 Membership PUT | 满足 |
| SkillCenter Public 添加 | 同上 | 持久异步 Reference Operation、202/轮询、懒物化后 Membership | Backend 满足；Mock 只 toast 成功，**前端需增加处理中/部分失败/恢复** |
| 查看市场 Skill 详情 | BotEdit 卡片跳 `#/skill/{marketId}`，`BotEdit.tsx:331-334` | 已物化共享资产可用 Botless `readme`；未物化 SC 使用 search `homepageUrl` | 满足；前端已通过 iframe 嵌入 SC 页面，查看不触发物化 |

## 4. Bot 工坊能力集

| 产品动作 | Mock 证据 | 正式 Spec | 结论 |
| --- | --- | --- | --- |
| 能力集列表、折叠、Skill/MCP 数量 | `BotEdit.tsx:39-57,213-294` | SkillSet list/resources/skills/mcps | 满足 |
| 新建能力集 | `BotEdit.tsx:76-83,296-298` | POST 创建 active ordinary Set；空集合不触发 Runtime | 满足，与最新 dev 语义一致 |
| 整体开关 | `BotEdit.tsx:224-245` | activate/deactivate 原子维护 Skill/MCP Installation | 满足 |
| 删除能力集 | `BotEdit.tsx:85-94,240-245` | 只允许删除 inactive ordinary Set，Default 禁止 | Backend 满足；前端需先关闭或处理 409 |
| 市场/工坊多选 Skill | `BotEdit.tsx:63-103,300-341` | TeamClaw/工坊普通 Membership；SC Public 批量 Reference 最多 20 | 满足 |
| 本地文件夹 | 当前 master 的选项仅 market/workshop；用户后续 PRD 截图已出现“添加本地文件夹” | multipart Bot-local Local upload + Membership | Backend 已覆盖；master Mock 落后最新产品图 |
| 添加/移除 Skill | `BotEdit.tsx:250-269` | SkillSet Membership PUT/DELETE；active Set 同事务维护 Installation | 满足 |
| 添加/移除 MCP | `BotEdit.tsx:271-289,343-374` | MCP Membership + permission flow | 满足 |
| MCP Owner/Caller 身份 | `BotEdit.tsx:114-209` | 已发布 caller-context 与 per-MCP call-type PATCH | 满足，不应重复进 SkillSet 表 |
| Default Set/CLI/exclusion | Mock 未完整画出 | 既有 resources、Default exclusion、CLI 接口保持 | 原型未定义，Backend 向前兼容 |

## 5. Runtime、Track Latest 与 Service Artifact

产品 Mock 没有真实 Runtime/Artifact Consumer，因此只能验证用户可见结果，不能证明下列合同。
正式 Spec 已补齐并必须以真实 Consumer 验收：

| 场景 | Spec 结果 | 产品可见结果 |
| --- | --- | --- |
| active Set 添加 Center Skill | Reference 完成后 Installation + exact Runtime Projection | 最终显示已添加；处理中由 Operation 状态驱动 |
| Space/SC Public 发布新版本 | PUBLISHED 后 Track Latest 异步扇出 | 发布先成功；Bot 更新不阻断成功弹窗 |
| Bot restart/re-ACTIVE | Reader + Resolver + full Projector；首次失败有有界 Retry | Bot 恢复后使用 latest exact Version |
| Service 新 Release | build 前完整 Projector，Artifact 冻结 exact Center/MCP | 新 Release 使用最新 PUBLISHED |
| Service restart/scale/rollback | 只读历史 Artifact | 历史版本不漂移 |
| Retirement | replayable Artifact 是硬 blocker | 产品显示受影响 Service Bot/Version，禁止继续 |

## 6. 明确不属于缺口的原型差异

- 原型没有超时、并发、幂等、补偿、Task deadline、RESULT_UNKNOWN 和 UNKNOWN_ARTIFACT；这些是
  Backend 必须补齐的可靠性合同，不需要 Mock 先画出来。
- 原型把状态压成 `draft/running/offline`；正式 Spec 使用 Asset、Draft、Attempt、Version、
  Retirement 分离状态，前端必须映射，不能要求 Backend 回到单 status。
- 原型只模拟文件内容；Canonical OSS、Mapping v3、Pool、Teclaw v4 和 Artifact 冻结不属于页面
  ViewModel，但仍是发布门禁。
- 产品入口没开放某个 Bot Type × Engine 组合不等于 Center 技术不支持；Backend 不编码静态拒绝。

## 7. Review 结论

正式 Spec 已覆盖产品主链路：创建、保存、协作编辑、发布、版本、市场发现/收藏、加入能力集、
SkillSet 原子开关、MCP、Track Latest、Service 历史重放和退役。

产品合同未再保留开放问题。Frontend Guide/Ticket 阶段需要执行的已定稿调整：

1. 删除“展示名称/描述/图标即时生效”的 Backend 依赖；Published 内容只读，修改走 Draft/Version。
2. 能力工坊搜索调用 Backend `keyword/page/page_size`。
3. 未物化 SC 详情继续使用现有 `homepageUrl` iframe，不触发物化。
4. 把升级影响弹窗移到发布确认，把“下线回草稿”替换为 Retirement，并支持完整 blocker。
5. 实现 SC Reference Operation 的处理中、部分成功、刷新恢复；Mock 当前只有同步 toast。
6. 新建 SkillSet 默认 active；空集合不触发 Runtime，后续新增成员立即生效。
