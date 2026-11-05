# 协作群头像与 Bot 信息修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接通 BCS Group/Session 与 `bots/mine` 的头像数据，并在内部 Bot 公开设置中提供单次协作信息修复入口。

**Architecture:** API 层区分成员摘要与详情，service 负责归一化和映射，Hook 负责请求生命周期，组件只负责展示。完整成员继续用于控制语义，新增摘要用于头像。手动修复独立使用 Backend Controller/service/Hook，严格按报告反馈结果，不与头像加载或协作开关写入混用。

**Tech Stack:** TypeScript、React 18、Zustand、Jest、Testing Library、Tailwind/shadcn、现有 `@dicebear/core` / `@dicebear/bottts`。

**Spec:** [spec.md](./spec.md)（实施前与本计划一起阅读）。

---

## 执行边界与约定

- 依据：[spec.md](./spec.md)，覆盖 AC-01 至 AC-35。本文件中的代码和命令是后续执行指引，本轮不实施业务代码。
- 编写日期：2026-11-05（Asia/Shanghai）；目录日期沿用关联 spec 的 2026-11-04。
- 遵循 [领域文档约定](../../../../docs/agents/domain.md)，计划与模块 spec 同目录；不另建第二份计划。
- 前端基线/合并目标：`origin/REL20261105`；本次 fetch 的最新提交为 `bf74bac9141751ddfb00c9773ac553c108c697c9`。文档分支为 `docs/rel20261105-collaboration-avatars`，从该基线 cherry-pick 文档提交 `19448431d5` 后更新。不向 dev 或 REL20261105 直接提交。
- 用户已确认 BCS 部署使用 dev，相关 BCS 与 Backend 接口均已部署。只按 spec §2.1 的 BCS 查询合同、§6.2 的 Backend 修复合同实施前端；不读取本分支服务端源码推导接口、不 cherry-pick 服务端变更、不安排服务端实现/迁移/发布任务。
- 原「通用配置」在本分支名为「公开设置」，组件名仍为 BotGeneralConfigDialog；修复说明已确认采用“名称、头像、归属、协作者等”。
- 本文 **Files 表内的路径均相对于 `apps/frontend-nextgen/`**。除 Task 0 和明确写出仓库根目录的命令外，命令均在该模块目录执行。每个新增源文件职责在下面固定，不建立通用头像服务或请求框架。
- 每个步骤完成后勾选；先运行新增测试观察行为失败，再修改生产代码。导入缺失可作为新模块的初始 RED；修改既有链路时须看到具体行为断言失败，不能把环境/依赖错误当作 RED。
- 每个任务提交前运行其聚焦测试和 `git diff --check`；提交仅包含本任务路径，不使用 `git add .`。不要提前声明全量 CI 或浏览器通过。

## 文件职责和执行顺序

| 单元 | 创建/修改的核心文件 | 职责 |
| --- | --- | --- |
| 头像值归一化 | 新建 `src/utils/avatarSource.ts` | 区分无头像、服务端字符串和可渲染图片来源，无状态、无 I/O |
| 展示成员 | 修改 `src/domain/collaboration/types.ts`；新建 `src/domain/collaboration/participantPresentation.ts` | 摘要类型、摘要选择和人数计算，不参与授权 |
| Wire DTO | 新建 `src/services/backendApi/collaboration/participantTypes.ts`；修改 Group/Session/Bot Controller | 分开列表摘要与详情，兼容 nullable/旧字段缺省 |
| 映射 | 修改 `src/services/workspace/mappers.ts`、`groupService.ts`、`groupExecuteService.ts`、`sessionService.ts` | 独立列表/详情映射，保留已有路由、身份和分页参数 |
| 图片渲染 | 新建 `src/components/Workspace/BotDisplayAvatar.tsx` | URL → 稳定默认图 → 本地图标；源变更重置失败状态 |
| 旧列表兼容 | 新建 `src/pages/Workspace/hooks/useGroupParticipantHydration.ts`；修改两个 Group workspace Hook | 有界补拉、按身份隔离缓存、丢弃旧列表请求 |
| 详情应用 | 修改 `src/pages/Workspace/hooks/useSessionMemberSync.ts`、`src/pages/Workspace/GroupWorkspaceArea.tsx` | 分别标识 Group/Session 完整详情；会话防止旧 pending detail 重放；管理面板在列表刷新后按需重拉群详情 |
| Group UI | 修改 GroupAvatarStack、GroupItem、CollapsedRail、GroupHeader | 消费摘要，保留尺寸、人数和交互 |
| 消息发送者 | 新建 `src/pages/Workspace/components/GroupChatPane/botSenderPresentation.ts`；修改 messageHelpers、GroupChatBubble、GroupChatMessageList | 身份/名称/头像分别解析，保留 Human、system 和匿名等待 |
| 身份选择框 | 修改 identityService、IdentitySelectorParts、InlineIdentityList；验证 Store 和 mapper | 复用 mine 数据，真实头像和固定默认图，不触发身份切换 |
| 修复后刷新 | 修改 `src/hooks/useCollaborationPrivacy.ts`；新增修复 Hook | 修复结果与读取错误分开；卸载/上下文变化时使所有旧读取失效，保护共享 Store |
| 配置分区 | 新建 `SettingRow.tsx`、`BotAbilitySettings.tsx` 和修复行 | SettingRow 供协作能力与发布审批共用，原 settingsSections 保持能力导出兼容 |

按 Task 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 7A → 7B → 7C → 8 执行。Task 3 是 Task 5–7 的共同前置；新增 Task 7A–7C 独立完成内部 Bot 修复能力，不要求头像路径全部完成才能聚焦验证。统一在 Task 8 集成验收，共 12 个任务。

## Task 0：确认基线、依赖和验证入口

**Files（只读）：**

- 仓库根 `AGENTS.md`、`CONTEXT-MAP.md`、`docs/agents/domain.md`。
- `package.json`、`package-lock.json`、`jest.config.cjs`、`scripts/ci-guardrails.mjs`。
- `specs/2026-11-04-collaboration-bot-avatars/spec.md`。
- 本目录 `spec.md` §2.1（BCS 已部署查询接口）和 §6.2（Backend 已部署修复接口）；无服务端源码读取步骤。

- [ ] **Step 1：使用 using-git-worktrees 技能建立实施工作区，并安装仓库 hooks。** 当前任务只写计划，不在这里提前执行。

在实施工作区的仓库根目录执行：

```bash
git status --short
scripts/install_git_hooks.sh
git fetch origin REL20261105
git log -1 --format='%H %s' origin/REL20261105
git merge-base origin/REL20261105 HEAD
```

预期：无被覆盖的用户改动；记录本次实际 REL20261105 基线。接口部署是已确认前提，不等待服务端代码出现在前端分支。若发布分支继续前进，只复核本计划涉及的前端文件，不混入 dev 服务端提交。后续若 push，使用 `AVERNET_PRE_PUSH_MERGE_TARGET=origin/REL20261105 git push`，不修改其他工作区的默认目标。

- [ ] **Step 2：检查 Node 和依赖。** `package.json` 的 engines 要求 Node ≥22，优先于 README 中较旧的 Node 20 说明。依赖已安装时直接复用；只有缺依赖时执行 `npm ci`，不引入私有 registry 配置。

```bash
cd apps/frontend-nextgen
node --version
npm --version
npm ls --depth=0
```

预期：Node 满足要求，项目依赖可解析。安装失败时记录命令和公开依赖缺口，不修改 lockfile 绕过。

- [ ] **Step 3：运行最接近的既有基线测试。**

```bash
npm test -- --runInBand --runTestsByPath \
  test/services/workspace/mappers.test.ts \
  test/services/workspace/identityStore.test.ts \
  test/pages/Workspace/hooks/useSessionMemberSync.test.ts
```

预期：全部 PASS。已有失败先记录和定位，不把旧失败归因于后续头像实现。

## 发布分支适配检查

- 已核对所有 Modify/Test/Existing 路径。变化集中在公开设置文案、GroupItem 创建入口、Session 创建参数、头像编辑暂存链路；头像生成器、成员 mapper、身份 selector 的关键缺口仍存在。
- Task 2 保留当前 `createNewSession(groupId, title?, contextQuery?, messageViewScope?)`，不增加 GroupContextDelivery/context_delivery，也不恢复已经删除的相关测试。
- Task 5 只替换 GroupItem 的展示数据和头像，保留 Human 的 MessageViewScopeMenuButton 与 Bot 的 IconButton；GroupItem 当前 279 行，GroupHeader 298 行。
- Task 7C 使用「公开设置」标题、加载/错误文案；保留 BotGeneralConfigDialog 名称，不重命名 API/组件来配合旧截图。
- 修复读取已经保存的信息；AvatarSettings 当前只暂存，BotBasicInfoDialog 的「保存修改」负责持久化，本计划不改这条链路。

## Task 1：建立展示类型、头像值归一化和 DTO 边界

**Files：**

- Create: `src/utils/avatarSource.ts`
- Create: `src/domain/collaboration/participantPresentation.ts`
- Create: `src/services/backendApi/collaboration/participantTypes.ts`
- Modify: `src/domain/collaboration/types.ts`
- Modify: `src/services/backendApi/collaboration/collaborationGroupController.ts`
- Modify: `src/services/backendApi/collaboration/sessionController.ts`
- Test/Create: `test/utils/avatarSource.test.ts`
- Test/Create: `test/domain/collaboration/participantPresentation.test.ts`

- [ ] **Step 1：添加纯行为测试。** 新测试完整导入如下 API；同时覆盖空字符串、null、对象、协议相对路径、根相对路径、data URI，以及空摘要优先于旧详情。

```ts
// test/utils/avatarSource.test.ts
import { normalizeAvatarUrl, imageAvatarSource } from '@/utils/avatarSource';

test.each([undefined, null, '', '  ', { name: '/wrong.png' }])(
  'non-string/empty avatar %p is absent', (value) => {
    expect(normalizeAvatarUrl(value)).toBeUndefined();
  },
);
test.each(['/bot.png', '//example.test/bot.png', 'https://example.test/bot.png',
  'http://example.test/bot.png', 'data:image/svg+xml,%3Csvg/%3E'])('%s is an image source', (src) => {
  expect(imageAvatarSource(` ${src} `)).toBe(src);
});
test.each(['B', 'javascript:alert(1)', 'data:text/html,hello'])('%s is not an image', (src) => {
  expect(imageAvatarSource(src)).toBeUndefined();
});
```

```ts
// test/domain/collaboration/participantPresentation.test.ts
import { getDisplayParticipants, getParticipantDisplayCount } from '@/domain/collaboration/participantPresentation';
import type { ParticipantView } from '@/domain/collaboration';

const old: ParticipantView = { actorId: 'b1', kind: 'bot', name: 'A',
  role: 'driver', mode: 'auto', avatarUrl: '/old.png' };
test('explicit empty summary clears stale detail display', () => {
  expect(getDisplayParticipants({ participants: [old], participantSummaries: [] })).toEqual([]);
});
test('legacy missing summary can display detail', () => {
  expect(getDisplayParticipants({ participants: [old] })).toEqual([old]);
});
test('zero is a valid count; absent count uses visible members', () => {
  expect(getParticipantDisplayCount(0, [old])).toBe(0);
  expect(getParticipantDisplayCount(undefined, [old])).toBe(1);
});
```

- [ ] **Step 2：运行 RED。**

```bash
npm test -- --runInBand --runTestsByPath test/utils/avatarSource.test.ts test/domain/collaboration/participantPresentation.test.ts
```

预期：新模块尚未实现导致 FAIL。

- [ ] **Step 3：实现无状态帮助函数与领域类型。**

```ts
// src/utils/avatarSource.ts
export function normalizeAvatarUrl(value: unknown): string | undefined {
  return typeof value === 'string' ? value.trim() || undefined : undefined;
}
export function imageAvatarSource(value: unknown): string | undefined {
  const src = normalizeAvatarUrl(value);
  return src && /^(?:https?:\/\/|\/|data:image\/)/i.test(src) ? src : undefined;
}
```

