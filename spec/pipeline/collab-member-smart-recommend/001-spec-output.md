---
agent: tc-review
status: approved
owner: Fengtai
created: 2026-10-15T00:00:00+08:00
updated: 2026-10-16T00:00:00+08:00
approved_by: Fengtai
approved_at: 2026-10-16
iteration: 1
---

# 系分 Spec: 协作群成员智能推荐（发起协作弹窗）

## 需求概述

在新版工作台「发起协作」弹窗中补全老版本已有的两个成员推荐能力：

1. **协作目标智能推荐**：协作目标输入框联动「智能推荐」按钮，基于目标文本推荐可协作成员 Bot；
2. **画像关键词搜索**：成员选择器「可协作 Bot」Tab 支持按画像关键词进行语义搜索推荐。

两个能力共用同一推荐链路：

```text
关键词（协作目标 / 画像关键词）
  → GET /openapi/v1/bots/catalog/discover（viewer 为当前协作身份）
  → 与弹窗已加载的可协作 Bot 列表求交集
  → 在线优先 + 推荐分降序排序
  → 回流展示到「可协作 Bot」Tab 列表
```

### 运行时验证结论（预发环境实测）

- discover 接口 Bot 视角（不下发 runtime_state）返回在线+离线混合候选，与「在线+离线都推荐」的产品决策一致；
- 响应字段包含 `bot_uuid`（与可协作列表的 `CollaborationBotView.id` 同构，可直接求交集）、`recommendation.score`、`recommendation.short_profile`、`is_online` 等；
- 语义搜索不能替代按名称搜索（以 Bot 名字为关键词时语义搜索未召回名字匹配项）——名称与画像必须双模式并存；
- 交集规模受可协作 Bot 画像数据质量影响：画像相关词可命中，长句目标可能为空——空交集兜底为本 Spec 强制要求。

## 范围与目标

### 纳入范围

- frontend-nextgen 发起协作弹窗（CreateGroupModal 及其子组件、picker hook、候选数据服务层）；
- 复用现有 `discoverPublicBots` 网关封装与 `collaborationCandidateService` 候选数据层，不新增接口、不修改后端。

### 不纳入范围

- 好友 Bot、已管理 Bot Tab 的搜索行为（保持现状）；
- 推荐效果埋点（本期不做）；
- 推荐质量根治（discover 接口增加可协作过滤参数、画像覆盖率提升），列为后续独立需求；
- 后端与网关改动。

### 部署形态

- `[SHAPE:SAME]`：通用功能，Open（Avernet 开源仓）与 Internal（OCB 内部仓）行为、入口、数据源一致；Avernet 仓 `dev_collab_member_recommend` 分支开发后同步 OCB 同名分支。

## 编码 Spec

### 功能点

- [ ] 「可协作 Bot」Tab 搜索区新增「按名称筛选 / 按画像筛选」双模式切换，默认按名称；按名称模式行为与现状完全一致。
- [ ] 按画像模式：输入关键词经防抖（350ms，与现有名称搜索一致）后调用推荐链路，结果替换可协作列表；清空关键词恢复原列表。
- [ ] 「协作目标」输入框旁新增「智能推荐」按钮：目标为空时置灰；点击后以目标文本为关键词触发推荐，并自动切换展示「可协作 Bot」Tab。
- [ ] 推荐结果展示：仅含可协作 Bot（交集过滤）；在线优先、推荐分降序；每项展示画像摘要（short_profile）与在线状态；推荐态有明确退出出口（清除关键词或切回按名称模式）。
- [ ] 交集为空：展示空态提示，原列表保留，可退出推荐态回到原列表。
- [ ] 推荐服务失败：展示可重试错误提示，不阻塞发起协作主流程。
- [ ] 弹窗关闭重置：搜索模式回退按名称、搜索词与推荐态清空。
- [ ] 推荐查询身份与可协作列表查询身份同源（当前协作身份 activeIdentity），保证交集口径一致。

