---
agent: tc-review
status: completed
owner: Fengtai
created: 2026-10-16T00:00:00+08:00
iteration: 1
---

# 开发报告：协作群成员智能推荐

## 实现依据

- Spec：同目录 `001-spec-output.md`（status: approved，2026-10-16 批准）
- PRD：《协作群成员智能推荐_PRD》v1.1（已批准，产品 PRD 知识库）
- 分支：`dev_collab_member_recommend`（基于远端最新 dev）
- 部署形态：`[SHAPE:SAME]`——通用功能，Open（Avernet）与 Internal（OCB）行为一致，待同步 OCB 同名分支

## 实现范围

### 服务层（apps/frontend-nextgen/src/services/workspace/collaborationCandidateService.ts）

- 新增 `RecommendCandidateView` 类型：`CollaborationBotView` 附加 `recommendScore`（推荐分）与 `shortProfile`（画像摘要）；
- 新增 `recommendCandidates` 方法：调用公开 Bot 智能发现接口（`keyword`、`top_k=20`、`min_score=0.01`、viewer 身份按 actorKind 映射 bot/human，不下发 `runtime_state`——协作群推荐需要在线+离线混合候选）→ 按返回 `bot_uuid` 与传入可协作列表求交集 → 在线优先、推荐分降序排序；空关键词直接返回空列表；异常统一转 `DomainError`（友好提示"推荐可协作 Bot 失败，请稍后重试。"）。

### Hook 层（apps/frontend-nextgen/src/pages/Workspace/hooks/useGroupCollaborationPicker.ts）

- 新增 `filterMode`（`'name' | 'profile'`，默认按名称）与 `setFilterMode`；
- 搜索分流：按名称模式完全保留原有 `listCandidates` 链路；按画像模式走推荐链路（350ms 防抖 + 交集输入 `candidatesRef` 同步 + 失败重试 `recommendNonce`）；
- 推荐态：`recommendations`、`isLoadingRecommend`、`recommendError`；清空关键词或切回按名称模式即退出推荐态并清空结果，原可协作列表数据始终保留；
- `recommendByGoal`：协作目标推荐入口——切换到可协作 Bot Tab + 按画像模式 + 目标文本作为关键词，与画像搜索共用推荐链路；
- 弹窗关闭重置：筛选模式回到按名称、搜索词、推荐结果与错误清空；
- `loadMore` 在推荐态不分页，保持推荐结果与原列表数据隔离；
- 签名新增可选参数 `actorKind`（缺省时由 `showMineTab` 推导），保证推荐身份与可协作列表查询身份同源。

### 组件层

- `GroupParticipantPicker.tsx`：仅"可协作 Bot"Tab 显示"按名称筛选 / 按画像筛选"分段切换；推荐态下列表切换为推荐结果（跳过本地名称过滤）；推荐项展示画像摘要（星形图标 + shortProfile）与在线状态徽标；推荐错误优先展示并提供重试；推荐空态提示"暂无与该目标相关的可协作 Bot"；
- `GroupConfigFields.tsx`：协作目标输入框旁新增"智能推荐"按钮（可选 props `canRecommend` / `onRecommend`，目标为空时置灰，不影响其他调用方）；
- `CreateGroupModal.tsx`：picker 传入 `activeIdentity.kind` 作为推荐身份；智能推荐按钮接 `picker.recommendByGoal`。

## AC 验收证据映射