在 `types.ts` 增加以下类型；分别在 `GroupView`、`SessionView` 增加 `participantSummaries?: ParticipantSummaryView[]`。`ParticipantView` 的 role/mode 保持必需。另外，给 `GroupView`、`SessionView` 均增加内部加载标记 `participantDetailsLoaded?: boolean`：新列表为 false，完整详情为 true，旧手工构造的 view 缺省时沿用完整成员长度判断。Group 的头像兼容补拉只返回成员数组，不含完整群配置，不能将 Group 标记为 true。该标记解决「详情明确为空」和「详情未加载」不能只靠长度区分的问题，不进入 wire 或授权契约；Task 4 用它在管理面板打开期间补齐被新列表替换的群详情。

```ts
export interface ParticipantSummaryView {
  actorId: string;
  kind: 'human' | 'bot';
  name: string;
  avatarUrl?: string;
}
```

```ts
// src/domain/collaboration/participantPresentation.ts
import type { ParticipantSummaryView, ParticipantView } from './types';
export function getDisplayParticipants(value: {
  participants: ParticipantView[];
  participantSummaries?: ParticipantSummaryView[];
}): ParticipantSummaryView[] {
  return value.participantSummaries ?? value.participants;
}
export function getParticipantDisplayCount(
  count: number | undefined, participants: readonly ParticipantSummaryView[],
): number {
  return typeof count === 'number' && Number.isFinite(count) && count >= 0
    ? count : participants.length;
}
```

- [ ] **Step 4：声明轻量 wire 类型，并把列表 Controller 返回类型从详情分开。**

```ts
// src/services/backendApi/collaboration/participantTypes.ts
export interface ParticipantSummaryDto {
  actor_id: string;
  actor_kind: 'human' | 'bot';
  name: string | null;
  avatar_url: string | null;
}
// 新服务字段必需；只有消费旧响应时允许缺头像。
export type ParticipantSummaryReadDto = Omit<ParticipantSummaryDto, 'avatar_url'> & {
  avatar_url?: string | null;
};
```

`GroupParticipantDto`、`SessionParticipantDto` 分别增加 `avatar_url?: string | null`，其余必需控制字段保持原定义。在 `collaborationGroupController.ts` 声明下列列表类型，`listGroups` 的泛型改用它；接口路径和 options 不变。

```ts
export interface GroupSummaryData {
  group_id: string;
  version?: number;
  kind: 'normal' | 'dm';
  status: 'active' | 'dissolved';
  visibility: GroupVisibility;
  membership?: 'direct' | 'session_only';
  originator_actor_id: string;
  participant_count?: number; // 已部署接口必有；缺省仅兼容历史读取边界。
  driver_bot_uuid?: string;
  strategy?: GroupStrategy;
  name?: string;
  participants?: ParticipantSummaryReadDto[];
  created_at: number;
  updated_at: number;
}
// listGroups 的响应类型：
// BackendApiEnvelope<BackendApiPage<GroupSummaryData>>
```

在 `sessionController.ts` 导入 `ParticipantSummaryReadDto` 并声明下面类型；`listGroupSessions` 的 `items` 改为 `SessionSummaryData[]`，get/create/update 仍返回 `SessionDetailData`。

```ts
export type SessionSummaryData = Omit<SessionDetailData, 'participants'> & {
  participants?: ParticipantSummaryReadDto[];
};
```

- [ ] **Step 5：运行 GREEN、类型检查和提交。** 这一步类型报错若来自原 mapper 强制把列表当详情，不加断言绕过；与 Task 2 的必要映射签名一起完成后再提交边界改动。

```bash
npm test -- --runInBand --runTestsByPath test/utils/avatarSource.test.ts test/domain/collaboration/participantPresentation.test.ts
npm run typecheck
git diff --check
```

预期：两套纯函数测试 PASS；Task 1–2 边界最终类型检查 PASS。提交标题：`feat(frontend): distinguish avatar summaries from participant details`。

## Task 2：接通列表/详情映射并保留会话控制信息

**Files：**

- Modify: `src/services/workspace/mappers.ts`
- Modify: `src/services/workspace/groupService.ts`
- Modify: `src/services/workspace/groupExecuteService.ts`
- Modify: `src/services/workspace/sessionService.ts`
- Modify: `src/pages/Workspace/hooks/useSessionMemberSync.ts`
- Test/Modify: `test/services/workspace/mappers.test.ts`
- Test/Modify: `test/services/workspace/groupService.test.ts`
- Test/Modify: `test/services/workspace/sessionService.test.ts`
- Test/Modify: `test/pages/Workspace/hooks/useSessionMemberSync.test.ts`
- Test/Existing: `test/services/workspace/groupExecuteService.aceLogin.test.ts`
- Test/Existing: `test/services/workspace/sessionService.aceLogin.test.ts`

- [ ] **Step 1：在 mappers 测试添加摘要与详情的对照用例。**

```ts
import { mapSessionDetail, mapSessionListItem } from '@/services/workspace/mappers';

test('summary carries avatar without inventing control fields', () => {
  const dto = { session_id: 's1', group_id: 'g1', version: 1,
    status: 'running' as const, created_at: 1, updated_at: 2,
    participants: [{ actor_id: 'b1', actor_kind: 'bot' as const,
      name: null, avatar_url: ' /b1.png ' }] };
  const summary = mapSessionListItem(dto);
  expect(summary.participants).toEqual([]);
  expect(summary.participantSummaries).toEqual([
    { actorId: 'b1', kind: 'bot', name: 'b1', avatarUrl: '/b1.png' },
  ]);
  const detail = mapSessionDetail({ ...dto, participants: [{
    ...dto.participants[0], name: 'B1', role: 'worker', mode: 'muted',
    message_view_scope: 'full', avatar_url: null,
  }] });
  expect(detail.participants[0]).toMatchObject({ role: 'worker', mode: 'muted', messageViewScope: 'full' });
  expect(detail.participantSummaries?.[0].avatarUrl).toBeUndefined();
});
```

在既有 Group DTO fixture 上分别添加 `participants: []`、缺省 participants、带头像/空头像 Human/Bot；断言三者不混淆。Session-only Bot 必须出现在本 Session 的摘要中，不借 Group fixture 填充。

- [ ] **Step 2：运行 RED。**

```bash
npm test -- --runInBand --runTestsByPath test/services/workspace/mappers.test.ts
```

预期：新增映射 API 不存在或列表仍写入完整成员/丢头像的断言 FAIL。

- [ ] **Step 3：添加摘要 mapper、拆开 Session 列表与详情入口。** 在 `mappers.ts` 导入 Task 1 的类型、`normalizeAvatarUrl` 和 Controller 列表类型。

```ts
export function mapParticipantSummary(dto: {
  actor_id: string; actor_kind: 'human' | 'bot';
  name?: string | null; avatar_url?: string | null;
}): ParticipantSummaryView {
  return {
    actorId: dto.actor_id,
    kind: dto.actor_kind,
    name: dto.name?.trim() || dto.actor_id,
    avatarUrl: normalizeAvatarUrl(dto.avatar_url),
  };
}
export function toParticipantSummary(p: ParticipantView): ParticipantSummaryView {
  return { actorId: p.actorId, kind: p.kind, name: p.name, avatarUrl: p.avatarUrl };
}
export function mapSessionDetail(dto: SessionDetailData): SessionView {
  const participants = dto.participants.map(mapParticipant);
  return {
    ...mapSessionListItem(dto),
    participants,
    participantSummaries: participants.map(toParticipantSummary),
    participantDetailsLoaded: true,
  };
}
```

具体替换点：

```ts
// mapParticipant 入参添加 avatar_url?: string | null；返回对象添加：
avatarUrl: normalizeAvatarUrl(dto.avatar_url),
// 同一返回对象的 name 字段替换为，确保详情派生的摘要也不保留空白名称：
name: typeof dto.name === 'string' && dto.name.trim() ? dto.name.trim() : actorId,

// GroupListItemDto 改为 GroupSummaryData 的类型别名；mapGroupListItem 返回对象中：
participants: [],
participantDetailsLoaded: false,
...(Array.isArray(dto.participants)
  ? { participantSummaries: dto.participants.map(mapParticipantSummary) }
  : {}),

// GroupSessionDto 保留原有可选 kind/status 等字段；仅将 participants 类型改为：
// participants?: Array<ParticipantSummaryReadDto | SessionParticipantDto>;
// 这样 mapSessionDetail 可复用公共列表字段映射，且不要求详情的可选 name 变成必需。
// mapSessionListItem 返回对象中：
participants: [],
participantDetailsLoaded: false,
...(Array.isArray(dto.participants)
  ? { participantSummaries: dto.participants.map(mapParticipantSummary) }
  : {}),
```

保留 mapper 的时间、收藏、caller、createdBy、kind、membership 等已有字段逻辑。导入 `SessionParticipantDto` 和 `ParticipantSummaryReadDto`；不要把 GroupSessionDto 直接别名到有必需 version/status 的 Controller 类型，从而破坏现有读取边界。Group 列表计数在 mapper 边界直接完成回退：有效计数（含 0）保持，缺省/非法值取本响应摘要数量，不能先经 `safeNumber` 折成 0 再交给展示 helper。领域 `GroupView.participantCount` 保持必需 number，不扩大其他消费者的可选类型。已有「完整 Session 详情映射」测试调用改为 `mapSessionDetail`；列表的完整对象断言增加 `participantDetailsLoaded: false`，不删除原 mode/scope 保护断言。

计数映射的精确替换（从 Task 1 的领域 helper 导入 `getParticipantDisplayCount`）：

```ts
// mapGroupListItem 返回对象内替换原 participantCount: safeNumber(...)：
participantCount: getParticipantDisplayCount(
  dto.participant_count,
  Array.isArray(dto.participants) ? dto.participants.map(mapParticipantSummary) : [],
),
```

在现有 Group fixture 上添加 4 个摘要成员，分别令 `participant_count` 缺省、NaN、负数和显式 0，断言映射后依次为 4、4、4、0。Task 5 还要把缺省计数的真实 mapper 输出传给头像堆，断言「成员 4 人」和 `2+`，不能只测直接传给 helper 的 undefined。无计数且无任何成员资料时仍回退 0；后续完整详情按实际成员数更新。

- [ ] **Step 4：逐入口接线，避免列表/详情混用。**

| 调用位置 | 精确处理 |
| --- | --- |
| `sessionService.createNewSession`、`getSessionDetail` | `mapSessionListItem(resp.data!)` 替换为 `mapSessionDetail(resp.data!)` |
| `groupService.loadGroups` | 用 `GroupSummaryData` 消费列表，移除 `mapGroupListItem(g as never)` 中失效的类型断言 |
| `groupService.loadGroupDetail` | 完整成员用 `mapParticipant`，从同一数组派生 `participantSummaries`；设置 `participantDetailsLoaded: true`，包括空详情 |
| `groupService.loadGroupSessions`、`sessionService.loadSessionsByIds` | 保持列表 mapper |
| `groupExecuteService.loadBcsGroupDetail` | 既有 raw 成员映射添加头像；由映射后的完整成员派生摘要；完整群详情设置 `participantDetailsLoaded: true` |
| `mapBcsSessionItem` | raw 字段统一为身份字段后映射摘要；仅成员行确有合法 role/mode 时保留其完整成员，不把 V1 轻量行默认成 observer/auto |

raw 兼容数据增加 `avatar_url?: string | null`；保持既有 `bot_uuid/actor_id/bot_id` 的取值顺序及 ACE 登录体处理。raw 映射的新增片段：

```ts
// BcsParticipantRaw 增加上述字段；Group raw 成员返回对象中：
avatarUrl: normalizeAvatarUrl(p.avatar_url),

// raw Group 返回对象中，participants 是已有完整映射结果：
participantDetailsLoaded: true,
participantSummaries: participants.map(toParticipantSummary),

// 判断 raw Session 行是否确有完整控制信息，不能仅检查 participants.length：
const hasDetail = participants.length > 0 && participants.every((p) =>
  typeof p.role === 'string' && Object.prototype.hasOwnProperty.call(ROLE_NATIVE_TO_DOMAIN, p.role) &&
  isParticipantMode(p.mode),
);
```

实现 raw Session 时先构造统一的行数组，`actor_id/actor_kind/name/avatar_url` 供 `mapParticipantSummary`，role/mode 仅在 `hasDetail` 为真时供原完整成员映射，同时设置 `participantDetailsLoaded: hasDetail`；显式空数组仍产生空摘要，字段缺省不产生摘要。不要通过伪造 role/mode 来满足 TypeScript。