### 技术方案

推荐服务方法落在 `collaborationCandidateService`（候选数据层内聚），输入为当前候选全量列表（弹窗已加载），避免重复请求：

```text
recommendCandidates(input: {
  actorId, actorKind,        // 当前协作身份（与可协作列表查询同源）
  keyword,                   // 协作目标文本或画像关键词
  candidates,                // 弹窗已加载的可协作 Bot 全量列表
}) : DomainResult<RecommendCandidateView[]>
```

处理步骤：

1. 调用 `discoverPublicBots`：`keyword`、`top_k=20`、`min_score=0.01`、`viewer_actor_type`（bot/human 按 actorKind）、`viewer_actor_id`；Bot 视角不下发 `runtime_state`（沿用现有约定）；
2. 交集：discover 返回的 `bot_uuid` 与 `candidates[].id` 求交（两者同构，直接比对）；
3. 交集项映射回候选列表中的 `CollaborationBotView` 对象，附加 `recommendScore`、`shortProfile`；
4. 排序：`online` 降序 → `recommendScore` 降序；
5. 错误统一转 `DomainError`（现有 toDomainError 机制），返回 `DomainResult`。

### 关键方法抽象

| 抽象/方法 | 所在层 | 职责与边界 | 输入与输出 |
|---|---|---|---|
| `recommendCandidates` | 候选数据服务（collaborationCandidateService） | discover 调用、交集计算、推荐分/画像摘要附加、排序；不持有组件状态，不做防抖 | 身份 + 关键词 + 当前候选列表 → DomainResult<RecommendCandidateView[]> |
| picker hook 扩展（filterMode / 画像搜索分流 / 推荐态状态） | Hook（useGroupCollaborationPicker） | 模式状态、画像模式防抖分流到推荐服务、推荐结果与加载态管理、弹窗关闭重置；名称模式链路保持原样 | 复用 search 状态，按 filterMode 分流 |
| 推荐结果展示 | 组件（GroupParticipantPicker） | 模式切换 UI、推荐态列表渲染、画像摘要、空态与错误态；不发起请求 | 消费 picker 状态 |
| 智能推荐按钮 + 触发接线 | 组件（GroupConfigFields / CreateGroupModal） | 协作目标非空校验、按钮 disabled 态、触发推荐并切换到可协作 Tab | 表单回调 |

### 领域模型（新增类型）

```ts
/** 推荐 Bot 视图：可协作候选 Bot 附加推荐信息。 */
export interface RecommendCandidateView extends CollaborationBotView {
  /** discover 接口 recommendation.score */
  recommendScore: number;
  /** discover 接口 recommendation.short_profile，画像摘要 */
  shortProfile?: string;
}

/** 可协作 Bot Tab 搜索模式。 */
export type CandidateFilterMode = 'name' | 'profile';
```

### 分层改造点

| 层 | 改造点 |
|---|---|
| API Controller | N/A（复用现有 discoverPublicBots 封装，不修改） |
| Service | collaborationCandidateService 新增 recommendCandidates 方法 |
| Hook | useGroupCollaborationPicker 增加 filterMode 状态、画像模式分流、推荐结果与加载态 |
| Component | GroupParticipantPicker 模式切换与推荐态展示；GroupConfigFields 智能推荐按钮；CreateGroupModal 触发接线 |
| Store | N/A |

### 文件改动范围