| AC | 实现要点 | 自动化证据 | 页面验收 |
|---|---|---|---|
| AC-01 推荐入口置灰/可点 | `canRecommend={Boolean(context.trim())}` 单行联动 | 无专项断言（逻辑单行），随页面走查确认 | 待验收 |
| AC-02 目标推荐结果 | recommendByGoal + 交集 + 排序 | hook：recommendByGoal 用例；服务：交集与在线优先/推荐分降序用例；组件：推荐项渲染用例 | 待验收 |
| AC-03 推荐空态兜底 | 空态文案 + 原列表保留 | hook：清空关键词恢复用例；组件：空态文案用例 | 待验收 |
| AC-04 画像关键词搜索 | profile 模式防抖推荐链路 | hook：profile 推荐与清空恢复用例 | 待验收 |
| AC-05 名称搜索零回归 | name 模式完全走原有链路 | 既有 hook/组件用例 + 全量测试通过 | 待验收 |
| AC-06 弹窗关闭重置 | 重置 effect 扩展 | hook：关闭重开重置用例 | 待验收 |
| AC-07 推荐失败降级 | recommendError + 重试 | hook：失败用例；组件：错误态 + 重试用例 | 待验收 |
| AC-08 其他 Tab 不受影响 | 模式切换仅可协作 Tab 渲染 | 组件：好友 Tab 无模式切换用例 + 既有用例 | 待验收 |
| AC-09 推荐加载中 | isLoading 分流至 Skeleton | hook：isLoadingRecommend 状态断言 | 待验收 |
| AC-10 身份口径一致 | actorKind 与可协作列表同源 | 服务：viewer 映射用例（bot/human） | 待验收 |

## 验证结果

| 验证项 | 结果 |
|---|---|
| 服务层单测 | 11/11 通过（含 4 个新增） |
| Hook 单测 | 9/9 通过（含 6 个新增） |
| Picker 组件测试 | 9/9 通过（含 4 个新增） |
| CreateGroupModal 回归 | 25/25 通过 |
| typecheck | 通过（tsc --noEmit 零错误） |
| lint | 通过（max lint 零告警） |
| 全量测试 | 523 套件 / 3864 用例全部通过，零回归 |

开发过程同步修复一处实现缺陷：切回按名称模式时推荐态残留（Spec FR 退出条件），已由"keeps name mode"用例锁定。

## Mock 台账

无新增 Mock。推荐链路复用既有 `discoverPublicBots` 网关封装与 `listCandidates` 真实接口；全部测试通过 `jest.mock` 在测试内隔离 controller/service，无 sample-only 数据进入业务代码，无 Mock 替换事项。

## 改动文件

| 文件 | 类型 | 改动 |
|---|---|---|
| apps/frontend-nextgen/src/services/workspace/collaborationCandidateService.ts | 源码 | +59 |
| apps/frontend-nextgen/src/pages/Workspace/hooks/useGroupCollaborationPicker.ts | 源码 | +106 |
| apps/frontend-nextgen/src/pages/Workspace/components/Modals/GroupParticipantPicker.tsx | 源码 | +94 |
| apps/frontend-nextgen/src/pages/Workspace/components/Modals/GroupConfigFields.tsx | 源码 | +37 |
| apps/frontend-nextgen/src/pages/Workspace/components/Modals/CreateGroupModal.tsx | 源码 | +4 |
| apps/frontend-nextgen/test/services/workspace/collaborationCandidateService.test.ts | 测试 | +114 |
| apps/frontend-nextgen/test/pages/Workspace/hooks/useGroupCollaborationPicker.test.ts | 测试 | +157 |
| apps/frontend-nextgen/test/pages/Workspace/components/Modals/GroupParticipantPicker.test.tsx | 测试 | +116 |

合计 8 个文件，+657 / -30 行。

## 已知限制与后续事项

- 推荐命中率受可协作 Bot 画像数据质量影响（Spec 已知限制）：画像相关关键词可命中，长句目标可能为空，AC-03 空态兜底保障体验；根治依赖后端（推荐范围过滤、画像覆盖率），后续独立需求推进；
- AC-01 按钮置灰联动、AC-09 加载 Skeleton 为单行联动逻辑，随页面人工验收一并确认；
- 页面级人工验收（入口、推荐结果、空态、错误态、重置的 UI 走查）待进行，建议在本地验证环境或预发完成；
- OCB 内源仓库同名分支同步待进行。

## Git 状态

未 stage、未 commit、未 push。工作区另含本需求 Spec 与本报告（spec/pipeline/collab-member-smart-recommend/）待随代码一并提交。

## 变更记录

| 日期 | 变更内容 |
|---|---|
| 2026-10-16 | 初稿 |