- [ ] **Step 5：补充并修正 Session 详情应用。** 在 `useSessionMemberSync.test.ts` 的真实 state harness 中添加：列表摘要已存在、详情迟到，仍应用完整 mode/scope 和新头像；明确空详情清空展示并标记已加载；列表重新加载后请求新详情，不能重放旧 pending detail；切换身份后的旧详情不能写回。

```ts
// useSessionMemberSync 内；新增 useWorkspaceStore 导入。
const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
const selected = Object.values(sessionMap).flat().find((s) => s.sessionId === selectedSessionId);
const detailLoaded = selected?.participantDetailsLoaded ??
  ((selected?.participants.length ?? participantsCount) > 0);
const selectedSummaries = selected?.participantSummaries;
const pendingDetailRef = useRef<SessionView | null>(null);

const applyDetail = useCallback((detail: SessionView) => {
  applyMapUpdate((cur) => {
    const list = [...(cur[detail.groupId] ?? [])];
    const idx = list.findIndex((s) => s.sessionId === detail.sessionId);
    const existing = list[idx];
    if (existing && (existing.participantDetailsLoaded ?? existing.participants.length > 0)) return cur;
    const updated = existing ? {
      ...existing, participants: detail.participants,
      participantSummaries: detail.participantSummaries, participantDetailsLoaded: true,
    } : { ...detail, participantDetailsLoaded: true };
    if (idx >= 0) list[idx] = updated;
    else list.push(updated);
    return { ...cur, [detail.groupId]: list };
  });
}, [applyMapUpdate]);

useEffect(() => {
  pendingDetailRef.current = null;
  if (!selectedSessionId || detailLoaded || isSessionKnownGone(selectedSessionId)) return;
  let cancelled = false;
  sessionService.getSessionDetail(selectedSessionId).then((res) => {
    if (cancelled || !res.ok || useWorkspaceStore.getState().activeIdentityId !== activeIdentityId) return;
    pendingDetailRef.current = res.data;
    applyDetail(res.data);
  });
  return () => { cancelled = true; };
}, [selectedSessionId, activeIdentityId, detailLoaded, selectedSummaries, applyDetail]);

useEffect(() => {
  if (detailLoaded) { pendingDetailRef.current = null; return; }
  const detail = pendingDetailRef.current;
  if (detail?.sessionId === selectedSessionId) applyDetail(detail);
}, [applyDetail, selectedSessionId, detailLoaded, sessionMap]);
```

替换原 applyDetail 和两个补齐 effect，保留后面的成员模式/可见域/成员更新动作。函数式 state updater 保持纯函数，不在其中写 ref。`selectedSummaries` 变化取消旧列表对应的在途详情；`detailLoaded` 使明确空详情不会无限重拉。`replaceSessionInMap` 已合并新字段且保护 favorite/ID，无需为头像重写它。

回归要求：已有完整成员不需要重复补齐，将原「已有 participants 仍请求但不覆盖」测试改为断言不请求且 state 中原 mode 保持；摘要非空但 `participantDetailsLoaded: false` 仍须请求。使用 StrictMode harness 复测空详情，避免副作用或重复 updater 导致丢更新。角色查询不得改读摘要。

- [ ] **Step 6：运行本任务聚焦测试、类型检查并提交。**

```bash
npm test -- --runInBand --runTestsByPath \
  test/services/workspace/mappers.test.ts \
  test/services/workspace/groupService.test.ts \
  test/services/workspace/sessionService.test.ts \
  test/pages/Workspace/hooks/useSessionMemberSync.test.ts \
  test/services/workspace/groupExecuteService.aceLogin.test.ts \
  test/services/workspace/sessionService.aceLogin.test.ts
npm run typecheck
git diff --check
```

预期：全部 PASS，列表摘要没有 role/mode，详情仍有真实控制信息。提交标题：`feat(frontend): preserve collaboration avatars through view mapping`。

## Task 3：实现共享 Bot 图片、稳定默认图和错误回退

**Files：**

- Create: `src/components/Workspace/BotDisplayAvatar.tsx`
- Test/Create: `test/components/Workspace/BotDisplayAvatar.test.tsx`
- Reuse: `src/services/botWorkshop/botAvatarGenerator.ts`

- [ ] **Step 1：编写浏览器图片错误与 props 更新测试。**

```tsx
/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';

test('failed source falls back by ID and a new URL is retried', () => {
  const { rerender } = render(<BotDisplayAvatar botId="b1" name="Alpha" avatarUrl="/old.png" />);
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', generateBotAvatar('b1'));
  rerender(<BotDisplayAvatar botId="b1" name="Renamed" avatarUrl="/new.png" />);
  expect(screen.getByRole('img', { name: 'Renamed' })).toHaveAttribute('src', '/new.png');
});
test('two image failures terminate in a local icon', () => {
  const { container } = render(<BotDisplayAvatar botId="b1" name="Alpha" avatarUrl="/bad.png" />);
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByRole('img', { name: 'Alpha' })).toBeInTheDocument();
});
```

另加无 URL 改名前后 `src === generateBotAvatar('b1')`，以及失败的 b1 切换到 b2 后使用 b2 的 URL；不 mock DiceBear，现有 Jest 配置已支持其 ESM。

- [ ] **Step 2：运行 RED。**

```bash
npm test -- --runInBand --runTestsByPath test/components/Workspace/BotDisplayAvatar.test.tsx
```

预期：共享组件不存在导致 FAIL。

- [ ] **Step 3：实现组件。** 失败计数仅属于内部、按 ID+源地址重新挂载的图片组件；名称改变不重置失败状态。

```tsx
import { useMemo, useState } from 'react';
import { Bot } from 'lucide-react';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import { imageAvatarSource } from '@/utils/avatarSource';

interface BotDisplayAvatarProps {
  botId: string;
  name: string;
  avatarUrl?: string;
  size?: number;
}

function AvatarImage({ botId, name, avatarUrl, size = 32 }: BotDisplayAvatarProps) {
  const fallback = useMemo(() => generateBotAvatar(botId), [botId]);
  const [attempt, setAttempt] = useState(0);
  const candidates = avatarUrl && avatarUrl !== fallback ? [avatarUrl, fallback] : [fallback];
  const src = candidates[attempt];
  const style = { width: size, height: size };
  if (!src) return (
    <span role="img" aria-label={name} style={style}
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
      <Bot aria-hidden="true" style={{ width: size * 0.6, height: size * 0.6 }} />
    </span>
  );
  return <img src={src} alt={name} style={style}
    className="shrink-0 rounded-full object-cover"
    onError={() => setAttempt((current) => current + 1)} />;
}

export function BotDisplayAvatar({ botId, avatarUrl, ...props }: BotDisplayAvatarProps) {
  const id = botId || 'bot';
  const src = imageAvatarSource(avatarUrl);
  return <AvatarImage key={JSON.stringify([id, src])} {...props} botId={id} avatarUrl={src} />;
}
```

- [ ] **Step 4：运行 GREEN 与文件门禁。**

```bash
npm test -- --runInBand --runTestsByPath test/components/Workspace/BotDisplayAvatar.test.tsx test/services/botWorkshop/botAvatarGenerator.test.ts
npm run ci:guardrails
git diff --check
```

预期：全部 PASS；没有新增依赖，也没有改通用 Human `Avatar` 的回退行为。

- [ ] **Step 5：提交。**

```bash
git add src/components/Workspace/BotDisplayAvatar.tsx test/components/Workspace/BotDisplayAvatar.test.tsx
git commit -m "feat(frontend): share stable bot avatar rendering"
```

## Task 4：新列表零头像补拉，旧列表请求有明确生命周期

**Files：**

- Create: `src/pages/Workspace/hooks/useGroupParticipantHydration.ts`
- Modify: `src/pages/Workspace/hooks/useGroupWorkspaceMutations.ts`
- Modify: `src/pages/Workspace/hooks/useGroupWorkspace.ts`
- Modify: `src/pages/Workspace/GroupWorkspaceArea.tsx`
- Test/Modify: `test/pages/Workspace/GroupWorkspaceAreaManagePanel.test.tsx`
- Test/Create: `test/pages/Workspace/hooks/useGroupParticipantHydration.test.tsx`
- Test/Modify: `test/pages/Workspace/hooks/useGroupWorkspace.test.ts`

- [ ] **Step 1：新增 Hook 测试，明确请求预算和迟到结果行为。** 在新测试中使用真实 `useState`，仅 mock `groupService`。

```tsx
/** @jest-environment jsdom */
import { useState } from 'react';
import { act, renderHook } from '@testing-library/react';
import { useGroupParticipantHydration } from '@/pages/Workspace/hooks/useGroupParticipantHydration';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { GroupView, ParticipantView } from '@/domain/collaboration';
import { scopeGroup } from '../../../mocks/collaborationScope';

jest.mock('@/services/workspace/groupService');
const load = jest.mocked(groupService.loadGroupParticipants);
function useHarness() {
  const [groups, setGroups] = useState<GroupView[]>([]);
  return { groups, setGroups, ...useGroupParticipantHydration(setGroups) };
}
beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.setState({ activeIdentityId: 'b1' });
});
test('fixed scope can capture a valid request without loading the directory', () => {
  const { result } = renderHook(useHarness);
  const first = result.current.captureRequest();
  expect(result.current.isCurrentRequest(first)).toBe(true);
  useWorkspaceStore.setState({ activeIdentityId: 'b2' });
  expect(result.current.isCurrentRequest(first)).toBe(false);
  const second = result.current.captureRequest();
  expect(second.identityId).toBe('b2');
  expect(result.current.isCurrentRequest(second)).toBe(true);
});
test('present empty summary never requests group detail', async () => {
  const { result } = renderHook(useHarness);
  const request = result.current.beginListLoad('b1');
  await act(async () => {
    await result.current.enrichParticipants([
      { ...scopeGroup, participantCount: 3, participantSummaries: [] },
    ], request, 'b1');
  });
  expect(load).not.toHaveBeenCalled();
});
test('late legacy hydration cannot replace a newer summary', async () => {
  let finish!: (members: ParticipantView[]) => void;
  load.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const { result } = renderHook(useHarness);
  const old = result.current.beginListLoad('b1');
  const pending = result.current.enrichParticipants([{ ...scopeGroup, participantCount: 1 }], old, 'b1');
  result.current.beginListLoad('b1');
  act(() => result.current.setGroups([{ ...scopeGroup, participantSummaries: [] }]));
  await act(async () => {
    finish([{ actorId: 'b1', kind: 'bot', name: 'old', role: 'worker', mode: 'auto', avatarUrl: '/old.png' }]);
    await pending;
  });
  expect(result.current.groups[0].participantSummaries).toEqual([]);
  expect(result.current.groups[0].participants).toEqual([]);
});
```

再在既有 `useGroupWorkspace.test.ts` 的 `gs.loadGroups` fixture 上添加非空新摘要，检查 `refreshGroups` 后头像清空时不命中旧 cache。保留旧响应缺字段的补拉测试，并新增两个身份都能看同一 groupId 时不能复用另一个身份缓存的用例。

补齐两个回归场景：

- 固定群：`renderHook(() => useGroupWorkspace(scopeGroup))`，不先调用 `refreshGroups`，直接 `reloadSelectedGroup(scopeGroup.groupId)`；返回新完整详情后，断言新成员/头像已应用、`loadGroups` 未调用。再覆盖固定群成员写入后的刷新，以及旧身份详情迟到不能写入新上下文。
- 管理面板：使用真实 `useGroupWorkspace` 与真实 state、只 mock service 和无关子组件。先加载完整详情并打开群管理，再刷新含新摘要的列表；断言摘要即时生效，管理详情按需重拉，返回后恢复 role/mode、owner 和群配置。显式空详情也标记已加载，不无限重拉；管理面板关闭时新列表仍无头像详情补拉。既有 `GroupWorkspaceAreaManagePanel.test.tsx` 保留面板打开时切群测试，并补上同群对象被新列表替换的编排断言。

- [ ] **Step 2：运行 RED。**

```bash
npm test -- --runInBand --runTestsByPath test/pages/Workspace/hooks/useGroupParticipantHydration.test.tsx test/pages/Workspace/hooks/useGroupWorkspace.test.ts
```

预期：新 Hook 未存在，且旧 Hook 对新空摘要仍有补拉行为。

- [ ] **Step 3：抽出头像成员补拉 Hook。** 以下实现取代 `useGroupWorkspaceMutations` 原有 cache/seed/enrich；管理和解散仍留在原文件。