| 文件路径 | 改动类型 | 说明 |
|---|---|---|
| `apps/frontend-nextgen/src/services/workspace/collaborationCandidateService.ts` | 修改 | 新增 RecommendCandidateView 类型与 recommendCandidates 方法 |
| `apps/frontend-nextgen/src/pages/Workspace/hooks/useGroupCollaborationPicker.ts` | 修改 | filterMode、画像搜索分流、推荐态状态与重置 |
| `apps/frontend-nextgen/src/pages/Workspace/components/Modals/GroupParticipantPicker.tsx` | 修改 | 模式切换 Segmented、推荐结果渲染（画像摘要、在线状态）、空态/错误态 |
| `apps/frontend-nextgen/src/pages/Workspace/components/Modals/GroupConfigFields.tsx` | 修改 | 协作目标输入框旁智能推荐按钮（disabled 联动目标非空） |
| `apps/frontend-nextgen/src/pages/Workspace/components/Modals/CreateGroupModal.tsx` | 修改 | 协作目标推荐触发与 Tab 切换接线 |
| 对应 `test/` 下测试文件 | 新增/修改 | 服务单测、hook 单测、组件测试 |

## 验收标准

- [ ] **AC-01 推荐入口可用性**：给定发起协作弹窗打开，当协作目标为空时，智能推荐按钮置灰不可点；输入目标后按钮可用。
- [ ] **AC-02 协作目标推荐结果**：给定已输入协作目标并点击智能推荐，当推荐链路返回非空交集时，则自动切换到「可协作 Bot」Tab 并展示推荐列表：仅含可协作 Bot，在线优先、推荐分降序，每项展示名称、简介、画像摘要与在线状态；推荐结果默认全部选中（含离线 Bot），用户手动取消过的 Bot 在再次推荐时不被自动勾选（用户取消优先），手动重新勾选即解除。
- [ ] **AC-03 交集为空兜底**：给定推荐交集为空，则展示「暂无与该目标相关的可协作 Bot」空态提示；清除关键词或切回按名称模式后恢复原可协作列表。
- [ ] **AC-04 推荐加载中**：给定推荐请求进行中，则列表区展示加载状态且按钮不可重复触发；请求结束后恢复。
- [ ] **AC-05 画像关键词搜索**：给定「可协作 Bot」Tab 且切换到按画像筛选模式，当输入关键词后（防抖 350ms），则展示语义推荐结果（排序规则同 AC-02，搜索结果不自动选中）；清空关键词恢复原列表；切回按名称模式时关键词沿用现有名称搜索行为。
- [ ] **AC-06 名称搜索不回归**：给定按名称筛选模式，则搜索、分页、本地过滤行为与现状完全一致。
- [ ] **AC-07 弹窗状态重置**：给定弹窗关闭后重新打开，则搜索模式回到按名称、搜索词与推荐态清空、列表为初始加载态。
- [ ] **AC-08 其他 Tab 不受影响**：给定好友 Bot 或已管理 Bot Tab，则现有列表与搜索行为不变。
- [ ] **AC-09 推荐失败降级**：给定 discover 接口调用失败，则展示可重试错误提示，原列表保留，发起协作主流程可正常继续。
- [ ] **AC-10 身份口径一致**：给定推荐链路执行，则 discover 的 viewer 身份与可协作列表查询身份为同一当前协作身份。

## Review Spec

### 关注点

- 交集口径：viewer 身份必须与可协作列表查询同源（activeIdentity），不得混用群主 Bot 或登录用户身份。
- 推荐态与原列表状态隔离：推荐结果不得覆盖或丢失原候选列表数据，退出推荐态必须可完整恢复。
- 推荐链路任何失败不得阻塞建群主流程（提交按钮、已选成员不受影响）。
- 名称搜索链路零改动：分流必须保证 name 模式不经过推荐代码路径。
- 弹窗重置覆盖推荐相关全部状态（filterMode、推荐结果、加载态）。
- 无障碍：模式切换 Segmented 与智能推荐按钮提供可读 aria 标签；推荐列表项的画像摘要对屏幕阅读器可读。

### 检查项