```ts
import { useCallback, useRef, type Dispatch, type SetStateAction } from 'react';
import type { GroupView, ParticipantView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { toParticipantSummary } from '@/services/workspace/mappers';
import { useWorkspaceStore } from '@/stores/workspaceStore';

export interface GroupParticipantRequest { identityId: string | null; epoch: number }
export function useGroupParticipantHydration(setGroups: Dispatch<SetStateAction<GroupView[]>>) {
  const current = useRef<GroupParticipantRequest>({
    identityId: useWorkspaceStore.getState().activeIdentityId, epoch: 0,
  });
  const cache = useRef(new Map<string, ParticipantView[]>());
  const revisions = useRef(new Map<string, number>());
  // 固定群入口不加载目录；身份初始化/切换不能依赖 beginListLoad。
  const ensureIdentity = useCallback((identityId: string | null) => {
    if (current.current.identityId !== identityId) {
      cache.current.clear();
      revisions.current.clear();
      current.current = { identityId, epoch: current.current.epoch + 1 };
    }
    return current.current;
  }, []);
  const beginListLoad = useCallback((identityId: string | null) => {
    ensureIdentity(identityId);
    current.current = { identityId, epoch: current.current.epoch + 1 };
    return current.current;
  }, [ensureIdentity]);
  const captureRequest = useCallback(() =>
    ensureIdentity(useWorkspaceStore.getState().activeIdentityId), [ensureIdentity]);
  const isCurrentRequest = useCallback((request: GroupParticipantRequest) =>
    request === current.current && useWorkspaceStore.getState().activeIdentityId === request.identityId, []);
  const rememberParticipants = useCallback((groupId: string, members: ParticipantView[]) => {
    revisions.current.set(groupId, (revisions.current.get(groupId) ?? 0) + 1);
    cache.current.set(groupId, members);
  }, []);
  const forgetParticipants = useCallback((groupId: string) => {
    revisions.current.set(groupId, (revisions.current.get(groupId) ?? 0) + 1);
    cache.current.delete(groupId);
  }, []);
  const seedFromCache = useCallback((list: GroupView[], request: GroupParticipantRequest) => {
    if (!isCurrentRequest(request)) return list;
    return list.map((g) => {
      if (g.participantSummaries !== undefined) {
        forgetParticipants(g.groupId);
        return g;
      }
      const members = cache.current.get(g.groupId);
      return g.participants.length === 0 && members && members.length > 0
        ? { ...g, participants: members, participantSummaries: members.map(toParticipantSummary), participantDetailsLoaded: false } : g;
    });
  }, [forgetParticipants, isCurrentRequest]);
  const enrichParticipants = useCallback(async (
    list: GroupView[], request: GroupParticipantRequest, viewBotId?: string,
  ) => {
    const queue = list.filter((g) => g.participantSummaries === undefined &&
      g.participants.length === 0 && g.participantCount > 0 && !cache.current.has(g.groupId));
    const worker = async () => {
      while (isCurrentRequest(request)) {
        const group = queue.shift();
        if (!group) return;
        const revision = revisions.current.get(group.groupId) ?? 0;
        let members: ParticipantView[] | null = null;
        try { members = await groupService.loadGroupParticipants(group.groupId, viewBotId); }
        catch { members = null; }
        const stillCurrent = () => isCurrentRequest(request) &&
          (revisions.current.get(group.groupId) ?? 0) === revision;
        if (!stillCurrent()) continue;
        cache.current.set(group.groupId, members ?? []);
        if (!members?.length) continue;
        const loaded = members;
        setGroups((groups) => !stillCurrent() ? groups : groups.map((g) =>
          g.groupId === group.groupId && g.participantSummaries === undefined && g.participants.length === 0
            ? { ...g, participants: loaded, participantSummaries: loaded.map(toParticipantSummary), participantDetailsLoaded: false } : g));
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, queue.length) }, () => worker()));
  }, [isCurrentRequest, setGroups]);
  return { beginListLoad, captureRequest, isCurrentRequest, seedFromCache,
    enrichParticipants, rememberParticipants, forgetParticipants };
}
```

缓存属于 Hook 实例而非全局。`members === null` 保留现有「本次挂载不反复请求失败群」语义；不抛额外 Toast。新摘要路径无展示 cache fallback，空头像不是触发补拉的理由。完整管理详情被新列表失效后，只由 Step 5 的已打开管理面板按需补齐；这是管理数据请求，不是为头像逐群补拉。

- [ ] **Step 4：接入请求代次和管理操作。** `useGroupWorkspaceMutations` 实例化上面的 Hook，删除原重复实现并返回其方法；解散成功时调用 `forgetParticipants(groupId)`。重载详情前捕获 token，返回后确认仍有效，再写 cache 和 state：

```ts
// useGroupWorkspaceMutations 内
const hydration = useGroupParticipantHydration(setSessionGroups);
// reloadSelectedGroup：请求前
const request = hydration.captureRequest();
// 防止旧身份闭包在身份已切换后发起请求。
if (request.identityId !== activeIdentityId) return;
// await loadGroupDetailOrBcs 后、处理结果或更新 state 前
if (!hydration.isCurrentRequest(request)) return;
// 成功响应包括空成员也必须记入缓存；删除原 length > 0 条件。
hydration.rememberParticipants(gid, res.data.participants);
```

Hook 回调依赖使用解构后的稳定方法，不能把每次创建的 `hydration` 返回对象放入依赖数组。在 `useGroupWorkspace.loadGroups` 开始任何 await 前生成 token；原有 identity/search/membership 判断仍保留。

```ts
const request = beginListLoad(identityId);
// isCurrentList 的返回表达式增加：isCurrentRequest(request) && 原有三个条件。
// 成功后：
const seeded = seedFromCache(res.data, request);
setSessionGroups(seeded);
void enrichParticipants(seeded, request, identity.kind === 'bot' ? identity.id : undefined);
```

相应更新解构和依赖列表，删除「列表从不带成员」的过时注释。新 helper Hook ≤250 行；主 Hook 当前 234 行，不把补拉实现重新内联进去。

- [ ] **Step 5：管理面板打开时重新补齐被列表失效的完整群详情。** 不把摘要当角色/权限，不从旧缓存恢复被清空的头像。`GroupWorkspaceArea.tsx` 原有「面板打开/切群补详情」effect 替换为下段；`selectedGroup` 已在上方解构，使用现有 React namespace：

```tsx
const lastManagedGroupRef = React.useRef<string | null>(null);
React.useEffect(() => {
  if (activePanel !== 'manage' || !selectedGroupId) {
    lastManagedGroupRef.current = null;
    return;
  }
  const entering = lastManagedGroupRef.current !== selectedGroupId;
  lastManagedGroupRef.current = selectedGroupId;
  const detailLoaded = selectedGroup?.participantDetailsLoaded ??
    ((selectedGroup?.participants.length ?? 0) > 0);
  // 打开/切群仍刷新；同群新列表对象（false）也刷新；详情回填（true）不再请求。
  if (entering || !detailLoaded) void reloadSelectedGroup(selectedGroupId);
}, [activePanel, selectedGroupId, selectedGroup, reloadSelectedGroup]);
```

只替换原 effect，不能同时保留旧 effect 而重复请求。依赖选中群对象是为了让“同群列表再次刷新、加载标记仍为 false”也能触发新代次详情；完整空详情为 true，因此回填后停止。失败不改群对象，不自动循环重试，沿用现有错误提示与重新打开入口。请求在列表切换后失效时，新列表对象会驱动当前管理详情重拉。未打开管理面板不发起此请求，保留 AC-02 的头像请求预算。该宿主基线 273 行，此处原位替换后仍须通过 Component 300 行门禁，不另建管理请求框架。

- [ ] **Step 6：运行 GREEN、门禁并提交。**

```bash
npm test -- --runInBand --runTestsByPath test/pages/Workspace/hooks/useGroupParticipantHydration.test.tsx test/pages/Workspace/hooks/useGroupWorkspace.test.ts test/pages/Workspace/GroupWorkspaceAreaManagePanel.test.tsx
npm run ci:guardrails
git diff --check
```

预期：新响应零头像补拉；旧响应最多 4 并发、同身份缓存可复用；旧身份/旧搜索/旧列表请求不能覆盖当前数据；固定群不加载目录也能刷新详情；已打开管理面板不会因列表刷新永久停留在加载态。提交标题：`fix(frontend): scope legacy avatar hydration to current group lists`。

## Task 5：侧栏、收起导航和 Group 顶栏使用同一头像堆

**Files：**

- Modify: `src/pages/Workspace/components/GroupSidebar/GroupAvatarStack.tsx`
- Modify: `src/pages/Workspace/components/GroupSidebar/GroupItem.tsx`
- Modify: `src/pages/Workspace/components/GroupSidebar/GroupSidebarCollapsedRail.tsx`
- Modify: `src/pages/Workspace/components/GroupHeader.tsx`
- Test/Create: `test/pages/Workspace/components/GroupSidebar/GroupAvatarStack.test.tsx`
- Test/Modify: `test/pages/Workspace/components/GroupHeader.test.tsx`
- Test/Existing: `test/pages/Workspace/components/GroupSidebar/GroupSidebar.groupListAndView.test.tsx`

- [ ] **Step 1：编写缩略图与人数测试。**

```tsx
/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { GroupAvatarStack } from '@/pages/Workspace/components/GroupSidebar/GroupAvatarStack';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';

test('summary avatars render in stored order with the remaining count', () => {
  render(<GroupAvatarStack participants={[
    { actorId: 'b1', kind: 'bot', name: 'Alpha', avatarUrl: '/alpha.png' },
    { actorId: 'b2', kind: 'bot', name: 'Beta' },
  ]} totalCount={4} size={20} />);
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', '/alpha.png');
  expect(screen.getByRole('img', { name: 'Beta' })).toHaveAttribute('src', generateBotAvatar('b2'));
  expect(screen.getByText('2+')).toBeInTheDocument();
  expect(screen.getByLabelText('成员 4 人')).toBeInTheDocument();
});
```

在 GroupHeader fixture 增加 `participants: []` 和 `participantSummaries`，验证摘要驱动头像及 Human/Bot 计数，但选中会话的加入状态仍依据完整 `participants`。另用 Task 2 的真实 `mapGroupListItem` 处理缺省计数且有 4 名摘要成员的 DTO，再渲染 GroupAvatarStack，断言人数为 4、剩余为 `2+`；显式 0 保持 0，覆盖映射到展示的完整链路。

- [ ] **Step 2：运行 RED。**

```bash
npm test -- --runInBand --runTestsByPath test/pages/Workspace/components/GroupSidebar/GroupAvatarStack.test.tsx test/pages/Workspace/components/GroupHeader.test.tsx
```

预期：根相对 URL 被过滤、无头像使用首字或顶栏不读摘要的断言 FAIL。

- [ ] **Step 3：替换 GroupAvatarStack 的 Bot 分支。** props 类型改为 `ParticipantSummaryView[]`；删除本地 `isHttpUrl`，保留叠放、尺寸、剩余人数和辅助文本。

```tsx
{participant.kind === 'bot' ? (
  <BotDisplayAvatar botId={participant.actorId} name={participant.name}
    avatarUrl={participant.avatarUrl} size={size} />
) : (
  <Avatar name={participant.name} src={imageAvatarSource(participant.avatarUrl)} size={size} />
)}
```

- [ ] **Step 4：更新三个展示入口。** GroupItem 的总数不能再使用 `count > 0` 覆盖合法的零；GroupHeader 的同类 `||` 也替换。

```tsx
// GroupItem / GroupHeader：分别用 group / selectedGroup 作输入
const displayParticipants = getDisplayParticipants(group);
const stackTotalCount = getParticipantDisplayCount(group.participantCount, displayParticipants);
// GroupItem JSX
<GroupAvatarStack participants={displayParticipants} totalCount={stackTotalCount} size={20} />

// GroupHeader：替换既有 participants / totalCount 声明，保持 humans/bots 计数代码。
const participants = selectedGroup ? getDisplayParticipants(selectedGroup) : [];
const totalCount = getParticipantDisplayCount(selectedGroup?.participantCount, participants);

// CollapsedRail 的 groups.map 内生成 displayParticipants / totalCount；替换原固定群图标：
{displayParticipants.length > 0 ? (
  <GroupAvatarStack participants={displayParticipants} totalCount={totalCount} size={16} />
) : (
  <AvatarTile label={group.name} className="h-7 w-7 rounded-full"
    fallbackContent={<Users className="h-3.5 w-3.5" aria-hidden="true" />} />
)}
```

导入 Task 1 帮助函数、Task 3 组件和 `imageAvatarSource`。保留 GroupHeader 中判断 `selectedSession.participants` 的权限/加载文案；不能全文件替换所有 participants。GroupHeader 基线 298 行，使用原声明位置替换并删除被更改语义的过时注释；若超过 300 行，提取此次新增的展示计算，不能扩大 allowlist。

- [ ] **Step 5：运行 GREEN、交互回归与提交。**

```bash
npm test -- --runInBand --runTestsByPath \
  test/pages/Workspace/components/GroupSidebar/GroupAvatarStack.test.tsx \
  test/pages/Workspace/components/GroupHeader.test.tsx \
  test/pages/Workspace/components/GroupSidebar/GroupSidebar.groupListAndView.test.tsx
npm run ci:guardrails
git diff --check
```

预期：全部 PASS；收起入口仍能选择群，展开行仍只展开/收起会话，不改变主舞台选择语义。提交标题：`feat(frontend): render collaboration group thumbnail avatars`。

## Task 6：按发送者 ID 解析消息头像，名称不再短路查找

**Files：**

- Create: `src/pages/Workspace/components/GroupChatPane/botSenderPresentation.ts`
- Modify: `src/pages/Workspace/components/GroupChatPane/messageHelpers.tsx`
- Modify: `src/pages/Workspace/components/GroupChatPane/GroupChatBubble.tsx`
- Modify: `src/pages/Workspace/components/GroupChatPane/GroupChatMessageList.tsx`
- Test/Create: `test/pages/Workspace/components/GroupChatPane/botSenderPresentation.test.ts`
- Test/Modify: `test/pages/Workspace/components/GroupChatPane/GroupChatBubble.identity.test.tsx`
- Test/Existing: `test/pages/Workspace/components/GroupChatPane/messageHelpers.test.ts`
- Test/Existing: `test/pages/Workspace/components/GroupChatPane/GroupChatBubble.progress.test.tsx`

- [ ] **Step 1：测试优先级、显式清空和 Bot ID 冲突。**

```ts
import { resolveBotSenderPresentation } from '@/pages/Workspace/components/GroupChatPane/botSenderPresentation';
import type { ParticipantSummaryView } from '@/domain/collaboration';

const bot: ParticipantSummaryView = { actorId: 'b1', kind: 'bot', name: 'Member name', avatarUrl: '/member.png' };
test.each(['senderName', 'botName'])('%s does not bypass member avatar', (field) => {
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderId: 'b1', [field]: 'Wire name' } }, [bot], []))
    .toEqual({ botId: 'b1', name: 'Wire name', avatarUrl: '/member.png' });
});
test('session explicit absence does not reuse group or message old URL', () => {
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderId: 'b1', senderAvatarUrl: '/old.png' } },
    [{ ...bot, avatarUrl: undefined }], [bot])?.avatarUrl).toBeUndefined();
});
test('botUuid is canonical when senderId conflicts', () => {
  expect(resolveBotSenderPresentation({ status: 'done', extra: { botUuid: 'missing', senderId: 'b1' } }, [bot], []))
    .toMatchObject({ botId: 'missing', avatarUrl: undefined });
});
test('anonymous pending placeholder stays anonymous', () => {
  expect(resolveBotSenderPresentation({ status: 'pending', extra: {} }, [bot], [])).toBeUndefined();
});
```

补充同名不同 ID、Session-only、只有 Group 成员、无任何成员但有消息头像、完全无 ID 的场景；预期分别按 ID 精确匹配或使用固定 seed `bot`，不可按名称查询。

- [ ] **Step 2：运行 RED。**

```bash
npm test -- --runInBand --runTestsByPath test/pages/Workspace/components/GroupChatPane/botSenderPresentation.test.ts
```

预期：新解析函数未实现而 FAIL。

- [ ] **Step 3：实现纯解析器。**

```ts
import type { ChatMessage } from '@tc-chat/core';
import type { ParticipantSummaryView } from '@/domain/collaboration';
import { normalizeAvatarUrl } from '@/utils/avatarSource';

export interface BotSenderPresentation { botId: string; name: string; avatarUrl?: string }
function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value.trim() || undefined : undefined;
}
export function resolveBotSenderPresentation(
  message: Pick<ChatMessage, 'extra' | 'status'>,
  session: readonly ParticipantSummaryView[],
  group: readonly ParticipantSummaryView[],
): BotSenderPresentation | undefined {
  const senderId = text(message.extra?.senderId);
  const botUuid = text(message.extra?.botUuid);
  const senderName = text(message.extra?.senderName);
  const botName = text(message.extra?.botName);
  if (message.status === 'pending' && !senderId && !botUuid && !senderName && !botName) return undefined;
  const id = botUuid ?? senderId;
  const match = (rows: readonly ParticipantSummaryView[]) => id
    ? rows.find((p) => p.kind === 'bot' && p.actorId === id) : undefined;
  const participant = match(session) ?? match(group);
  const readable = (name: string | undefined) => name !== senderId && name !== botUuid ? name : undefined;
  return {
    botId: id ?? 'bot',
    name: readable(senderName) ?? readable(botName) ?? participant?.name ?? botName ?? id ?? 'Bot',
    avatarUrl: participant
      ? participant.avatarUrl : normalizeAvatarUrl(message.extra?.senderAvatarUrl),
  };
}
```

- [ ] **Step 4：接入消息渲染，保持 Human 分支和 run status 的完整成员。** `resolveSender` 的 session 入参类型改为 `ParticipantSummaryView[]`，内部 participantSender 的参数同样改为展示类型；它只读展示字段，无需 role/mode。

在 assistant 分支替换原 `wsSenderName` 提前返回、`knownBotName` 提前返回以及后面的 ID 查找循环：

```tsx
const sender = resolveBotSenderPresentation(
  message, sessionParticipants ?? [], group ? getDisplayParticipants(group) : [],
);
return sender ? {
  name: sender.name,
  avatar: <BotDisplayAvatar botId={sender.botId} name={sender.name} avatarUrl={sender.avatarUrl} size={32} />,
} : undefined;
```

`GroupChatBubbleProps` 增加可选 `displayParticipants?: ParticipantSummaryView[]`，调用 `resolveSender` 时用 `displayParticipants ?? participants`。`GroupChatMessageList` 的既有 bubble JSX 增加：

```tsx
displayParticipants={getDisplayParticipants(session)}
```

继续把完整 `participants` 传给 `GroupChatRunStatus`。Human 分支原候选池保留当前 Session/Group 的用户资料策略，不因为 Bot 解析重排当前登录用户头像优先级。移除本次替换后无用的局部变量，保留其他消息块、时间、流式状态逻辑。

- [ ] **Step 5：加一条真实渲染断言并运行 GREEN。** 在 `GroupChatBubble.identity.test.tsx` 的既有 fixture 中构造带 `botName` 的历史消息、带 `senderName` 的实时消息，匹配 Session 摘要 `/b1.png`；断言 DOM 的 `img.src`，不能只 mock 返回一个头像节点。

```bash
npm test -- --runInBand --runTestsByPath \
  test/pages/Workspace/components/GroupChatPane/botSenderPresentation.test.ts \
  test/pages/Workspace/components/GroupChatPane/GroupChatBubble.identity.test.tsx \
  test/pages/Workspace/components/GroupChatPane/messageHelpers.test.ts \
  test/pages/Workspace/components/GroupChatPane/GroupChatBubble.progress.test.tsx
git diff --check
```

预期：头像按 ID 正确；Human 对齐和头像、system 行、pending 匿名、并发流式动画全部不回归。提交标题：`fix(frontend): resolve group message avatars by bot identity`。

## Task 7：mine 头像进入所有身份选择框布局

**Files：**

- Modify: `src/services/backendApi/collaboration/collaborationBotController.ts`
- Modify: `src/services/workspace/identityService.ts`
- Modify: `src/services/workspace/collaborationCandidateService.ts`
- Modify: `src/services/botWorkshop/externalBotService.ts`
- Modify: `src/components/Workspace/IdentitySelector/IdentitySelectorParts.tsx`
- Modify: `src/components/Workspace/IdentitySelector/IdentityTriggerButton.tsx`
- Modify: `src/components/Workspace/IdentitySelector/InlineIdentityList.tsx`
- Test/Modify: `test/services/workspace/identityService.test.ts`
- Test/Modify: `test/services/workspace/identityStore.test.ts`
- Test/Modify: `test/hooks/workspaceIdentityMapper.test.ts`
- Test/Create: `test/components/Workspace/IdentityAvatar.test.tsx`
- Test/Modify: `test/components/Workspace/IdentitySelector.layout.test.tsx`
- Test/Modify: `test/components/Workspace/InlineIdentityList.test.tsx`
- Test/Existing: `test/components/Workspace/IdentitySelector.display.test.tsx`
- Test/Existing: `test/pages/Workspace/components/CollaborationIdentityHeader.test.tsx`

- [ ] **Step 1：先锁定 mine 读取、清空和身份保留行为。** 在已有 `identityService.test.ts` 追加下面用例，复用其 `listMyBots` mock、`SuccessResult` 和 beforeEach；现有引擎补充失败/并发请求复用用例继续运行。

```ts
it.each([null, undefined, '', '  ', { name: '/wrong.png' }])(
  'does not reinterpret missing or malformed mine avatar %p', async (avatar_url) => {
    listMyBots.mockResolvedValue({ code: 20000, message: '', request_id: 'avatar',
      data: { items: [{ kind: 'bot', bot_id: 'b1', name: 'Alpha', avatar_url }],
        total: 1, offset: 0, limit: 50 } });
    const result = await identityService.loadIdentities();
    expect(result.ok).toBe(true);
    expect((result as SuccessResult).data.identities.find((i) => i.id === 'b1')?.avatarUrl).toBeUndefined();
    expect(listMyBots).toHaveBeenCalledTimes(1);
  },
);
```

在 `identityStore.test.ts` 中添加：

```ts
it('avatar-only refresh preserves identity, group and session selection', () => {
  const bot = { id: 'b1', kind: 'bot' as const, displayName: 'Alpha', online: true };
  useWorkspaceStore.setState({ identities: [{ ...bot, avatarUrl: '/old.png' }],
    activeIdentityId: 'b1', selectedGroupId: 'g1', selectedSessionId: 's1' });
  for (const avatarUrl of ['/new.png', undefined]) {
    applyIdentityLoadResult({ identities: [{ ...bot, avatarUrl }], defaultActiveId: 'me' });
    expect(useWorkspaceStore.getState()).toMatchObject({ activeIdentityId: 'b1',
      selectedGroupId: 'g1', selectedSessionId: 's1' });
    expect(useWorkspaceStore.getState().identities[0].avatarUrl).toBe(avatarUrl);
  }
});
```

在 mapper 测试中传入同一 `IdentityView.id`、不同 displayName，并验证 ID 原样保留、有效 URL 原样透传；默认图由组件负责，mapper 不写生成图回 Store。

- [ ] **Step 2：添加共享 IdentityAvatar 的真实渲染测试并运行 RED。**

```tsx
/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { IdentityAvatar } from '@/components/Workspace/IdentitySelector/IdentitySelectorParts';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import type { Identity } from '@/services/workspace/workspaceModel';

const bot: Identity = { id: 'b1', kind: 'bot', name: 'Alpha', avatar: 'A' };
test('legacy initial becomes the same fixed Bot default', () => {
  render(<IdentityAvatar identity={bot} />);
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', generateBotAvatar('b1'));
});
test('avatar URL changes clear a previous image error', () => {
  const { rerender } = render(<IdentityAvatar identity={{ ...bot, avatar: '/old.png' }} />);
  fireEvent.error(screen.getByRole('img', { name: 'Alpha' }));
  rerender(<IdentityAvatar identity={{ ...bot, avatar: '/new.png' }} />);
  expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', '/new.png');
});
test('Human uses the caller-provided avatar', () => {
  render(<IdentityAvatar identity={{ id: 'u1', kind: 'user', name: 'User', avatar: '' }}
    userAvatarUrl="https://example.test/user.png" />);
  expect(screen.getByRole('img', { name: 'User' })).toHaveAttribute('src', 'https://example.test/user.png');
});
```