- [ ] recommendCandidates 纯函数语义（无组件状态、无副作用），交集与排序有单测覆盖（含空交集、单元素、离线优先级）。
- [ ] hook 分流逻辑有单测：name 模式调用 listCandidates、profile 模式调用 recommendCandidates、防抖与竞态（快速切换模式/关键词时旧请求结果不覆盖新状态）。
- [ ] 组件测试覆盖模式切换、推荐态渲染、空态、错误态、disabled 态。
- [ ] 既有 GroupParticipantPicker/useGroupCollaborationPicker 相关测试全绿，无断言语义改动。
- [ ] typecheck、lint、pre-commit 门禁全绿。

### 不可接受的模式

- 用语义搜索替换名称搜索，或按名称模式混入推荐调用。
- 推荐失败时吞掉错误并静默展示空列表。
- 推荐态数据覆盖原候选列表导致退出后无法恢复。
- 在推荐链路中混入埋点调用（本期明确不做）。
- 把 discover 响应中未约定的字段（如 friend_check_in_strategy）固化为领域模型字段。

## QA Spec

### 测试用例

| 编号 | 用例名称 | 操作步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | 目标为空按钮置灰 | 打开发起协作弹窗，不输入协作目标 | 智能推荐按钮 disabled |
| TC-02 | 协作目标推荐展示 | 输入与某可协作 Bot 画像相关的目标，点击智能推荐 | 自动切到可协作 Bot Tab；该 Bot 出现在推荐列表，附画像摘要与在线状态，在线项在前 |
| TC-03 | 推荐空态与恢复 | 输入无匹配目标触发推荐 | 空态提示展示；清除关键词后原列表恢复 |
| TC-04 | 画像关键词搜索 | 可协作 Bot Tab 切到按画像筛选，输入画像关键词 | 防抖后展示语义推荐结果；清空关键词恢复原列表 |
| TC-05 | 名称搜索回归 | 按名称模式输入 Bot 名称 | 与改动前行为一致；分页可用 |
| TC-06 | 模式切换状态 | 画像搜索后切回按名称模式 | 推荐态清除，关键词按名称搜索重新生效 |
| TC-07 | 弹窗关闭重置 | 推荐态中关闭弹窗再打开 | 模式回按名称、无残留推荐结果 |
| TC-08 | 推荐失败降级 | 断网或接口失败时触发推荐 | 错误提示展示，原列表与建群流程正常 |
| TC-09 | 好友/已管理 Tab 不受影响 | 分别操作两个 Tab 的列表与搜索 | 行为与现状一致 |

### 前置条件

- 预发或本地验证环境可用；可协作列表中存在画像与关键词可匹配的 Bot（画像相关词如「值班」对准值班类 Bot）。
- 空交集场景可用长句协作目标或无匹配关键词构造。

## Ship Spec

### 部署目标环境

- [ ] 预发环境

### 分支策略

- 开发分支：`dev_collab_member_recommend`（基于远端最新 dev，已创建）
- 同步策略：Avernet 开源仓开发完成后同步 OCB 内部仓同名分支，两仓 frontend-nextgen 同构。

### 回滚方案

- 纯前端改动，无数据迁移、无接口变更；回滚即 revert 本特性提交。

## 已知限制与后续建议

- 推荐命中率受可协作 Bot 画像数据质量影响：实测画像相关关键词可命中（如「值班」命中值班类 Bot），协作目标长句可能返回空交集（AC-03 兜底保障体验不劣化）。推荐质量根治需后端配合（discover 增加可协作过滤参数，或提升可协作 Bot 画像索引覆盖率），建议列为独立后续需求推进，不在本 Spec 范围。
- discover 接口按可见性过滤（含可申请好友的受保护 Bot），与可协作集合存在天然差异；本方案通过前端交集收敛，行为与老版本语义搜索的候选范围一致。

## 变更记录

| 日期 | 变更内容 |
|---|---|
| 2026-10-15 | 初稿：基于需求沟通结论与预发实测数据形成完整系分 Spec |
| 2026-10-16 | AC-02 补充推荐结果默认全选与取消优先规则；AC-05 明确画像搜索结果不自动选中 |