```bash
npm test -- --runInBand --runTestsByPath test/services/workspace/identityService.test.ts test/components/Workspace/IdentityAvatar.test.tsx
```

预期：对象被误当头像、默认首字和相对 URL 过滤等新断言 FAIL。Store 已支持头像差异比较的测试可以直接 PASS，不为制造 RED 改坏它。

- [ ] **Step 3：修正 DTO 和字符串边界，处理共享 DTO 的两个透传点。**

```ts
// collaborationBotController.ts，CollaborationBotDto：
avatar_url?: string | null;

// identityService.ts，导入 normalizeAvatarUrl 后替换对应字段：
avatarUrl: normalizeAvatarUrl(b.avatar_url),

// collaborationCandidateService.ts 的 mapBot，以及 externalBotService.ts 的 mapExternalBot：
avatarUrl: normalizeAvatarUrl(dto.avatar_url),
```

后两个文件只处理共享 DTO 的 nullable 类型传播，不引入其他页面行为改造。`identityStore.ts` 和 `workspaceIdentityMapper.ts` 现有逻辑若通过新测试无需修改。`enrichBotMetadata` 保持只补 engine/botType，不能读取补充接口的头像。

- [ ] **Step 4：统一 IdentityAvatar 和 inline 图片入口。** `IdentitySelectorParts.tsx` 导入 `BotDisplayAvatar`；给 `size` 联合类型增加数值 `20`，用于保留 inline 原尺寸；保留其余尺寸映射。

```tsx
// size 类型：'xxs' | 'xs' | 'sm' | 'md' | 'lg' | 20
const avatarSize = size === 20 ? 20 : size === 'xxs' ? 16 : size === 'xs' ? 24
  : size === 'sm' ? 32 : size === 'lg' ? 48 : 36;
return (
  <span className={cn('shrink-0', className)}>
    {identity.kind === 'bot' ? (
      <BotDisplayAvatar botId={identity.id} name={identity.name} avatarUrl={identity.avatar} size={avatarSize} />
    ) : (
      <Avatar name={identity.name}
        src={userAvatarUrl && isAvatarUrl(userAvatarUrl) ? userAvatarUrl : undefined}
        size={avatarSize} />
    )}
  </span>
);
```

`InlineIdentityList.tsx` 删除其 Bot/Human URL helper 与直接 Avatar 引用，导入共享 IdentityAvatar；原行中替换为：

```tsx
<IdentityAvatar identity={identity} size={20} userAvatarUrl={userAvatarUrl} />
```

搜索、按钮、在线点、分组及选中标识保留。Human 的 HTTP 过滤规则仍由原有分支承担，避免此任务顺带改变用户头像政策。

- [ ] **Step 5：补上收起导航的旁路。** `IdentityTriggerButton.tsx` 的 `collapsedLayout` 当前对 Bot 直接渲染名称首字，必须把整个 user/bot 分支改成共享组件：

```tsx
// collapsedLayout ? 后面的 JSX：
<IdentityAvatar identity={identity} size="xs" userAvatarUrl={userAvatarUrl} />
```

在已有 layout 测试中对六种布局逐一断言 Bot 图片：

```tsx
it.each(['default', 'sidebar', 'collapsed', 'collaboration', 'sidebarChip', 'inline'] as const)(
  '%s trigger displays the Bot source', (layout) => {
    render(<WorkspaceIdentitySelector layout={layout}
      identities={[{ id: 'b1', kind: 'bot', name: 'Alpha', avatar: '/b1.png' }]}
      activeId="b1" onChange={jest.fn()} />);
    expect(screen.getByRole('img', { name: 'Alpha' })).toHaveAttribute('src', '/b1.png');
  },
);
```

复用该测试文件现有 imports。对普通弹层和 `InlineIdentityList` 分别验证候选项，不只验证触发按钮；`CollaborationIdentityHeader` 的 readOnly 模式已用 IdentityAvatar，应在现有测试中验证其实际图片结果。

- [ ] **Step 6：运行 GREEN、类型检查和回归后提交。**

```bash
npm test -- --runInBand --runTestsByPath \
  test/services/workspace/identityService.test.ts \
  test/services/workspace/identityStore.test.ts \
  test/hooks/workspaceIdentityMapper.test.ts \
  test/components/Workspace/IdentityAvatar.test.tsx \
  test/components/Workspace/IdentitySelector.layout.test.tsx \
  test/components/Workspace/IdentitySelector.display.test.tsx \
  test/components/Workspace/InlineIdentityList.test.tsx \
  test/pages/Workspace/components/CollaborationIdentityHeader.test.tsx \
  test/services/workspace/collaborationCandidateService.test.ts \
  test/services/workspace/botAvatarPropagation.test.tsx
npm run typecheck
git diff --check
```

预期：全部 PASS；mine 请求预算不增加，Bot 清空头像后不复活旧图，更新头像不改变 activeIdentityId/selectedGroupId/selectedSessionId。提交标题：`feat(frontend): render mine avatars in identity selectors`。

## Task 7A：前端 Backend API 调用封装与修复报告边界

**Files：**

- Create: `src/services/backendApi/bots/botCollaborationRepairController.ts`
- Create: `src/services/botWorkshop/collaborationRepairReport.ts`
- Create: `src/services/botWorkshop/botCollaborationRepairService.ts`
- Test/Create: `test/services/backendApi/bots/botCollaborationRepairController.test.ts`
- Test/Create: `test/services/botWorkshop/botCollaborationRepairService.test.ts`
- Read: 本目录 `spec.md` §6.2 的已部署 HTTP 接口合同；本任务全部代码均位于前端 `src/services/`，不涉及服务端实现。

- [ ] **Step 1：写无 body 和报告分类的 RED 测试。** Controller mock `backendRequest`，验证 URL 编码、只用 Backend ID、完全没有 data/rawBody；service 覆盖三种整体状态、unknown、not_applicable、重复/漏项/乱序/非法状态/不一致成功报告。

```ts
import { backendRequest } from '@/services/backendApi/httpClient';
import { repairBotCollaboration } from '@/services/backendApi/bots/botCollaborationRepairController';
jest.mock('@/services/backendApi/httpClient');
test('repair is one empty-body POST', async () => {
  jest.mocked(backendRequest).mockResolvedValue({ code: 200000, data: null });
  await repairBotCollaboration('bot:a/b', 'u1');
  const [url, options] = jest.mocked(backendRequest).mock.calls[0];
  expect(url).toBe('/openapi/v1/bots/bot%3Aa%2Fb/collaboration/repair');
  expect(options).toMatchObject({ method: 'POST', retryOnTransient: false });
  expect(options).not.toHaveProperty('data');
  expect(options).not.toHaveProperty('rawBody');
});
```

```bash
npm test -- --runInBand --runTestsByPath test/services/backendApi/bots/botCollaborationRepairController.test.ts test/services/botWorkshop/botCollaborationRepairService.test.ts
```

预期：新模块缺失或合同断言失败；不要用 HTTP 200 作为成功断言。

- [ ] **Step 2：定义 wire 报告和空请求 Controller。** ownerUserId 是当前认证 Human，使用现有身份来源，不传 Bot owner 来冒充认证用户；后端仍执行最终授权。

```ts
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
import { userScopedParams } from './botController';

export type RepairComponent = 'ownership' | 'profile' | 'managers' | 'caller_config';
export type RepairItemStatus = 'succeeded' | 'failed' | 'unknown' | 'skipped_dependency' | 'not_applicable';
export interface CollaborationRepairReport {
  bot_id: string;
  status: 'succeeded' | 'partial_failed' | 'failed';
  items: Array<{ component: RepairComponent; status: RepairItemStatus; error_code: string | null }>;
}
export function repairBotCollaboration(botId: string, ownerUserId: string) {
  return backendRequest<BackendApiEnvelope<unknown>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/collaboration/repair`, {
      method: 'POST', params: userScopedParams({ user_id: ownerUserId }),
      retryOnTransient: false, operation: 'repair-bot-collaboration',
    },
  );
}
```

- [ ] **Step 3：实现报告校验。** `collaborationRepairReport.ts` 提供唯一 parser，拒绝成功假象；未知 error_code 不改变分项结果，UI 只展示映射后的状态。

```ts
import type { CollaborationRepairReport, RepairItemStatus } from '@/services/backendApi/bots/botCollaborationRepairController';
const components = ['ownership', 'profile', 'managers', 'caller_config'] as const;
const states: readonly RepairItemStatus[] = ['succeeded', 'failed', 'unknown', 'skipped_dependency', 'not_applicable'];
export function parseCollaborationRepairReport(value: unknown): CollaborationRepairReport {
  const invalid = () => new Error('未能确认修复结果，请稍后重试');
  if (!value || typeof value !== 'object') throw invalid();
  const r = value as Record<string, unknown>;
  if (typeof r.bot_id !== 'string' || !r.bot_id.trim() || !Array.isArray(r.items) || r.items.length !== 4) throw invalid();
  const items = r.items.map((value: unknown, index: number) => {
    if (!value || typeof value !== 'object') throw invalid();
    const item = value as Record<string, unknown>;
    if (item.component !== components[index] || !states.includes(item.status as RepairItemStatus) ||
      (item.error_code != null && typeof item.error_code !== 'string')) throw invalid();
    return { component: components[index], status: item.status as RepairItemStatus,
      error_code: typeof item.error_code === 'string' ? item.error_code : null };
  });
  const hasFailure = items.some((i) => ['failed', 'unknown', 'skipped_dependency'].includes(i.status));
  const status = hasFailure ? (items.some((i) => i.status === 'succeeded') ? 'partial_failed' : 'failed') : 'succeeded';
  if (r.status !== status) throw invalid();
  return { bot_id: r.bot_id, status, items };
}
```

- [ ] **Step 4：service 提供按认证用户和 Bot 去重的在途 Promise。** 只有在途请求保留；settle 后删除，不保存长期修复状态、不重试。跨关闭重开也不会产生第二个 POST。

```ts
import { repairBotCollaboration, type CollaborationRepairReport } from '@/services/backendApi/bots/botCollaborationRepairController';
import { isEnvelopeSuccess } from '@/services/backendApi/types';
import { parseCollaborationRepairReport } from './collaborationRepairReport';
export type { CollaborationRepairReport } from '@/services/backendApi/bots/botCollaborationRepairController';
const pending = new Map<string, Promise<CollaborationRepairReport>>();
const key = (botId: string, userId: string) => JSON.stringify([userId, botId]);
export const botCollaborationRepairService = {
  pending(botId: string, userId: string) { return pending.get(key(botId, userId)); },
  repair(botId: string, userId: string): Promise<CollaborationRepairReport> {
    const id = key(botId, userId);
    const active = pending.get(id);
    if (active) return active;
    const request = repairBotCollaboration(botId, userId).then((response) => {
      if (!isEnvelopeSuccess(response)) throw new Error('Bot 信息修复失败');
      return parseCollaborationRepairReport(response.data);
    }).finally(() => { if (pending.get(id) === request) pending.delete(id); });
    pending.set(id, request);
    return request;
  },
};
```

- [ ] **Step 5：GREEN 与提交。** 用 deferred Promise 断言重复 repair 返回同一 Promise、只发一次请求、失败后再手动调用产生新请求；原 HTTP Error 对象保持透传，以便全局提示去重。

```bash
npm test -- --runInBand --runTestsByPath test/services/backendApi/bots/botCollaborationRepairController.test.ts test/services/botWorkshop/botCollaborationRepairService.test.ts
npm run typecheck
git diff --check
```

预期全部 PASS；提交 `feat(frontend): consume owner-scoped collaboration repair reports`。

## Task 7B：修复 Hook 的 loading、结果与过期请求隔离

**Files：**

- Create: `src/hooks/useBotCollaborationRepair.ts`
- Modify: `src/hooks/useCollaborationPrivacy.ts`
- Test/Create: `test/hooks/useBotCollaborationRepair.test.tsx`
- Test/Modify: `test/hooks/useCollaborationPrivacy.test.tsx`
- Test/Modify: `test/components/CollaborationPrivacy/BotGeneralConfigDialog.test.tsx`（Task 7C 接线后完成刷新竞态集成测试）
- Reuse: `src/utils/errorToast.ts`

- [ ] **Step 1：RED 测试矩阵。** renderHook 使用 deferred repair Promise：连续两次 run 只调用一次 service；succeeded/partial_failed/failed 分别反馈；unknown 不弹成功；rerender 切换 botId 或 unmount 后完成旧请求不调用刷新、不写新状态；刷新失败保留原报告。还要覆盖 POST 已结束、自动 `privacy.load()` 仍在途时卸载/切 Bot：使用真实 privacy Hook 和共享 Store，mock service 返回不理会 abort 的 deferred Promise，先让 B 配置返回，再放行 A 的旧读取（成功、失败各一例），断言 B 的 overview/error/loading 完全不变；只 mock refresh 函数无法验证共享 Store 防线。

```tsx
// 在 jsdom 测试中，mock service、sonner、safeReportError；每项测试重新 reset。
test('double click produces one request', async () => {
  let resolve!: (value: CollaborationRepairReport) => void;
  jest.mocked(botCollaborationRepairService.repair).mockImplementation(() => new Promise((r) => { resolve = r; }));
  const refresh = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotCollaborationRepair('b1', 'u1', true, refresh));
  let first!: Promise<void>;
  act(() => { first = result.current.run(); void result.current.run(); });
  expect(botCollaborationRepairService.repair).toHaveBeenCalledTimes(1);
  expect(result.current.loading).toBe(true);
  await act(async () => { resolve(successReport); await first; });
  expect(result.current.report?.status).toBe('succeeded');
  expect(refresh).toHaveBeenCalledTimes(1);
});
```

测试头部完整导入 React Testing Library、Hook、service 和 `CollaborationRepairReport`；`successReport` 明确定义四项按序 succeeded/error_code:null 的 fixture，不 mock parser 结果代替 service 测试。

```bash
npm test -- --runInBand --runTestsByPath test/hooks/useBotCollaborationRepair.test.tsx
```

- [ ] **Step 2：实现 Hook。** 刷新动作来自组件，service 不 Toast。loading 同时保护同步双击；重开后跟踪在途请求，不重发 POST。

```ts
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { safeReportError } from '@/utils/errorToast';
import { botCollaborationRepairService as service } from '@/services/botWorkshop/botCollaborationRepairService';
import type { CollaborationRepairReport } from '@/services/botWorkshop/botCollaborationRepairService';

export function useBotCollaborationRepair(botId: string, userId: string, enabled: boolean, refresh: () => Promise<void>) {
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<CollaborationRepairReport>();
  const [error, setError] = useState<string>();
  const [refreshError, setRefreshError] = useState<string>();
  const busy = useRef(false);
  const generation = useRef(0);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const settle = useCallback(async (request: Promise<CollaborationRepairReport>, version: number) => {
    try {
      const result = await request;
      if (generation.current !== version) return;
      setReport(result);
      if (result.status === 'succeeded') toast.success('Bot 信息已修复');
      else if (result.status === 'partial_failed') toast.warning('部分 Bot 信息修复失败');
      else toast.error('Bot 信息修复失败');
      if (result.status !== 'failed') {
        try { await refreshRef.current(); }
        catch { if (generation.current === version) setRefreshError('修复请求已完成，配置刷新失败，请重新打开查看'); }
      }
    } catch (cause) {
      if (generation.current !== version) return;
      setError('未能确认修复结果，请稍后重试');
      safeReportError(cause, { title: 'Bot 信息修复失败' });
    } finally {
      if (generation.current === version) { busy.current = false; setLoading(false); }
    }
  }, []);
  useEffect(() => {
    const version = ++generation.current;
    busy.current = false; setLoading(false); setReport(undefined); setError(undefined); setRefreshError(undefined);
    const active = enabled ? service.pending(botId, userId) : undefined;
    if (active) { busy.current = true; setLoading(true); void settle(active, version); }
    return () => { generation.current += 1; };
  }, [botId, userId, enabled, settle]);
  const run = useCallback(async () => {
    if (!enabled || busy.current) return;
    busy.current = true; setLoading(true); setReport(undefined); setError(undefined); setRefreshError(undefined);
    await settle(service.repair(botId, userId), generation.current);
  }, [botId, userId, enabled, settle]);
  return { loading, report, error, refreshError, run };
}
```

UI 宿主使用按 `[bot.id,currentHumanId]` 的 key，确保切 Bot 的首帧也不显示旧报告；身份/权限变化同样使旧请求失效。若原 `privacy.load()` 吞掉读失败，则不能仅在这里 catch，Task 7C 将读取错误态传给修复行展示。

- [ ] **Step 3：使 privacy 的所有在途读取在卸载/上下文变化时失效。** 修复 Hook 的 generation 只保护自身结果，不保护 `privacy.load()` 内部对共享 Store 的写入。保留 load 签名和单次刷新行为；在 `useCollaborationPrivacy.ts` 的现有加载 effect cleanup 中，先递增该 Hook 的 `latestLoadId`，再执行原有 abort/reset：

```ts
return () => {
  latestLoadId.current += 1;
  controller.abort();
  useCollaborationPrivacyStore.getState().reset();
};
```

`load()` 的成功 `setOverview`、失败 `setError` 和 finally `setLoading` 均须继续检查捕获的 `loadId === latestLoadId.current`。这会同时使挂载 effect 请求和无 signal 的手动/修复后刷新失效；不能只在外层 await 后检查 generation，届时共享 Store 已可能被改写。保留原有 signal 检查，不能把 abort 当作取消修复 POST，也不清除仍由 service 跟踪的修复请求。Task 7C 的 keyed body 切 Bot 时会卸载旧 privacy Hook；同实例身份/固定目标变化走上述 effect cleanup。

使用 `test/hooks/useCollaborationPrivacy.test.tsx` 的真实 Hook/Store harness 验证：启动额外 `result.current.load()` 后卸载，reset 后的 Store 不被迟到成功、失败或 finally 改写；重挂 B 后，旧 A 返回也不能覆盖 B。保留正常重试和当前读取失败的测试，确保并未屏蔽有效错误。

- [ ] **Step 4：运行 GREEN 和门禁，提交。**

```bash
npm test -- --runInBand --runTestsByPath test/hooks/useBotCollaborationRepair.test.tsx test/hooks/useCollaborationPrivacy.test.tsx
npm run ci:guardrails
git diff --check
```

提交 `feat(frontend): track one-shot bot repair outcomes`。

## Task 7C：内部 Bot 协作能力卡片增加修复行

**Files：**

- Create: `src/components/CollaborationPrivacy/BotGeneralConfigDialog/CollaborationRepairRow.tsx`
- Create: `src/components/CollaborationPrivacy/SettingRow.tsx`（协作能力和发布审批共用的纯展示行）。
- Create: `src/components/CollaborationPrivacy/BotAbilitySettings.tsx`（移动原能力分区，导入共享 SettingRow，保留原导出兼容）。
- Modify: `src/components/CollaborationPrivacy/settingsSections.tsx`
- Modify: `src/components/CollaborationPrivacy/BotGeneralConfigDialog/index.tsx`
- Test/Create: `test/components/CollaborationPrivacy/CollaborationRepairRow.test.tsx`
- Test/Modify: `test/components/CollaborationPrivacy/BotGeneralConfigDialog.test.tsx`
- Test/Modify: `test/components/CollaborationPrivacy/ExternalBotGeneralConfigDialog.test.tsx`

- [ ] **Step 1：RED 布局与调用测试。** 在既有内部配置测试 fixture 明确设置 Backend bot.id=`bot-1`、ownerId=`900004`，BCS targetBot.id=`bot-1:900004`。点击后断言 service 参数是前者和认证 Human `900004`；非 Owner/锁被他人持有不能提交。外部 Bot、未声明 BCN 的 Coding Bot 不出现按钮。协作状态 hidden/offline，以及 privacy 错误/空结果仍显示符合 Owner 条件的入口。

```tsx
// 在现有 BotGeneralConfigDialog.test.tsx 的 imports 中添加 service，mock 其 repair。
await waitFor(() => expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled());
fireEvent.click(screen.getByRole('button', { name: '修复信息' }));
expect(botCollaborationRepairService.repair).toHaveBeenCalledWith('bot-1', '900004');
expect(screen.getByRole('button', { name: '修复中…' })).toBeDisabled();
```

为修复行传入 partial_failed fixture，断言「归属」「基本资料」「协作者」「调用配置」及「结果未确认」；断言全局提示已处理的错误不会再次 Toast。

```bash
npm test -- --runInBand --runTestsByPath test/components/CollaborationPrivacy/CollaborationRepairRow.test.tsx test/components/CollaborationPrivacy/BotGeneralConfigDialog.test.tsx test/components/CollaborationPrivacy/ExternalBotGeneralConfigDialog.test.tsx
```

- [ ] **Step 2：提取已有能力分区，添加精准插槽。** settingsSections 基线已 296 行，按实际调用关系拆分，而不是把 SettingRow 私有地搬进能力组件：

1. `SettingRowProps`、`SettingRow` 原样移动到 `SettingRow.tsx`，将函数改为 `export function SettingRow(...)`，迁入它依赖的 Badge/Switch/cn/ControlStateTooltip imports。
2. `BotAbilitySettingsProps`、`BotAbilitySettings` 移到 `BotAbilitySettings.tsx`，通过 `import { SettingRow } from './SettingRow'` 复用行组件。
3. `PublishApprovalSettings` 留在 `settingsSections.tsx`，它也使用 SettingRow，必须增加下面的本地 import；仅 re-export 其他符号不会创建 SettingRow 的本地绑定。

```ts
// settingsSections.tsx
import { SettingRow } from './SettingRow';
export { BotAbilitySettings } from './BotAbilitySettings';
```

只清理搬移导致的死 import，不修改发布审批行文案、disabled/loading 或 onChange。既有内部团队 Bot 测试须开启发布审批 capability，断言「共同编辑者发布需 Owner 审批」仍能渲染和提交、错误/加载态保持；同时验证独立权限页和外部 Bot 的能力分区未新增修复行。

新 `BotAbilitySettingsProps` 增加 `afterCollaborationStatus?: ReactNode`，解构后在 `collaborationStatus` 的 SettingRow 紧后插入：

```tsx
{afterCollaborationStatus}
```

其他能力行保持原条件。内部公开设置传入该 slot 且 `grid={false}`，因此第一行开关、第二行修复、后续公开画像沿单列 divide-y 排列；其他调用不传 slot，布局原样。

- [ ] **Step 3：实现纯展示修复行。** Hook 由父弹窗装配，行的 report/error/loading 随状态更新；从 `useBotCollaborationRepair` 导出返回类型或用 ReturnType 约束，禁止组件直接 import API。

```tsx
import { Button } from '@/components/ui/Button';
import type { useBotCollaborationRepair } from '@/hooks/useBotCollaborationRepair';
const componentText = { ownership: '归属', profile: '基本资料', managers: '协作者', caller_config: '调用配置' };
const statusText = { succeeded: '已修复', failed: '修复失败', unknown: '结果未确认',
  skipped_dependency: '前置步骤未完成', not_applicable: '不适用' };
export function CollaborationRepairRow({ repair, disabled, refreshFailed }: {
  repair: ReturnType<typeof useBotCollaborationRepair>; disabled: boolean; refreshFailed: boolean;
}) {
  return <div className="py-3" aria-label="修复 Bot 信息">
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0 flex-1">
        <p className="m-0 text-sm font-medium text-foreground">修复 Bot 信息</p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">修复当前 Bot 在「协作群」中的基本信息（名称、头像、归属、协作者等）</p>
      </div>
      <Button variant="outline" size="sm" loading={repair.loading} disabled={disabled}
        onClick={() => void repair.run()}>{repair.loading ? '修复中…' : '修复信息'}</Button>
    </div>
    {repair.report && repair.report.status !== 'succeeded' && <ul aria-live="polite" className="mt-2 text-xs text-muted-foreground">
      {repair.report.items.map((item) => <li key={item.component}>{componentText[item.component]}：{statusText[item.status]}</li>)}
    </ul>}
    {repair.error && <p role="alert" className="mt-2 text-xs text-destructive">{repair.error}</p>}
    {(repair.refreshError || (refreshFailed && repair.report && repair.report.status !== 'failed')) &&
      <p role="status" className="mt-2 text-xs text-muted-foreground">修复请求已完成，配置刷新失败，请重新打开查看</p>}
  </div>;
}
```

说明文字以 spec §6.1 最终文案为准；与后台实际范围保持一致，不增加好友关系修复调用。

- [ ] **Step 4：父弹窗装配 Owner 约束和降级卡片。** 父弹窗使用 `useHumanIdentity` 的认证 Human（非当前 Bot 视角），沿用 `getGeneralConfigAvailability`。正常卡片渲染 repairRow；error/空数据分支仍显示它。只存在一个修复 Hook，不能因 privacy loading/error 切换而丢失执行结果。

```tsx
const human = useHumanIdentity();
const userId = human.identity?.userId ?? '';
const repairVisible = !isExternalBot && showCollaborationAbilitySettings;
const availability = getGeneralConfigAvailability(Boolean(userId && bot.ownerId === userId), bot.lock?.status === 'other');
const repairEnabled = repairVisible && human.status === 'ready' && availability.enabled;
const repair = useBotCollaborationRepair(bot.id, userId, repairEnabled, privacy.load);
const repairRow = repairVisible ? <CollaborationRepairRow repair={repair}
  disabled={!repairEnabled} refreshFailed={Boolean(privacy.error)} /> : undefined;

// 正常分支的 BotAbilitySettings 替换原 grid 参数，添加 slot：
grid={isExternalBot}
afterCollaborationStatus={repairRow}

// privacy 的三元展示分支后，ConfirmDialog 之前，添加：
{repairVisible && (privacy.loading || privacy.error || !targetBot) && (
  <div className={sectionCardClass}>
    <h4 className="mb-3 text-xs font-semibold text-muted-foreground">协作能力</h4>
    {repairRow}
  </div>
)}
```

将现有导出的 `BotGeneralConfigDialog` 包成轻量 wrapper，以 `bot.id` 和认证 Human ID 的组合 key 挂载有状态 body，防止切 Bot 首帧残留结果。保持主文件 ≤300 行；若超限，将本任务新增装配拆成同目录子组件，不能把 Hook 塞入原 296 行 settingsSections。privacy loading 时修复行独立可用，不在它上面覆盖骨架。非 Owner disabled 原因沿用现有 Tooltip/说明组件。接线后运行 Task 7B 的“POST 已结束、配置刷新在途时切 Bot”真实 Hook/Store 集成用例，不能仅靠 mock repair Hook 判断隔离成功。

- [ ] **Step 5：GREEN、布局手测和提交。** 核对截图的左右排列与分隔线，窄屏说明换行而按钮不被挤出；开关、可见性、好友审批行为保持。

```bash
npm test -- --runInBand --runTestsByPath test/components/CollaborationPrivacy/CollaborationRepairRow.test.tsx test/components/CollaborationPrivacy/BotGeneralConfigDialog.test.tsx test/components/CollaborationPrivacy/ExternalBotGeneralConfigDialog.test.tsx
npm run typecheck
npm run ci:guardrails
git diff --check
```

提交 `feat(frontend): add internal bot collaboration repair action`。

## Task 8：集成验收、CI、行数和交付证据

**Files：**

- Read: 所有本次变更源文件和测试。
- Create: `specs/2026-11-04-collaboration-bot-avatars/validation.md`（实施时记录真实结果）。
- Modify: `specs/2026-11-04-collaboration-bot-avatars/spec.md`（实施结束时更新状态和证据）。

- [ ] **Step 1：将全部 AC 对应到实际测试名，执行模块 CI。** 先完成上面聚焦测试，不在每个任务重复全模块 build。只有本轮完整集成后运行：

```bash
npm run check:open-core
npm run ci
```

预期：Open Core 检查、guardrails、typecheck、lint、全量 Jest 和 build 全部 exit 0。记录实际套件数/测试数/退出码；因环境不能运行时写具体命令和原因，不写「应当通过」。不新增 Rust/Python 门禁执行要求，因为本计划不修改 BCS/Backend。

- [ ] **Step 2：检查所有改动源文件行数，确认门禁没有被放宽。** 在仓库根目录执行，基线取实施工作区开始时记录的提交；下面使用本计划的文档基线。若工作区基线更新，替换变量为 Task 0 记录值。

```bash
AVATAR_PLAN_BASE=bf74bac9141751ddfb00c9773ac553c108c697c9 python3 - <<'PY'
import os
import subprocess
from pathlib import Path
root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
args = ['git', 'diff', '--name-only', '--diff-filter=ACMR', os.environ['AVATAR_PLAN_BASE'], 'HEAD', '--', 'apps/frontend-nextgen/']
paths = subprocess.check_output(args, cwd=root, text=True).splitlines()
violations = []
for name in paths:
    path = root / name
    if path.suffix not in {'.ts', '.tsx', '.js', '.jsx'} or not path.exists():
        continue
    count = len(path.read_text().splitlines())
    print(f'{count:4} {name}')
    if count > 1000:
        violations.append(name)
assert not violations, violations
PY
git diff --check
```

此脚本检查已提交任务的源文件；未提交源码也须运行相同检查或先纳入任务提交。更严格的 Component/Hook/Store 限制由前面的 guardrails 检查。预计 GroupHeader 最紧，不能靠豁免或删除无关代码通过。

- [ ] **Step 3：运行本地页面，按明确场景验收。** 在模块目录使用已有公开本地配置；本地网关地址由开发环境提供，不写入源码。

```bash
npm run dev:local
```

| 场景 | 准备/操作 | 可观察结果 |
| --- | --- | --- |
| 混合群 | 2 个 Bot、1 个 Human；Bot A 有图，Bot B 为 null | 前两位按服务端顺序显示，人数正确，Human 无 Bot 默认图 |
| URL 类型 | 分别使用 HTTP(S)、根相对资源、data URI | Group 缩略图、消息、身份触发区和候选项均显示图片 |
| 图片失效 | URL 返回 404；随后通过既有数据刷新改为有效 URL | 先按 ID 回退，后重新加载新图，不弹业务错误 Toast |
| Session-only | 某 Bot 只加入当前 Session 并发送消息 | 会话显示该 Bot 图，Group 缩略图成员不被 Session 成员污染 |
| 名称与身份 | 两个同名 Bot 并发回复；回看带 bot_name 的历史消息 | 按各自 ID 显示头像，不因名称分支跳过 |
| 默认稳定性 | 对无图 Bot 改名、刷新、切群、切身份 | 同 ID 在三个入口的生成图相同 |
| 清空头像 | 刷新响应由 URL 变为 null；mine 和 Session 分别验证 | 对应入口回退默认图，旧缓存不覆盖清空；不要求接口间即时同步 |
| 请求预算 | Network 中打开新摘要 Group 列表、展开身份选择框 | 无为头像逐群/逐 Bot 补拉；mine/引擎信息沿用现有请求 |
| 身份不重置 | 选择 Bot、群和会话后，只刷新 Bot 头像 | 当前身份、群、会话保持，头像更新 |
| 收起布局 | 收起导航栏和群侧栏，打开对应选择/群入口 | Bot 身份不再显示首字，群入口仍可操作 |
| 请求竞态 | 让旧列表补拉延迟，切换身份/搜索后再放行 | 当前列表与头像不被旧响应覆盖 |
| 完整详情 | 摘要先返回，详情中的 muted/可见域后返回 | 头像可先显示，控制行为仍以完整详情为准 |
| 固定群详情 | 直接打开固定群/会话入口，打开管理并修改群成员 | 未加载全量群列表也能应用新详情、成员和头像 |
| 管理面板刷新 | 管理面板保持打开，刷新/搜索群列表且仍包含当前群 | 新摘要先显示，完整管理详情随后补齐；空详情不循环请求 |
| 缺省人数 | 拦截列表返回 4 个摘要成员但省略 participant_count，再对照显式 0 | 前者显示 4 人和 2+，后者保留 0，不混同缺省和零 |
| 修复后读取竞态 | A 的 POST 完成后延迟配置刷新，关闭 A 打开 B，再释放 A 的响应 | B 的 overview/error/loading 不被 A 的成功、失败或 finally 覆盖 |

可以用浏览器请求拦截构造 null/延迟/失效资源，记录为前端 fixture 验证；再连接用户已部署的接口验证真实响应。记录前端消费结果与请求证据，不把本分支是否含服务端源码或 PR 提交作为联调条件。

- [ ] **Step 4：写真实 validation.md，更新 spec 状态。** 记录 REL20261105 执行基线、消费的接口及响应、RED→GREEN 证据、完整 CI 命令/退出码、浏览器场景和未验证项。只有代码、必要门禁和验收满足后才将 spec 标记完成；未完成的前端接口联调用例须显式保留；不将已部署接口重新标记为待部署。

- [ ] **Step 5：审查最终 diff 和提交文档。**

```bash
git diff --stat bf74bac9141751ddfb00c9773ac553c108c697c9 HEAD
git status --short
git add specs/2026-11-04-collaboration-bot-avatars/spec.md specs/2026-11-04-collaboration-bot-avatars/validation.md
git commit -m "docs(frontend): record collaboration avatar validation"
```

本计划不包含自动 push、创建/合并 PR 或部署。若之后提交 PR，使用仓库要求的 Problem/Solution/Validation 段落，明确只消费增量协议、无需数据迁移，以及前端接口联调是否通过。

## Spec 覆盖矩阵与自检

| Spec / AC | 实施任务 | 主要验证 |
| --- | --- | --- |
| §2、§4.1；AC-01、AC-03、AC-12 | Task 1–2 | DTO、mapper、Session 详情补齐测试 |
| §4.2；AC-02、AC-11、AC-13、AC-31、AC-32 | Task 2、4 | 空摘要不补拉、清空、身份和列表代次、固定群 token、管理面板详情失效重拉 |
| §5.1；AC-04、AC-05、AC-06 | Task 1、3 | URL 来源、真实生成器、错误与源变更测试 |
| §5.2；AC-01、AC-15、AC-33 | Task 1、2、5 | GroupAvatarStack、GroupHeader、侧栏交互及 DTO→mapper→头像堆计数测试 |
| §5.3；AC-07、AC-08、AC-09、AC-10、AC-14 | Task 6 | sender 解析和真实消息渲染回归 |
| §4.3、§5.4；AC-16、AC-17、AC-18、AC-19、AC-20、AC-21、AC-22 | Task 7 | mine、Store、mapper、六布局及两类候选列表测试 |
| §6.1–§6.3；AC-23、AC-24、AC-25、AC-26、AC-27、AC-28、AC-29、AC-30、AC-34、AC-35 | Task 7A–7C | 空 body、Backend ID、Owner、单次请求、三种报告、降级卡片、旧响应隔离、共享 Store 刷新竞态、刷新失败及发布审批回归 |
| §6、§8、§9 | Task 0、8 | REL20261105 基线、公开依赖、全 CI、行数、浏览器和接口消费证据 |

编写时自检：每个 AC 均有任务；身份与成员使用相同 BotDisplayAvatar；默认图不写入业务字段；列表与详情的 mapper 名称分别固定为 `mapSessionListItem` / `mapSessionDetail`；`getDisplayParticipants` 只出现在展示路径；Group/Session 摘要不会作为角色/权限依据。

修复浏览器验收补充：内部 Owner 可操作，外部 Bot 无入口；关闭群聊仍可修复；Network 验证空 body POST 且只发一次；构造 partial_failed/unknown 报告验证分项；模拟 BCS 缺失、Owner 无权限和 503 不可用；关闭重开及切 Bot 不串报告。已部署修复接口联调与 fixture 验证分别记录，截图不提交。

计划状态：已编写，未执行。文中测试和 CI 的 PASS/FAIL 是执行阶段的预期，不是本轮运行结果。

## 2026-11-05 审阅修订记录

- 固定群 token 不再依赖列表加载初始化；增加固定入口详情刷新测试。
- Group 与 Session 显式区分列表/完整详情，管理面板在列表刷新后按需补齐，不为头像逐群补拉。
- privacy Hook cleanup 使所有旧读取失效，覆盖修复 POST 完成后的自动刷新窗口和共享 Store 写入。
- SettingRow 独立共享，保留发布审批的本地导入、渲染与提交测试。
- 计数在 mapper 边界完成缺省回退，覆盖真实 DTO 到头像堆链路，不用 0 隐藏缺省值。

本次仍仅修订文档；以上代码片段和测试为实施要求，不是已完成实现或测试通过证据。
