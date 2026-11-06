# Collab Assembly and Independent Internal Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Avernet's complete `apps/frontend-nextgen` and its Singlebox
startup, add a build-time `collab` assembly, and export an independent internal
collaboration site containing only the selected nextgen source closure. No
phase depends on the legacy `apps/frontend` implementation.

**Architecture:** Third assembly form alongside Open (`extensions/empty.ts`) and internal (`extensions/internal.ts`, internal repo). Form is selected by the `FRONTEND_FORM` env var in `config/config.ts`, which swaps the route table and the `@/extensions` alias target. In-page pruning uses a dependency-free slot registry (`src/extensions/slots.ts`): the assembly file statically imports the real task/TC implementations and registers them; components resolve slot contents through the registry, never through static imports of their own. The collab assembly registers `null` for every slot, so the task stack never enters the collab import graph. Open behavior must remain equivalent: the Open assembly registers today's real implementations and the consumer regressions verify it.

Component and test code also imports `@/extensions/slots` (registry) and `@/extensions/contract` (types) directly, so the facade alias in `config/config.ts` must use the **exact-match key `'@/extensions$'`**: webpack/enhanced-resolve prefix-matches non-`$` alias keys, which would rewrite `@/extensions/slots` to `<assembly>/slots` and fail `max build` while jest stays green (`moduleNameMapper` maps `^@/(.*)$` into `src/` and masks every alias mistake). The internal repo's overlay repointing needs the same `$`.

**Tech Stack:** umi max (build), React 18, jest (babel-jest, `test/`), nginx Docker image.

**Spec:** `apps/frontend-nextgen/specs/2026-11-03-collab-form/spec.md`

**Required continuation:** `export-plan.md` defines Tasks 17–20: type-complete
source isolation, dedicated private collab assembly, non-destructive internal
deployment export, full-source Singlebox regression and isolated artifact
validation. Tasks 1–16 alone do not complete the user's requested delivery.
The historical legacy exporter is a design reference only; do not invoke it
or copy its implementation/assets into the nextgen artifact.

**Status:** Revised after review on 2026-11-03. The checkboxes below are
implementation work still to execute, not evidence of passing builds or E2E.
Tasks 2A and 7A are mandatory parts of the runtime API boundary. The subsequent
user-confirmed complete-source/Singlebox/internal-export requirements are
recorded in `spec.md` and `export-plan.md`; those gates are not yet executed.
Revision checks: Task 14's extracted script passed all 10 fixture tests; the
proposed nullable-editor guard passed a targeted in-memory TypeScript check.
Full application builds and runtime acceptance have not run for this revision.

**Execution evidence (2026-11-04):** plan.md Tasks 1–16 implemented with
automated gates green (`npm run ci`: 679 jest suites / 5275 tests, closure
guard 11 entries / 509 files / 0 violations, Open build; `build:collab`;
`check:open-core`); live Singlebox regression, the runtime network trace, and
private-runtime acceptance remain owed by export-plan.md Tasks 17–20 (see
spec.md Status for the full unverified-boundary list).

**Working agreements:**
- All commands run from `apps/frontend-nextgen` unless stated otherwise.
- Jest single-file runs: `npx jest <path>` (add `-- --runInBand` behavior via CI). DOM tests use `/** @jest-environment jsdom */` docblock.
- Commit steps exist per repo convention (PR title `type(frontend-nextgen): outcome`), **but only run them when the user explicitly confirms git mutations**; otherwise stage nothing and report.
- Every new/modified source file must stay ≤ 1,000 lines (CI guardrail).
- Component code must never branch on the form; form differences live only in `config/config.ts`, `config/routes.collab.ts`, `src/extensions/collab.ts`.
- The above paths describe public assembly. Private selection lives only in
  the dedicated OCB nextgen composition/configuration entries in `export-plan.md`.
- Keep the full public `src/`, `config/routes.ts`, Open extension and test suites.
  Export writes to a new independent directory and never replaces Avernet source.
- Keep `FRONTEND_VARIANT=nextgen FRONTEND_FORM=open` working with Singlebox.
  Do not change the global frontend variant default or make startup require OCB.

---

### Task 1: `getPrimaryNavigationItems` capability (navigation baseline switch)

**Files:**
- Modify: `src/capabilities/types.ts` (AppCapabilities interface, next to `getInternalNavigationItems`, currently line ~568)
- Modify: `src/capabilities/defaultCapabilities.ts` (after `getInternalNavigationItems`, currently line ~226)
- Modify: `src/shell/navigation.tsx` (`getMergedNavigationItems`, lines 100–119)
- Test: `test/capabilities/primaryNavigationItems.test.ts` (create)

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from '@jest/globals';
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import type { NavigationItem } from '@/shell/navigation';
import { getMergedNavigationItems, navigationItems } from '@/shell/navigation';
import { Users } from 'lucide-react';

describe('getPrimaryNavigationItems capability', () => {
  it('Open Core 默认返回 null（沿用 shell/navigation 基线数组）', () => {
    expect(defaultCapabilities.getPrimaryNavigationItems()).toEqual({ status: 'available', value: null });
  });

  it('合并结果默认仍等于模块基线数组（Open 行为零变化）', () => {
    expect(getMergedNavigationItems().map((item) => item.id)).toEqual(navigationItems.map((item) => item.id));
  });

  it('装配覆盖后基线被整体替换（internal 注入项仍按 section 追加）', () => {
    const collabItems: NavigationItem[] = [
      {
        id: 'collaboration',
        label: '协作群',
        path: '/workspace/collaboration',
        icon: Users,
        section: 'collab',
        description: '与用户和 Bot 即时协作',
      },
    ];
    extendCapabilities({ getPrimaryNavigationItems: () => ({ status: 'available', value: collabItems }) });
    expect(getMergedNavigationItems().map((item) => item.id)).toEqual(['collaboration']);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/capabilities/primaryNavigationItems.test.ts`
Expected: FAIL — `defaultCapabilities.getPrimaryNavigationItems is not a function`.

- [ ] **Step 3: Add the capability to the contract**

In `src/capabilities/types.ts`, immediately after the `getInternalNavigationItems: ...` entry, add:

```ts
  /**
   * 产品一级导航基线的形态开关（collab-form）。
   * 返回 null = 使用 `src/shell/navigation.tsx` 的 Open Core 基线数组（Open/internal 默认，行为零变化）；
   * collab 装配（`src/extensions/collab.ts`）覆盖为「协作群 + 外部 Bot」两项。
   * internal overlay 的注入项仍经 `getInternalNavigationItems` 按 section 追加，与本基线正交。
   */
  getPrimaryNavigationItems: () => CapabilityResult<NavigationItem[] | null>;
```

`NavigationItem` is already type-imported at `src/capabilities/types.ts:4`.

- [ ] **Step 4: Add the Open default**

In `src/capabilities/defaultCapabilities.ts`, immediately after `getInternalNavigationItems`:

```ts
  // Open Core 默认 null = 沿用 shell/navigation 基线导航数组（不 import 该模块，避免依赖环）；
  // collab 形态经 src/extensions/collab.ts 覆盖为两项。
  getPrimaryNavigationItems: () => ({ status: 'available', value: null }),
```

- [ ] **Step 5: Consume the capability in `getMergedNavigationItems`**

In `src/shell/navigation.tsx`, replace the `getMergedNavigationItems` function (lines 100–119) with:

```tsx
export function getMergedNavigationItems(): NavigationItem[] {
  // 形态化基线：capability 默认返回 null → 模块基线数组（Open/internal 现状）；
  // collab 装配覆盖为两项极简导航。internal 注入项仍追加到同名分组末尾。
  const primary = getCapabilities().getPrimaryNavigationItems();
  const baseline = primary.status === 'available' && primary.value ? primary.value : navigationItems;
  const internal = getCapabilities().getInternalNavigationItems();
  const grouped: Record<NavigationSection, NavigationItem[]> = {
    collab: baseline.filter((item) => item.section === 'collab'),
    bot: baseline.filter((item) => item.section === 'bot'),
  };
  if (internal.status === 'available' && internal.value.length) {
    // 未知 section 剔除（防御性收口：契约变更期的录入错误不进侧栏）。
    for (const item of internal.value) {
      if (grouped[item.section]) grouped[item.section].push(item);
    }
  }
  // Evolution stays last in the Bot group, including after internal overlays.
  const evolution = grouped.bot.findIndex((item) => item.id === 'evolution-factory');
  if (evolution >= 0) grouped.bot.push(...grouped.bot.splice(evolution, 1));
  return [grouped.collab, grouped.bot].flatMap((items) => [
    ...items.filter((item) => item.placement !== 'last'),
    ...items.filter((item) => item.placement === 'last'),
  ]);
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `npx jest test/capabilities/primaryNavigationItems.test.ts test/shell/navigation.test.tsx test/shell/routeMeta.test.ts`
Expected: all PASS (existing shell tests assert literal identity with `navigationItems` — unchanged).

- [ ] **Step 7: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/capabilities/types.ts \
        apps/frontend-nextgen/src/capabilities/defaultCapabilities.ts \
        apps/frontend-nextgen/src/shell/navigation.tsx \
        apps/frontend-nextgen/test/capabilities/primaryNavigationItems.test.ts
git commit -m "feat(frontend-nextgen): add primary-navigation capability for form-level nav baselines"
```

---

### Task 2: `getShellVisibility().globalSearch` field + AppSidebar gate

**Files:**
- Modify: `src/capabilities/types.ts:268-271` (`ShellVisibility` interface)
- Modify: `src/capabilities/defaultCapabilities.ts:334-337` (`getShellVisibility`)
- Modify: `src/shell/AppSidebar.tsx` (lines 39-45, 59-68, 83-87, 96)
- Test: `test/capabilities/shellVisibility.test.ts` (create)

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from '@jest/globals';
import { defaultCapabilities } from '@/capabilities/defaultCapabilities';

describe('getShellVisibility', () => {
  it('Open Core 默认含 globalSearch:true（现状行为不变）', () => {
    expect(defaultCapabilities.getShellVisibility()).toEqual({
      status: 'available',
      value: { spaceSwitcher: false, notificationBell: true, globalSearch: true },
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/capabilities/shellVisibility.test.ts`
Expected: FAIL — received value lacks `globalSearch`.

- [ ] **Step 3: Extend the type and the default**

In `src/capabilities/types.ts`, replace the `ShellVisibility` interface and its doc (lines 261–271) with:

```ts
/**
 * 壳层入口形态级可见性（见 `getShellVisibility` capability）。
 * - `spaceSwitcher`：侧栏管理区域底部的空间切换器（隐藏不影响 initSpaceContext 空间数据链路）
 * - `notificationBell`：页头右上角通知中心（未读轮询随组件不挂载自然停止）
 * - `globalSearch`：侧栏品牌区搜索入口（隐藏时 GlobalSearchDialog 不挂载；
 *   collab 形态隐藏，避免搜索结果深链到该形态不存在的页面）
 * 注：原 `adminEntry` 已随【管理后台】导航项退役移除（split-admin-space-ticket-pages）。
 */
export interface ShellVisibility {
  spaceSwitcher: boolean;
  notificationBell: boolean;
  globalSearch: boolean;
}
```

In `src/capabilities/defaultCapabilities.ts`, update `getShellVisibility`:

```ts
  getShellVisibility: (): CapabilityResult<ShellVisibility> => ({
    status: 'available',
    value: { spaceSwitcher: false, notificationBell: true, globalSearch: true },
  }),
```

- [ ] **Step 4: Gate the search entry in AppSidebar**

In `src/shell/AppSidebar.tsx`:

Change line 39-45:

```tsx
  // 全局搜索弹窗（设计稿 artboard-016）：入口固定在通知中心左侧；可见性经 capability
  // （getShellVisibility.globalSearch，collab 形态隐藏：搜索结果会深链到该形态不含的页面）。
  const [searchOpen, setSearchOpen] = useState(false);
  // 品牌语义经 capability 解析：Open Core = Avernet 横版 wordmark；internal = 现状「蓝底色块 + TeamClaw」。
  const brand = getCapabilities().getProductBrand().value;
  // 通知中心为形态级入口（getShellVisibility.notificationBell）：Open Core（阿里云部署）默认展示；
  // 隐藏形态下未读数轮询随组件不挂载自然停止，通知 service 层不改。
  const { notificationBell, globalSearch } = getCapabilities().getShellVisibility().value;
```

Wrap the collapsed-mode search button (lines 59–68):

```tsx
          {globalSearch && (
            <IconButton
              size="sm"
              className="text-muted-foreground"
              label="搜索"
              icon={<Search className="h-4 w-4" />}
              onClick={() => {
                Tracert.click(Points.shell.globalSearchOpen);
                setSearchOpen(true);
              }}
            />
          )}
```

Wrap the expanded-mode search button (lines 84–87):

```tsx
            {globalSearch && (
              <IconButton label="搜索" icon={<Search className="h-4 w-4" />} onClick={() => {
                Tracert.click(Points.shell.globalSearchOpen);
                setSearchOpen(true);
              }} />
            )}
```

Gate the dialog mount (line 96):

```tsx
      {globalSearch ? <GlobalSearchDialog open={searchOpen} onOpenChange={setSearchOpen} /> : null}
```

- [ ] **Step 5: Sweep other ShellVisibility consumers for literal assertions**

Run: `grep -rn "getShellVisibility" src test --include='*.ts' --include='*.tsx'`
Expected: consumers destructure fields (additively safe); **fix any test that asserts the full object literal** to include `globalSearch: true`. Also visually confirm no consumer constructs a `ShellVisibility` literal that would now fail typecheck — `npm run typecheck` is the gate.

- [ ] **Step 6: Run tests + typecheck**

Run: `npx jest test/capabilities/shellVisibility.test.ts && npm run typecheck`
Expected: PASS + no type errors.

- [ ] **Step 7: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/capabilities/types.ts \
        apps/frontend-nextgen/src/capabilities/defaultCapabilities.ts \
        apps/frontend-nextgen/src/shell/AppSidebar.tsx \
        apps/frontend-nextgen/test/capabilities/shellVisibility.test.ts
git commit -m "feat(frontend-nextgen): add globalSearch to shell-visibility capability"
```

---

### Task 2A: Disable admin space initialization through a capability

**Files:**
- Modify: `src/capabilities/types.ts`, `src/capabilities/defaultCapabilities.ts`
- Modify: `src/shell/AppShell.tsx`
- Test: `test/shell/AppShell.test.tsx`, `test/capabilities/shellVisibility.test.ts`
- Task 12 integration: `src/extensions/collab.ts`

- [ ] **Step 1: Specify both independent initialization triggers**

Extend the shell suite's existing service mocks to record
`ensurePersonalSpaceOnAppEntry` and `initSpaceContext`. With the new capability
false, mount at `/workspace/collaboration`, navigate to `/external-bots`
(whose navigation section is `bot`), and assert neither function was called.
With the default true, assert the existing entry initialization and Bot-section
initialization still occur, even with `spaceSwitcher: false`. This tests the
consumer; a literal capability-value assertion alone is insufficient.

- [ ] **Step 2: Add the capability and gate both effects**

In `AppCapabilities`, next to the shell capabilities, add:

```ts
  getSpaceContextInitializationEnabled: () => CapabilityResult<boolean>;
```

In `defaultCapabilities` add:

```ts
  getSpaceContextInitializationEnabled: () => ({ status: 'available', value: true }),
```

Import `getCapabilities` in `AppShell.tsx`, resolve the boolean inside the
component, and replace the two existing effects (do not add duplicate effects):

```tsx
  const initializeSpaces = getCapabilities().getSpaceContextInitializationEnabled().value;
  useEffect(() => {
    if (initializeSpaces && activeSection === 'bot') void initSpaceContext();
  }, [activeSection, initializeSpaces]);
  useEffect(() => {
    if (initializeSpaces) void ensurePersonalSpaceOnAppEntry();
  }, [initializeSpaces]);
```

Keep auth and identity initialization unchanged. Task 12 sets the capability
false for collab; `spaceSwitcher` remains false so its own mount effect cannot
reintroduce initialization. This change gates requests before they start;
it does not suppress HTTP errors or weaken persistence failure handling.

- [ ] **Step 3: Verify default and collab shell behavior**

Run: `npx jest test/shell/AppShell.test.tsx test/capabilities/shellVisibility.test.ts && npm run typecheck`.
Expected: both triggers remain enabled in Open, both absent under the collab
capability. Task 16 additionally records actual HTTP requests through these
flows; passing hook mocks is not the runtime API acceptance evidence.

---

### Task 3: Extension slot registry + contract + Open assembly wiring

**Files:**
- Create: `src/extensions/contract.ts`
- Create: `src/extensions/slots.ts`
- Modify: `src/extensions/empty.ts`
- Modify: `src/components/BotWorkshop/BotAvatar/AvatarSettings.tsx` (nullable editor consumer)
- Test: `test/components/BotWorkshop/AvatarSettings.test.tsx` (null and registered editor)
- Modify: `src/app.tsx` (remove lines 10, 74–78, 87; use slot for observer)
- Modify: `config/config.ts:13` (alias key `'@/extensions'` → `'@/extensions$'`; target value unchanged for now — locked before Task 4 lands)
- Test: `test/extensions/slots.test.ts` (create)
- Modify: `jest.config.cjs` (add `setupFiles`)
- Create: `test/setup/registerExtensionSlots.ts`

The registry module is dependency-free on purpose: if components imported a registry that statically holds the real implementations, the import graph would cycle (component → registry → component).

- [ ] **Step 1: Write the failing test**

`test/extensions/slots.test.ts`:

```ts
import { describe, expect, it } from '@jest/globals';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';

describe('extension slots registry', () => {
  it('jest 全局装配已注册 Open 真实槽位（对齐 app.tsx 启动副作用）', () => {
    const slots = getExtensionSlots();
    expect(slots.groupTasks).not.toBeNull();
    expect(slots.groupServiceBotRouting).not.toBeNull();
    expect(slots.taskSessionNavigationObserver).not.toBeNull();
    expect(slots.taskPanelBootstrap).not.toBeNull();
  });

  it('可整体覆盖为 null（collab 形态），组件解析到空槽位', () => {
    registerExtensionSlots({ groupTasks: null, groupServiceBotRouting: null, taskSessionNavigationObserver: null, taskPanelBootstrap: null });
    expect(getExtensionSlots().groupTasks).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/extensions/slots.test.ts`
Expected: FAIL — module `@/extensions/slots` not found.

- [ ] **Step 3: Create the contract**

`src/extensions/contract.ts`:

The implementation-derived types below are an intermediate full-tree seam.
Task 17 must extract pure contracts before source export; runtime erasure of
`import type` does not make an absent implementation resolvable by TypeScript.

```ts
// 装配形态契约：empty(Open Core) / collab(协作群极简站) / internal(内部仓覆盖) 三形态共享。
// 全部 type-only import：编译期擦除，本文件运行时为纯类型，槽位真实实现由装配文件静态持有。
import type { AppCapabilities } from '@/capabilities';
import type AvatarEditorDefault from '@/components/BotWorkshop/BotAvatar/AvatarEditor';
import type { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import type { ServiceBotRoutingControl } from '@/pages/Workspace/components/Modals/ServiceBotRoutingControl';
import type { ManagerWorkerTasks } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import type { useGroupTaskExecution } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import type { useSessionTaskPanelBootstrap } from '@/hooks/useSessionTaskPanelBootstrap';
import type { useServiceBotRouting } from '@/pages/Workspace/hooks/useServiceBotRouting';
import type { buildTaskInstructions } from '@/services/tasks/taskMapper';
import type { createGroupViaExecute } from '@/services/workspace/groupExecuteService';
import type { ComponentType, LazyExoticComponent } from 'react';

/** 群任务栈槽位（collab 形态 null：不渲染任务 UI、不发任务引擎请求、相关代码不进产物）。 */
export interface GroupTasksExtension {
  /** Task execute 建群；Task 7A 将共享 groupService 的实现依赖改为此槽位。 */
  createGroupViaExecute: typeof createGroupViaExecute;
  /** 协作群任务执行装配（composer 能力菜单数据源）。 */
  useGroupTaskExecution: typeof useGroupTaskExecution;
  /** 输入区能力菜单（任务胶囊/工作流）。 */
  ComposerCapabilitiesMenu: typeof ComposerCapabilitiesMenu;
  /** 任务胶囊 → 任务指令消息构造。 */
  buildTaskInstructions: typeof buildTaskInstructions;
  /** 任务节点下钻单 bot 会话视图（覆盖整个聊天区）。 */
  TaskLinkedSession: typeof TaskLinkedSession;
  /** task_master_slave 群的 manager/worker 任务栏。 */
  ManagerWorkerTasks: typeof ManagerWorkerTasks;
}

/** 建群 service-bot 选路（TC Bot 分类服务；collab 形态 null：跳过分类查询、不打 routing 标签）。 */
export interface GroupServiceBotRoutingExtension {
  useServiceBotRouting: typeof useServiceBotRouting;
  ServiceBotRoutingControl: typeof ServiceBotRoutingControl;
}

/** 经 slots.ts 注册的组件/hook 槽位集合。 */
export interface AppExtensionSlots {
  groupTasks: GroupTasksExtension | null;
  groupServiceBotRouting: GroupServiceBotRoutingExtension | null;
  /** app.tsx 根容器任务会话联动观察者；null = 不挂载。 */
  taskSessionNavigationObserver: ComponentType | null;
  /** 任务副屏会话 bootstrap（群/单聊头部 hook 消费；collab 形态 null：不重放任务面板页签、不拉取任务栈，见 Task 8）。 */
  taskPanelBootstrap: typeof useSessionTaskPanelBootstrap | null;
}

/** appExtension 本体（capabilities + 组件装配），三形态各自在装配文件内注解满足。 */
export interface AppExtension {
  capabilities: AppCapabilities;
  /** Bot 头像编辑器（BotWorkshop 专属消费）；collab 形态无该页，置 null。 */
  avatarEditor: LazyExoticComponent<typeof AvatarEditorDefault> | null;
}
```

- [ ] **Step 4: Create the slot registry**

`src/extensions/slots.ts`:

```ts
// 槽位注册表：本模块零业务 import（无环保证），组件经 getExtensionSlots() 解析槽位。
// 装配文件（empty.ts Open / collab.ts 极简 / internal.ts 内源）在 app.tsx 启动链注册真实实现。
import type { AppExtensionSlots } from './contract';

let slots: AppExtensionSlots = {
  groupTasks: null,
  groupServiceBotRouting: null,
  taskSessionNavigationObserver: null,
  taskPanelBootstrap: null,
};

export function registerExtensionSlots(next: AppExtensionSlots): void {
  slots = next;
}

export function getExtensionSlots(): AppExtensionSlots {
  return slots;
}
```

- [ ] **Step 5: Register real implementations in the Open assembly**

In `src/extensions/empty.ts`:

Add imports after the existing ones:

```ts
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import { ServiceBotRoutingControl } from '@/pages/Workspace/components/Modals/ServiceBotRoutingControl';
import { ManagerWorkerTasks } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import { useSessionTaskPanelBootstrap } from '@/hooks/useSessionTaskPanelBootstrap';
import { useTaskSessionNavigationObserver } from '@/pages/Workspace/Chat/hooks/taskSessionNavigation';
import { useGroupTaskExecution } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import { useServiceBotRouting } from '@/pages/Workspace/hooks/useServiceBotRouting';
import { buildTaskInstructions } from '@/services/tasks/taskMapper';
import { createGroupViaExecute } from '@/services/workspace/groupExecuteService';
import { registerExtensionSlots } from './slots';
import type { AppExtension } from './contract';
```

Replace the final `appExtension` export (lines 105–108) with:

```ts
/** 任务副屏 V2 会话联动观察者(task-panel-v2 P4):无 UI,消费 pendingNavigation 跳会话 + 时间窗消息定位。 */
function TaskSessionNavigationObserver(): JSX.Element | null {
  useTaskSessionNavigationObserver();
  return null;
}

// Open Core 槽位注册：任务栈/TC 选路的真实实现（组件经 slots.ts 解析，import 单侧无环）。
registerExtensionSlots({
  groupTasks: {
    createGroupViaExecute,
    useGroupTaskExecution,
    ComposerCapabilitiesMenu,
    buildTaskInstructions,
    TaskLinkedSession,
    ManagerWorkerTasks,
  },
  groupServiceBotRouting: { useServiceBotRouting, ServiceBotRoutingControl },
  taskSessionNavigationObserver: TaskSessionNavigationObserver,
  taskPanelBootstrap: useSessionTaskPanelBootstrap,
});

export const appExtension: AppExtension = {
  avatarEditor: React.lazy(() => import('@/components/BotWorkshop/BotAvatar/AvatarEditor')),
  capabilities: defaultCapabilities,
};
```

The `AppExtension` annotation widens the Open export's editor type too.
In `AvatarSettings.tsx`, keep the existing hook before the availability guard:

```tsx
  const avatar = useBotAvatar(botId);
  const Editor = appExtension.avatarEditor;
  if (!Editor) return null;
  // Existing Popover/Suspense/Editor/Save JSX follows unchanged.
```

In the dedicated jsdom test, mock `useBotAvatar` with its existing value/action
shape and the `@/extensions` facade before importing `AvatarSettings`; exercise
both `avatarEditor: null` (empty render, no Save button) and a stub editor
(editor and Save button present). Do not skip this consumer in typecheck:
both forms still typecheck all `src/**/*`, including excluded routes.

- [ ] **Step 6: Move the root observer onto the slot in app.tsx**

In `src/app.tsx`:
- Delete line 10 (`import { useTaskSessionNavigationObserver } from '@/pages/Workspace/Chat/hooks/taskSessionNavigation';`).
- Delete the `TaskSessionNavigationObserver` function (lines 74–78).
- Add import: `import { getExtensionSlots } from '@/extensions/slots';`
- In `rootContainer`, remove `React.createElement(TaskSessionNavigationObserver),` and restructure the function to read the slot once per call:

```tsx
export function rootContainer(container: React.ReactNode) {
  // 任务会话联动观察者经槽位装配：collab 形态为 null（无任务栈），Open/internal 为真实观察者。
  const TaskObserver = getExtensionSlots().taskSessionNavigationObserver;
  return React.createElement(
    React.Fragment,
    null,
    container,
    React.createElement(GatewayLoginRedirector),
    React.createElement(ErrorNotifyObserver),
    TaskObserver ? React.createElement(TaskObserver) : null,
    React.createElement(ExternalLoginPromptModal),
    React.createElement(InviteCodeBindingModal),
    React.createElement(Toaster),
  );
}
```

- [ ] **Step 7: Create the jest global setup mirroring app boot**

`test/setup/registerExtensionSlots.ts`:

```ts
// jest 全局装配：单元测试不加载 app.tsx，槽位在此注册 Open 真实实现以对齐线上启动副作用；
// collab 形态断言用例在文件内自行 registerExtensionSlots(全 null) 覆盖。
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import { ServiceBotRoutingControl } from '@/pages/Workspace/components/Modals/ServiceBotRoutingControl';
import { ManagerWorkerTasks } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import { useGroupTaskExecution } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import { useServiceBotRouting } from '@/pages/Workspace/hooks/useServiceBotRouting';
import { buildTaskInstructions } from '@/services/tasks/taskMapper';
import { createGroupViaExecute } from '@/services/workspace/groupExecuteService';
import { useSessionTaskPanelBootstrap } from '@/hooks/useSessionTaskPanelBootstrap';
import { registerExtensionSlots } from '@/extensions/slots';

registerExtensionSlots({
  groupTasks: {
    createGroupViaExecute,
    useGroupTaskExecution,
    ComposerCapabilitiesMenu,
    buildTaskInstructions,
    TaskLinkedSession,
    ManagerWorkerTasks,
  },
  groupServiceBotRouting: { useServiceBotRouting, ServiceBotRoutingControl },
  taskSessionNavigationObserver: () => null,
  taskPanelBootstrap: useSessionTaskPanelBootstrap,
});
```

In `jest.config.cjs`, add to the exported config object (top level, before `projects`):

```js
  // 形态槽位注册（empty.ts 启动副作用的测试镜像）：组件经 getExtensionSlots() 解析。
  setupFiles: ['<rootDir>/test/setup/registerExtensionSlots.ts'],
```

⚠️ `jest.config.cjs` 是单一 `node` project（jsdom 由各测试文件的 `@jest-environment` docblock 按需切换，不是 node/jsdom 双 project），root 级 `setupFiles` 对该 project 生效。真正要盯的风险不是配置被忽略，而是**本 setup 会在每个 node 环境数据测试启动前导入整个 UI 组件栈**：若图内某模块在 import 期触碰 `document`/`window`，node 测试会批量失败（症状：Step 9/10 中 node 用例大面积炸在 setup 导入）。修法：把真实组件注册拆进带 jsdom docblock 的前置文件，node 仅注册 hooks/mapper；或给故障模块的 import 包一层惰性 `require`。修的是 setup 结构，不是测试断言。

- [ ] **Step 8: Lock the facade alias to exact-match (`'@/extensions$'`)**

In `config/config.ts` (line 13), change only the alias key — target value stays `src/extensions/empty` for now (Task 13 adds the form ternary):

```ts
    // 精确匹配键：非 `$` alias 键在 webpack/enhanced-resolve 里是前缀匹配——不带 `$` 时
    // `@/extensions/slots` 等子路径会被改写为 `<assembly>/slots` 而解析失败（jest 不受影响）。
    '@/extensions$': path.resolve(__dirname, '../src/extensions/empty'),
    // '@avernet/a2a-chat-client': ...（其余条目不变）
```

internal 仓的重指向必须同步带 `$`（随 carry-back PR 一并修改）。

Run: `npm run build`
Expected: success。这是 Task 13 形态开关之前唯一的构建门禁——证明 Task 3 接入（Task 4 起持续新增）的每一处 `@/extensions/slots` / `@/extensions/contract` 导入在 webpack alias 语义下可解析（jest 的 `moduleNameMapper` 永远测不出坏 alias）。若报 `Can't resolve '@/extensions/slots'`，先查 `$` 是否生效。

为什么放在这里而不是 Task 13：Task 4 起组件就开始 import `@/extensions/slots`，把 alias 修复拖到形态切换会留下连续多个不可构建的提交。

- [ ] **Step 9: Run tests + typecheck**

Run: `npx jest test/extensions/slots.test.ts test/components/BotWorkshop/AvatarSettings.test.tsx && npm run typecheck`
Expected: PASS + no type errors.

- [ ] **Step 10: Run the existing group-chat pane/composer tests to prove zero regression**

Run: `npx jest test/pages/Workspace/GroupChatPane.test.tsx test/pages/Workspace/components/GroupChatPane test/components/Workspace/ChatLayoutPanelSlot.test.tsx`
Expected: PASS (behavior unchanged; slots resolve to real implementations).

- [ ] **Step 11: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/extensions/contract.ts \
        apps/frontend-nextgen/src/extensions/slots.ts \
        apps/frontend-nextgen/src/extensions/empty.ts \
        apps/frontend-nextgen/src/components/BotWorkshop/BotAvatar/AvatarSettings.tsx \
        apps/frontend-nextgen/test/components/BotWorkshop/AvatarSettings.test.tsx \
        apps/frontend-nextgen/src/app.tsx \
        apps/frontend-nextgen/config/config.ts \
        apps/frontend-nextgen/test/extensions/slots.test.ts \
        apps/frontend-nextgen/test/setup/registerExtensionSlots.ts \
        apps/frontend-nextgen/jest.config.cjs
git commit -m "feat(frontend-nextgen): introduce extension slot registry for form-level feature pruning"
```

---

### Task 4: Slot the task drill-down and task execution in GroupChatPane

**Files:**
- Modify: `src/pages/Workspace/components/GroupChatPane/index.tsx` (lines 5, 8, 12, 80-81, 113-122, 167-169)
- Test: `test/pages/Workspace/components/GroupChatPane/GroupChatPaneSlots.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from '@jest/globals';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';

describe('GroupChatPane 任务槽位接线', () => {
  it('Open 装配（jest 全局 setup）下槽位为真实实现', () => {
    expect(getExtensionSlots().groupTasks?.useGroupTaskExecution).toBeDefined();
    expect(getExtensionSlots().groupTasks?.TaskLinkedSession).toBeDefined();
  });

  it('collab 空槽位下组件解析为 null（菜单/下钻不渲染）', () => {
    registerExtensionSlots({ groupTasks: null, groupServiceBotRouting: null, taskSessionNavigationObserver: null, taskPanelBootstrap: null });
    const slots = getExtensionSlots();
    expect(slots.groupTasks?.ComposerCapabilitiesMenu ?? null).toBeNull();
    expect(slots.groupTasks?.ManagerWorkerTasks ?? null).toBeNull();
  });
});
```

(The full component-level regression is carried by the existing pane/composer suites, which run under the jest global setup with real slots.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/pages/Workspace/components/GroupChatPane/GroupChatPaneSlots.test.tsx`
Expected: FAIL — `@/extensions/slots` does not exist until Task 3 lands (tasks are executed in order; after Task 3 this file runs green once rewiring is present, and the first assertion already passes after Task 3 alone).

- [ ] **Step 3: Rewire GroupChatPane to the slot registry**

In `src/pages/Workspace/components/GroupChatPane/index.tsx`:

- Delete line 5 (`TaskLinkedSession` import) and line 8 (`useGroupTaskExecution` import).
- Add after line 1:

```tsx
import { getExtensionSlots } from '@/extensions/slots';
```

- After the imports (before `export function GroupChatPane`), add module-level slot resolution (constant per build; hook identity stable, React hook rules satisfied):

```tsx
// 任务栈槽位（collab 形态注册 null）：hook/组件身份按构建恒定，不违反 hooks 规则。
const useResolvedGroupTaskExecution =
  getExtensionSlots().groupTasks?.useGroupTaskExecution ?? useNullGroupTaskExecution;
const GroupTaskLinkedSession = getExtensionSlots().groupTasks?.TaskLinkedSession;

/** collab 形态占位：不发起任务装配，composer 收到 null 后不渲染任务菜单。 */
function useNullGroupTaskExecution(): null {
  return null;
}
```

- Replace the `useGroupTaskExecution(...)` call (lines 113–122): change the callee to `useResolvedGroupTaskExecution(...)`, arguments unchanged.
- Keep the `useTaskSidePanelStore` import and the `linkedSession`/`closeLinkedSession` selectors (lines 80–81): the store is a tiny shared module that stays; `linkedSession` is simply never set in collab.
- Replace the drill-down branch (lines 167–169):

```tsx
        {linkedSession && GroupTaskLinkedSession ? (
          <GroupTaskLinkedSession target={linkedSession} userId={userIdentityId ?? undefined} onClose={closeLinkedSession} />
        ) : (
```

- [ ] **Step 4: Run pane + slot tests**

Run: `npx jest test/pages/Workspace/GroupChatPane.test.tsx test/pages/Workspace/components/GroupChatPane/GroupChatPaneSlots.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/pages/Workspace/components/GroupChatPane/index.tsx \
        apps/frontend-nextgen/test/pages/Workspace/components/GroupChatPane/GroupChatPaneSlots.test.tsx
git commit -m "feat(frontend-nextgen): resolve group-pane task features through extension slots"
```

---

### Task 5: Slot the composer capabilities menu and task send interception

**Files:**
- Modify: `src/pages/Workspace/components/GroupChatPane/GroupChatComposer.tsx` (lines 4, 10, 132-140, 208-220)
- Create: `src/components/Workspace/AttachmentMenu.tsx`
- Modify: `test/pages/Workspace/components/GroupChatComposer.test.tsx`

- [ ] **Step 1: Rewire the composer**

In `GroupChatComposer.tsx`:

- Delete line 4 (`ComposerCapabilitiesMenu` import) and line 10 (`buildTaskInstructions` import). Keep line 6 (`import type { UseTaskExecutionResult }`) — type-only.
- Add after the imports:

```tsx
// 任务菜单/任务指令构造经槽位解析（collab 形态为 null：菜单与任务发送分支整体消失）。
const CapabilitiesMenu = getExtensionSlots().groupTasks?.ComposerCapabilitiesMenu;
const buildInstructions = getExtensionSlots().groupTasks?.buildTaskInstructions;
```

plus `import { getExtensionSlots } from '@/extensions/slots';` with the other `@/` imports.

- In `submit` (lines 132–140), change the guard to:

```tsx
      if (execution && execution.selections.length > 0 && buildInstructions) {
        for (const instruction of buildInstructions(quotedContent, execution.selections)) {
          onSend(instruction, [], undefined);
        }
        execution.clearSelection();
        onDraftChange('');
        return;
      }
```

- Create a task-independent attachment menu. It must not import or re-export
  anything from `TaskComposerMenu` (including its barrel):

```tsx
import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { Plus } from 'lucide-react';

export function AttachmentMenu({ onUpload, onAddImage, disabled }: {
  onUpload: () => void;
  onAddImage: () => void;
  disabled: boolean;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" aria-label="添加附件" disabled={disabled}>
          <Plus className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="flex w-40 flex-col bg-popover p-1">
        <Button variant="ghost" onClick={onUpload}>上传文件</Button>
        <Button variant="ghost" onClick={onAddImage}>添加图片</Button>
      </PopoverContent>
    </Popover>
  );
}
```

- Import `AttachmentMenu` directly from its new module. In the toolbar
  (lines 208–220), retain the existing Open menu and provide the attachment
  menu when task execution is absent:

```tsx
              {execution && CapabilitiesMenu ? (
                <CapabilitiesMenu
                  execution={execution}
                  onUpload={() => setUploadFilesOpen(true)}
                  onAddImage={triggerImagePicker}
                  enableWorkflow={false}
                  disabled={!session}
                  selections={execution.selections}
                  onWorkflowSelected={execution.selectWorkflow}
                  onDynamicSelected={execution.selectDynamic}
                  onRemoveSelection={execution.removeSelection}
                />
              ) : (
                <AttachmentMenu
                  onUpload={() => setUploadFilesOpen(true)}
                  onAddImage={triggerImagePicker}
                  disabled={!session || connectionStatus !== 'connected'}
                />
              )}
```

- [ ] **Step 2: Verify attachment behavior with null task slots**

In the existing jsdom composer suite, arrange null slots **before importing
the composer**, because its task slot constants resolve at module evaluation.
Render a connected session with `execution={null}` and exercise:

- “添加附件 → 添加图片” invokes the hidden image input; dispatch a `change`
  with a PNG `File` and assert the image hook receives the file.
- “上传文件” opens `UploadFilesModal`; the existing staging/upload path works.
- Pasting an image still calls the image hook; “任务” and “工作流任务” are absent.
- The existing real task slot retains task selection and submission behavior.

Use the existing Sender and upload-hook fixtures; do not merely assert the
registry is null. The first image-selection case must fail if the fallback
menu is removed, protecting the reviewed regression.

Run: `npx jest test/pages/Workspace/components/GroupChatComposer.test.tsx test/pages/Workspace/components/GroupChatPane test/components/Workspace/TaskComposerMenu`
Expected: PASS (Open assembly registers the real menu, so behavior is unchanged).

- [ ] **Step 3: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/pages/Workspace/components/GroupChatPane/GroupChatComposer.tsx
git add apps/frontend-nextgen/src/components/Workspace/AttachmentMenu.tsx \
        apps/frontend-nextgen/test/pages/Workspace/components/GroupChatComposer.test.tsx
git commit -m "feat(frontend-nextgen): resolve group composer task menu through extension slots"
```

---

### Task 6: Slot the ManagerWorkerTasks rail in GroupChatCollabPanel

**Files:**
- Modify: `src/pages/Workspace/components/GroupChatPane/GroupChatCollabPanel.tsx` (lines 4, 33-58)

- [ ] **Step 1: Rewire the task rail**

In `GroupChatCollabPanel.tsx`:

- Delete line 4 (`import { ManagerWorkerTasks } from './ManagerWorkerTasks';`).
- Add import and module-level resolution:

```tsx
import { getExtensionSlots } from '@/extensions/slots';

// task_master_slave 任务栏槽位（collab 形态 null：只保留 ActiveRuns + CollabPanel）。
const GroupTaskRail = getExtensionSlots().groupTasks?.ManagerWorkerTasks;
```

- In branch A (lines 33–58), replace `<ManagerWorkerTasks key={session.sessionId} session={session} messages={messages} connected={connected} />` with:

```tsx
            {GroupTaskRail ? (
              <GroupTaskRail key={session.sessionId} session={session} messages={messages} connected={connected} />
            ) : null}
```

- [ ] **Step 2: Run collab-panel + slot tests**

Run: `npx jest test/pages/Workspace/components/GroupChatPane`
Expected: PASS.

- [ ] **Step 3: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/pages/Workspace/components/GroupChatPane/GroupChatCollabPanel.tsx
git commit -m "feat(frontend-nextgen): resolve manager-worker task rail through extension slots"
```

---

### Task 7: Slot the service-bot routing in the create-group flow

**Files:**
- Create: `src/pages/Workspace/hooks/noopServiceBotRouting.ts`
- Modify: `src/pages/Workspace/components/Modals/CreateGroupModal.tsx` (import + line ~81)
- Modify: `src/pages/Workspace/components/Modals/SelectedParticipantChip.tsx` (line 6, line 42)
- Test: `test/pages/Workspace/hooks/noopServiceBotRouting.test.ts` (create)

- [ ] **Step 1: Write the failing noop test**

```ts
import { describe, expect, it } from '@jest/globals';
import { useNoopServiceBotRouting } from '@/pages/Workspace/hooks/noopServiceBotRouting';

describe('useNoopServiceBotRouting', () => {
  it('恒 ready、不打标签、不做分类查询', () => {
    const routing = useNoopServiceBotRouting();
    expect(routing.ready).toBe(true);
    expect(routing.states).toEqual({});
    const participants = [{ actor_id: 'bot_1' }];
    expect(routing.applyTags(participants)).toBe(participants);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/pages/Workspace/hooks/noopServiceBotRouting.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the noop hook**

`src/pages/Workspace/hooks/noopServiceBotRouting.ts`:

```ts
import type { useServiceBotRouting } from './useServiceBotRouting';

type ServiceBotRouting = ReturnType<typeof useServiceBotRouting>;

/**
 * collab 形态的 service-bot 选路占位（无 TC Bot 分类服务）：
 * 恒 ready（不阻塞建群提交）、states 空（picker 过滤保持全量候选）、applyTags 原样透传。
 * 使用方与真实 hook 同签名，React hooks 规则满足（按构建恒定）。
 */
export function useNoopServiceBotRouting(): ServiceBotRouting {
  return {
    states: {},
    ready: true,
    selectRoutingMode: () => {},
    selectStage: () => {},
    retry: async () => {},
    applyTags: <T extends { actor_id: string }>(participants: T[]) => participants,
  };
}
```

- [ ] **Step 4: Rewire CreateGroupModal**

In `CreateGroupModal.tsx`:
- Replace the `useServiceBotRouting` import with:

```tsx
import { getExtensionSlots } from '@/extensions/slots';
import { useNoopServiceBotRouting } from '@/pages/Workspace/hooks/noopServiceBotRouting';
```

- Add module-level (above the component):

```tsx
// 建群选路槽位（collab 形态 noop：跳过 TC 分类查询，提交不被 routing.ready 阻塞）。
const useResolvedServiceBotRouting =
  getExtensionSlots().groupServiceBotRouting?.useServiceBotRouting ?? useNoopServiceBotRouting;
```

- Change line ~81 `const routing = useServiceBotRouting(participantBotIds, open);` to `const routing = useResolvedServiceBotRouting(participantBotIds, open);`.

- [ ] **Step 5: Rewire SelectedParticipantChip**

In `SelectedParticipantChip.tsx`:
- Delete the runtime import `import { ServiceBotRoutingControl } from './ServiceBotRoutingControl';` (line 6). Keep the type import of `useServiceBotRouting` (line 5, type-only).
- Add:

```tsx
import { getExtensionSlots } from '@/extensions/slots';

// 选路控件槽位（collab 形态 null：chip 不渲染 segmented 选路控件）。
const RoutingControl = getExtensionSlots().groupServiceBotRouting?.ServiceBotRoutingControl;
```

- Change line 42 to:

```tsx
      {routing && RoutingControl ? <RoutingControl actorId={bot.id} botName={bot.name} routing={routing} /> : null}
```

- [ ] **Step 6: Run modal + hook tests**

Run: `npx jest test/pages/Workspace/hooks/noopServiceBotRouting.test.ts test/pages/Workspace/components/Modals test/pages/Workspace/hooks/useServiceBotRouting.test.tsx`
Expected: PASS (Open default = real hook).

- [ ] **Step 7: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/pages/Workspace/hooks/noopServiceBotRouting.ts \
        apps/frontend-nextgen/src/pages/Workspace/components/Modals/CreateGroupModal.tsx \
        apps/frontend-nextgen/src/pages/Workspace/components/Modals/SelectedParticipantChip.tsx \
        apps/frontend-nextgen/test/pages/Workspace/hooks/noopServiceBotRouting.test.ts
git commit -m "feat(frontend-nextgen): resolve service-bot routing through extension slots"
```

---

### Task 7A: Separate BCS reads from task-execute group creation

**Files:**
- Create: `src/services/workspace/groupBcsReadService.ts`
- Modify: `src/services/workspace/groupExecuteService.ts`, `src/services/workspace/groupService.ts`
- Modify: `src/pages/Workspace/components/Modals/CreateGroupModal.tsx`, `GroupConfigFields.tsx` in the same directory
- Verify: `src/pages/Workspace/hooks/useCreateGroup.ts`
- Modify: `test/services/workspace/groupExecuteService.aceLogin.test.ts`, `groupService.test.ts` in the same directory
- Modify: `test/pages/Workspace/components/Modals/CreateGroupModal.test.tsx`

The executor slot is already declared and registered in Task 3. This task is
mandatory even though `GROUP_CREATE_VIA_EXECUTE` currently defaults to false:
the checkbox can enable it, and the shared service's static import already
reaches `backendApi/tasks/taskController` without any checkbox interaction.

- [ ] **Step 1: Move BCS read behavior without changing its contract**

Move these definitions verbatim from `groupExecuteService.ts` into
`groupBcsReadService.ts`, together with their read-only helpers and imports:
`BcsGroupDetailRaw`, `loadBcsGroupSessions`, `loadBcsGroupDetail`,
`isBcsGroupId`, `loadGroupDetailOrBcs`, `loadGroupSessionsOrBcs`, and the
inflight session-request map. Keep ACE-body handling, legacy field mapping,
pagination and `bcs_grp_` behavior. The new module must not import
`executeTask`, `groupTaskAdapter`, or `groupExecuteService`.

Keep `createGroupViaExecute` in `groupExecuteService.ts`; import its
`loadBcsGroupDetail` dependency directly from `./groupBcsReadService`.
Retarget `groupService`'s three BCS read imports to `./groupBcsReadService`
and remove its `execCreateGroupViaExecute` import. Update direct read-helper
tests to the new module; the task-execute test in `groupCreateRequest.test.ts`
keeps its executor import. Do not add a read barrel that re-exports the executor.

- [ ] **Step 2: Delegate explicit execute calls through the existing slot**

Import `getExtensionSlots` in `groupService.ts`. Replace only its
`createGroupViaExecute` method, retaining its caller-facing signature:

```ts
  createGroupViaExecute(input: CreateGroupInput, ownerUserId: string): Promise<DomainResult<GroupView>> {
    const execute = getExtensionSlots().groupTasks?.createGroupViaExecute;
    if (!execute) {
      return Promise.resolve({
        ok: false,
        error: toDomainError('GROUP_EXECUTE_UNAVAILABLE', '当前站点不支持以任务执行方式创建协作群。'),
      });
    }
    return execute(input, ownerUserId, (value) => this.createGroup(value));
  },
```

`useCreateGroup` keeps the explicit execute-vs-ordinary dispatch. A stale or
programmatic `viaExecute: true` must receive the above error, rather than
silently changing the requested operation or calling the task engine.
The slots module has only type imports, so it does not restore the executor's
runtime dependency. Open/internal retain the existing executor and fallback.

- [ ] **Step 3: Hide the option and force ordinary UI submission when absent**

In `CreateGroupModal`, alongside `enableTaskExecute`, derive:

```tsx
  const taskExecuteAvailable = Boolean(getExtensionSlots().groupTasks?.createGroupViaExecute);
  const effectiveTaskExecute = taskExecuteAvailable && enableTaskExecute;
```

Use `effectiveTaskExecute` for all three existing reads: the local
`viaExecute` validation calculation, `run(..., { viaExecute: ... })`, and
`GroupConfigFields`'s `viaExecute` prop. Keep the state setter and reset behavior
unchanged. Add `allowTaskExecute={taskExecuteAvailable}` to the fields component.
In `GroupConfigFieldsProps`, add `allowTaskExecute?: boolean`, destructure it
with default `true` to preserve other callers, and change **only the execute
checkbox wrapper** (not the YAML/binding wrappers):

```tsx
      {kind === 'task_dag' && allowTaskExecute && (
        <div className="flex items-center gap-2">
          <Checkbox
            id="create-group-via-execute"
            checked={viaExecute}
            onCheckedChange={(checked) => onViaExecuteChange(checked)}
          />
          <label htmlFor="create-group-via-execute" className="select-none text-xs font-semibold text-foreground">
            是否以任务执行
          </label>
        </div>
      )}
```

YAML editing, validation, binding, preview and the original identity eligibility
remain unchanged. `state_machine` creation in collab uses `createGroup` and
preserves `definitionYaml` and `participantBindings`.

- [ ] **Step 4: Verify both paths and the forbidden dependency**

Extend the existing modal suite (with its `botIdentity`, YAML and Alpha binding
fixtures): under null slots, select 自定义协作, validate YAML, bind Alpha and
submit. Assert the execute checkbox is absent and `groupService.createGroup`
receives `strategy: 'state_machine'` with the same YAML/bindings. Keep the Open
execute case and assert it still calls `createGroupViaExecute`.

In the service suite, explicitly call `createGroupViaExecute` with null slots:
assert `GROUP_EXECUTE_UNAVAILABLE`, zero execute requests and no ordinary-create
fallback. Keep BCS detail/session compatibility tests after the extraction.
Restore the Open slot object between cases; component null-slot tests arrange
slots before import so module-level hook selection matches the intended form.

Run: `npx jest test/services/workspace/groupService.test.ts test/services/workspace/groupCreateRequest.test.ts test/services/workspace/groupExecuteService.aceLogin.test.ts test/pages/Workspace/components/Modals/CreateGroupModal.test.tsx && npm run typecheck`.
Task 14 must additionally reject any path from a collab entry to
`groupExecuteService` or `groupTaskAdapter`, including dynamic imports.

---

### Task 8: Group header hooks — slot the task-panel bootstrap, precise sidePane imports

Why this task exists (review finding): `useGroupChatHeaderPanels.ts` is on the collab keep-chain (`GroupWorkspaceArea` / `GroupHeader` consume it), but line 24 statically imports `useSessionTaskPanelBootstrap` — a FORBIDDEN module that pulls `useTaskSwitcherTasks` + `services/myTask` (`listMyTasks`), i.e. the MyTask/task-engine chain, and would violate spec §5 ("task engine is never called from the collab form"). Line 18 additionally imports `reopenSidePaneTabsFromMessages` / `sessionHasSidePaneContent` from the `useChatHeaderPanels` re-export, which drags the whole 228-line chat header module (and its own `useSessionTaskPanelBootstrap` edge) into the closure; the pure implementations live in `Chat/hooks/sidePaneContent.ts`. Both edges must go through here — the Task 14 guard only rediscovers them. The slot key `taskPanelBootstrap` was already added to the contract/registry in Task 3.

**Files:**
- Modify: `src/pages/Workspace/hooks/useGroupChatHeaderPanels.ts` (lines 18, 24, 96)
- Test: `test/pages/Workspace/hooks/useGroupChatHeaderPanelsSlot.test.ts` (create)

- [ ] **Step 1: Write the failing static-edge test**

`test/pages/Workspace/hooks/useGroupChatHeaderPanelsSlot.test.ts`:

```ts
// 群头部 hook 的静态边收口（Task 14 闭包守卫落地前的任务级红线）：
// useGroupChatHeaderPanels 属 collab 保留链，不得运行时引用任务副屏 bootstrap；
// 对 chat 侧 header hook 仅允许 type-only 引用（UseSessionHistorySearchModel）。
import { describe, expect, it } from '@jest/globals';
import { readFileSync } from 'fs';
import { join } from 'path';

const source = readFileSync(
  join(__dirname, '../../../../src/pages/Workspace/hooks/useGroupChatHeaderPanels.ts'),
  'utf8',
);

describe('useGroupChatHeaderPanels 静态边', () => {
  it('不再 import 任务副屏 bootstrap（该模块拉入 useTaskSwitcherTasks + services/myTask）', () => {
    expect(source).not.toMatch(/from\s+['"]@\/hooks\/useSessionTaskPanelBootstrap['"]/);
  });

  it('副屏重放助手改从纯模块 sidePaneContent 精确引入', () => {
    expect(source).toMatch(/from\s+['"]@\/pages\/Workspace\/Chat\/hooks\/sidePaneContent['"]/);
    expect(source).not.toMatch(/^import\s+(?!type\b)[^\n]*useChatHeaderPanels['"]/m);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/pages/Workspace/hooks/useGroupChatHeaderPanelsSlot.test.ts`
Expected: FAIL — 现状第 24 行仍 import bootstrap、第 18 行仍从 `useChatHeaderPanels` 引入，两条断言均红。

- [ ] **Step 3: Rewire the hook**

In `src/pages/Workspace/hooks/useGroupChatHeaderPanels.ts`:

- Delete line 24 (`import { useSessionTaskPanelBootstrap } from '@/hooks/useSessionTaskPanelBootstrap';`).
- Change line 18 to import from the pure module (keep it single-line so the Step 1 regex stays meaningful):

```ts
import { reopenSidePaneTabsFromMessages, sessionHasSidePaneContent } from '@/pages/Workspace/Chat/hooks/sidePaneContent';
```

- Keep line 17 (`import type { UseSessionHistorySearchModel } from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';`) — type-only, the closure crawler skips it.
- Add with the other imports:

```ts
import { getExtensionSlots } from '@/extensions/slots';
import type { AppExtensionSlots } from '@/extensions/contract';
```

- Above the hook definition, add module-level resolution (noop typed via the contract so a real-signature drift fails typecheck):

```ts
// 任务副屏 bootstrap 槽位（collab 形态注册 null）：hook 身份按构建恒定，不违反 hooks 规则。
// collab 下不重放任务面板页签、不发起 services/myTask 拉取（spec §5：任务引擎不进本形态运行时面）。
const useNullTaskPanelBootstrap: NonNullable<AppExtensionSlots['taskPanelBootstrap']> = () => undefined;
const useResolvedTaskPanelBootstrap = getExtensionSlots().taskPanelBootstrap ?? useNullTaskPanelBootstrap;
```

- Line 96: change the callee to `useResolvedTaskPanelBootstrap({ sessionId, userId: options.userId, panelRef });`, arguments unchanged.

Note: the chat-side twin `useChatHeaderPanels` keeps its own real bootstrap import — after the line-18 retarget nothing kept imports it, so it leaves the collab closure without surgery of its own.

- [ ] **Step 4: Run new + existing tests, verify residual edges**

Run: `npx jest test/pages/Workspace/hooks test/pages/Workspace/components/GroupHeader.test.tsx`
Expected: all PASS（Open 行为零变化：jest 全局 setup 注册的是真实 hook）。

> [Errata 2026-11-04] 「jest 全局 setup 注册的是真实 hook」按本计划早期设想撰写；自 T3 落地起
> 全局 setup 注册的是占位 UI 组件（渲染空 Fragment）与调用即抛错的 hook/服务桩件，
> 见 test/setup/registerExtensionSlots.ts 头注释；需要真实实现的用例在套件内自行注册覆盖。

Verify residual edges point only at allowed modules:

```bash
grep -n "useSessionTaskPanelBootstrap\|useChatHeaderPanels" src/pages/Workspace/hooks/useGroupChatHeaderPanels.ts
# Expected: 仅第 17 行 type-only 引用 useChatHeaderPanels；无 bootstrap 引用。
grep -rn "from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels'" src/pages/Workspace/Collaboration src/pages/Workspace/hooks src/pages/Workspace/components \
  --include='*.ts' --include='*.tsx' | grep -v "import type"
# Expected: 无输出（保留链仅剩 type-only 引用）。
```

- [ ] **Step 5: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/pages/Workspace/hooks/useGroupChatHeaderPanels.ts \
        apps/frontend-nextgen/test/pages/Workspace/hooks/useGroupChatHeaderPanelsSlot.test.ts
git commit -m "feat(frontend-nextgen): slot the task-panel bootstrap out of group header hooks"
```

---

### Task 9: Move SafeHtml/sanitizeHtml out of the TaskPanel asset tree

The kept 会话文件预览副屏 imports two generic HTML utilities that happen to live inside the task-panel asset folder; moving them keeps `src/assets/TaskPanel/` fully excludable.

**Files:**
- Move: `src/assets/TaskPanel/SafeHtml.tsx` → `src/shared/html/SafeHtml.tsx`
- Move: `src/assets/TaskPanel/sanitizeHtml.ts` → `src/shared/html/sanitizeHtml.ts`
- Modify: every importer (find via grep)

- [ ] **Step 1: Enumerate importers**

Run: `grep -rln "TaskPanel/SafeHtml\|TaskPanel/sanitizeHtml\|from '.*SafeHtml'\|from '.*sanitizeHtml'" src test`
Expected (from analysis): `src/pages/Workspace/components/GroupChatPane/SessionFilesPreviewPane.tsx`, `src/pages/Workspace/components/GroupChatPane/sessionFileMarkdown.ts`, plus internal `src/assets/TaskPanel/*` importers (relative paths).

- [ ] **Step 2: Move the files**

```bash
mkdir -p src/shared/html
git mv src/assets/TaskPanel/SafeHtml.tsx src/shared/html/SafeHtml.tsx
git mv src/assets/TaskPanel/sanitizeHtml.ts src/shared/html/sanitizeHtml.ts
```

- [ ] **Step 3: Update importers**

- `SessionFilesPreviewPane.tsx` line 1: `import { SafeHtml } from '@/assets/TaskPanel/SafeHtml';` → `import { SafeHtml } from '@/shared/html/SafeHtml';`
- `sessionFileMarkdown.ts` line 3: `import { sanitizeMarkdownHtml } from '@/assets/TaskPanel/sanitizeHtml';` → `import { sanitizeMarkdownHtml } from '@/shared/html/sanitizeHtml';`
- Remaining importers reported by the grep in Step 1 (TaskPanel-internal relative imports become `@/shared/html/...`).
- Verify no reverse imports remain: `grep -rn "assets/TaskPanel" src/shared/html/` must output nothing.

- [ ] **Step 4: Typecheck + targeted tests**

Run: `npm run typecheck && npx jest test/pages/Workspace`
Expected: no type errors; tests PASS.

- [ ] **Step 5: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/shared/html \
        apps/frontend-nextgen/src/assets/TaskPanel \
        apps/frontend-nextgen/src/pages/Workspace/components/GroupChatPane
git commit -m "refactor(frontend-nextgen): move shared html sanitizers out of the task-panel asset tree"
```

---

### Task 10: Relocate the external-bot service chain out of `services/botWorkshop/`

Why this task exists (review finding): the collab keep-chain `/external-bots` → `ExternalBotPanel` → `useExternalBots` reaches `services/botWorkshop/externalBotService` (`src/hooks/useExternalBots.ts:1`) and `ExternalBotPanel.tsx:11` imports `externalBotToGeneralConfig` from the same file — but `src/services/botWorkshop/` is the first FORBIDDEN prefix (acceptance criterion 1). The spec's closure analysis covered the Collaboration subtree only; the collab route table's new page entry widens the closure. The service's only runtime dependency inside the forbidden tree is `mapBotDto` (for `externalBotToGeneralConfig`), and `botMapper` itself needs `botLifecycleResolver` plus the two pure capability getters that live in `agentCodingTemplateService.ts` (a 424-line file whose backend-controller import forbids moving it wholesale). Minimal relocation radius, verified 2026-11-03: **three file moves + one pure-function extraction**; everything else is import retargeting. Pure relocation — Open behavior is byte-identical (same implementation, new paths; the botWorkshop barrel keeps every existing re-export name).

**Files:**
- Move: `src/services/botWorkshop/externalBotService.ts` → `src/services/externalBots/externalBotService.ts`
- Move: `src/services/botWorkshop/botMapper.ts` → `src/services/bots/botMapper.ts`
- Move: `src/services/botWorkshop/botLifecycleResolver.ts` → `src/services/bots/botLifecycleResolver.ts`
- Create: `src/services/bots/botCapability.ts` — relocated `AgentCodingTemplate` interface + pure getters `getBcnCapability` / `getServiceBotCapability` / `supportsServiceBot` + private copies of the 4-line `record`/`list` helpers
- Modify: `src/services/botWorkshop/agentCodingTemplateService.ts` (delete the moved interface + three functions; re-export them from the new module)
- Modify: `src/services/botWorkshop/index.ts` (barrel re-export retargeting)
- Modify: `src/hooks/useExternalBots.ts:1`, `src/components/BotWorkshop/ExternalBotPanel.tsx:11`, `src/hooks/useExpertBotCreation.ts:6`, `src/services/botWorkshop/localBotService.ts:10`, `src/services/botWorkshop/botWorkshopService.ts:27` (direct-path importers)
- Move/modify tests: `test/services/botWorkshop/externalBotService.test.ts` → `test/services/externalBots/`; `test/services/botWorkshop/botMapper.test.ts` (+ `botLifecycleOwner.test.ts` if it imports the old path) retarget imports. `agentCodingTemplateService.test.ts` needs no change (re-export keeps identity).

- [ ] **Step 1: Extract the capability slice**

Create `src/services/bots/botCapability.ts`: move the `AgentCodingTemplate` interface, `getServiceBotCapability`, `getBcnCapability`, `supportsServiceBot` from `agentCodingTemplateService.ts` **verbatim**, plus local copies of the `record`/`list` helpers (they stay in the source file too — its other exports use them ~35×). The new module must not gain runtime imports; if the moved interface references `BotTemplateField`/`BotTemplateDto` types, keep those `import type` edges (type-only is erased and passes the closure guard).

In `agentCodingTemplateService.ts`: delete the moved interface + three functions, add

```ts
import type { AgentCodingTemplate } from '@/services/bots/botCapability';

// 能力位判定已迁至 services/bots/botCapability（外部 Bot 服务链复用，见 collab-form spec §4）；此处 re-export 保持既有 import 源不变。
export { getBcnCapability, getServiceBotCapability, supportsServiceBot } from '@/services/bots/botCapability';
export type { AgentCodingTemplate } from '@/services/bots/botCapability';
```

- [ ] **Step 2: Move lifecycle resolver + mapper**

```bash
mkdir -p src/services/bots src/services/externalBots
git mv src/services/botWorkshop/botLifecycleResolver.ts src/services/bots/botLifecycleResolver.ts
git mv src/services/botWorkshop/botMapper.ts src/services/bots/botMapper.ts
```

Retarget imports inside the moved files:

- `src/services/bots/botLifecycleResolver.ts`: `import type { BotLifecycle } from './types';` → `import type { BotLifecycle } from '@/domain/botWorkshop';`（`./types` 本就是 Domain 层的兼容出口；`hasServiceDraftEvidence` 与 `backendApi/types` 引用不变）。
- `src/services/bots/botMapper.ts`:
  - `import { getBcnCapability, getServiceBotCapability } from './agentCodingTemplateService';` → `from './botCapability';`
  - `import { resolveBotLifecycle } from './botLifecycleResolver';` — 两文件同目录搬迁，相对路径不变，不改。
  - `import type { BotDomain, BotRuntimeDomain } from './types';` → `import type { BotDomain, BotRuntimeDomain } from '@/domain/botWorkshop';`
  - `@/adapters/bot-runtime/resolveBotRuntime` 与 `@/services/backendApi/types` 引用不变。

- [ ] **Step 3: Move the external-bot service**

```bash
git mv src/services/botWorkshop/externalBotService.ts src/services/externalBots/externalBotService.ts
```

Retarget imports:

```ts
import { mapBotDto } from '@/services/bots/botMapper';
import type { BotDomain } from '@/domain/botWorkshop';
```

（`collaborationBotController` / `backendApi/types` 引用不变——服务本体本就走 collaboration 数据面，符合 spec §5。）

- [ ] **Step 4: Retarget the barrel and direct importers**

- `src/services/botWorkshop/index.ts`: `export * from './botMapper';` → `export * from '@/services/bots/botMapper';`，`export * from './externalBotService';` → `export * from '@/services/externalBots/externalBotService';`
- `src/hooks/useExternalBots.ts:1` → `@/services/externalBots/externalBotService`
- `src/components/BotWorkshop/ExternalBotPanel.tsx:11` → `@/services/externalBots/externalBotService`
- `src/hooks/useExpertBotCreation.ts:6` → `@/services/bots/botMapper`
- `src/services/botWorkshop/localBotService.ts:10`、`src/services/botWorkshop/botWorkshopService.ts:27` → `@/services/bots/botMapper`

兜底 sweep：

```bash
grep -rn "botWorkshop/botMapper\|botWorkshop/externalBotService\|botWorkshop/botLifecycleResolver" src test
# Expected: 仅注释/文档命中（如有），无代码 import。
grep -rn "from '@/services/botWorkshop'" src/pages/ExternalBots src/components/BotWorkshop/ExternalBotPanel.tsx src/hooks/useExternalBots.ts src/components/CollaborationPrivacy
# Expected: 仅 BotGeneralConfigDialog 的 import type（type 边不入闭包）。
```

- [ ] **Step 5: Move/retarget tests**

```bash
mkdir -p test/services/externalBots
git mv test/services/botWorkshop/externalBotService.test.ts test/services/externalBots/externalBotService.test.ts
```

- `test/services/externalBots/externalBotService.test.ts`：import 改指 `@/services/externalBots/externalBotService`。
- `test/services/botWorkshop/botMapper.test.ts`（以及 `botLifecycleOwner.test.ts`，若其 import 指向旧路径）：改指 `@/services/bots/*`。
- `test/services/botWorkshop/agentCodingTemplateService.test.ts`：不动（re-export 保持符号身份）。

- [ ] **Step 6: Typecheck + targeted tests**

Run: `npm run typecheck && npx jest test/services/botWorkshop test/services/externalBots test/components/BotWorkshop test/components/CollaborationPrivacy`
Expected: PASS + no type errors。（`test/hooks/` 下现有 `useExternal*` 均为 auth 流向，无 useExternalBots 专项单测；面板/弹窗回归由 components 两目录覆盖。）

- [ ] **Step 7: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/services/bots \
        apps/frontend-nextgen/src/services/externalBots \
        apps/frontend-nextgen/src/services/botWorkshop \
        apps/frontend-nextgen/src/hooks/useExternalBots.ts \
        apps/frontend-nextgen/src/hooks/useExpertBotCreation.ts \
        apps/frontend-nextgen/src/components/BotWorkshop/ExternalBotPanel.tsx \
        apps/frontend-nextgen/test/services
git commit -m "refactor(frontend-nextgen): relocate external-bot service chain out of the bot-workshop service tree"
```

---

### Task 11: Standalone 外部 Bot page (ExternalBotPanel relocation + route meta)

**Files:**
- Move: `src/components/BotWorkshop/ExternalBotPanel.tsx` → `src/components/ExternalBots/ExternalBotPanel.tsx`（其 `externalBotService` import 已在 Task 10 改指 `services/externalBots`，本步不再触碰）
- Modify: `src/pages/BotWorkshop/index.tsx:11` (import path)
- Create: `src/pages/ExternalBots/index.tsx`
- Modify: `src/shell/routeMeta.ts` (baseline `routeMetaList`)
- Test: `test/pages/ExternalBots.test.tsx` (create)

- [ ] **Step 1: Write the failing page test**

```tsx
/** @jest-environment jsdom */
import { describe, expect, it, jest } from '@jest/globals';
import { render, screen } from '@testing-library/react';

jest.mock('@/components/ExternalBots/ExternalBotPanel', () => ({
  ExternalBotPanel: () => <div data-testid="external-bot-panel" />,
}));

import ExternalBotsPage from '@/pages/ExternalBots';

describe('ExternalBotsPage', () => {
  it('渲染薄壳：页头 + 复用的外部 Bot 面板', () => {
    render(<ExternalBotsPage />);
    expect(screen.getByText('外部 Bot')).toBeTruthy();
    expect(screen.getByTestId('external-bot-panel')).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/pages/ExternalBots.test.tsx`
Expected: FAIL — `@/pages/ExternalBots` not found.

- [ ] **Step 3: Move the panel**

```bash
mkdir -p src/components/ExternalBots
git mv src/components/BotWorkshop/ExternalBotPanel.tsx src/components/ExternalBots/ExternalBotPanel.tsx
```

Update its sole external importer, `src/pages/BotWorkshop/index.tsx` line 11:

```tsx
import { ExternalBotPanel } from '@/components/ExternalBots/ExternalBotPanel';
```

- [ ] **Step 4: Create the thin page**

`src/pages/ExternalBots/index.tsx`:

```tsx
import { PageHeader } from '@/components/Common/PageHeader';
import { ExternalBotPanel } from '@/components/ExternalBots/ExternalBotPanel';

/**
 * collab 形态的外部 Bot 管理页（/external-bots）：复用 Bot 管理「外部 Bot」面板，
 * 不经 BotWorkshop 页壳（避免拖入 useBotWorkshop 全栈）。数据全部来自 collaboration 接口。
 */
export default function ExternalBotsPage(): JSX.Element {
  return (
    <main className="app-scrollbar h-full overflow-y-auto bg-page-canvas">
      <div className="w-full p-5 sm:p-6">
        <PageHeader title="外部 Bot" description="接入、查看和配置当前空间内的外部 Bot" />
        <section className="mt-5 rounded-xl border border-border/60 bg-card p-4 sm:p-6">
          <ExternalBotPanel active />
        </section>
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Add the route meta**

In `src/shell/routeMeta.ts` baseline `routeMetaList`, add after the `/bot-workshop` entry:

```ts
  // collab 形态一级页（Open 形态路由表不含该路径，meta 在 Open 下惰性无害）。
  {
    path: '/external-bots',
    title: '外部 Bot',
    section: 'bot',
    navKey: 'external-bots',
    openCore: true,
  },
```

(Confirm the exact `/bot-workshop` meta entry location with `grep -n "bot-workshop" src/shell/routeMeta.ts` first.)

- [ ] **Step 6: Run page test + typecheck**

Run: `npx jest test/pages/ExternalBots.test.tsx && npm run typecheck`
Expected: PASS + clean.

- [ ] **Step 7: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/components/ExternalBots \
        apps/frontend-nextgen/src/components/BotWorkshop/ExternalBotPanel.tsx \
        apps/frontend-nextgen/src/pages/BotWorkshop/index.tsx \
        apps/frontend-nextgen/src/pages/ExternalBots \
        apps/frontend-nextgen/src/shell/routeMeta.ts \
        apps/frontend-nextgen/test/pages/ExternalBots.test.tsx
git commit -m "feat(frontend-nextgen): add standalone external-bots page reusing the workshop panel"
```

---

### Task 12: `src/extensions/collab.ts` assembly

**Files:**
- Modify: `src/extensions/contract.ts` (no change needed if Task 3 landed; verify)
- Create: `src/extensions/collab.ts`
- Test: `test/extensions/collab.test.ts` (create)

- [ ] **Step 1: Write the failing assembly test**

```ts
import { describe, expect, it } from '@jest/globals';
import { appExtension, registerSidePanelWiring } from '@/extensions/collab';
import { getExtensionSlots } from '@/extensions/slots';

describe('collab 装配', () => {
  it('导航覆盖为「协作群 + 外部 Bot」两项', () => {
    const items = appExtension.capabilities.getPrimaryNavigationItems().value;
    expect(items?.map((item) => [item.id, item.path])).toEqual([
      ['collaboration', '/workspace/collaboration'],
      ['external-bots', '/external-bots'],
    ]);
  });

  it('壳层可见性覆盖：隐藏空间切换/通知中心/全局搜索', () => {
    expect(appExtension.capabilities.getShellVisibility().value).toEqual({
      spaceSwitcher: false,
      notificationBell: false,
      globalSearch: false,
    });
    expect(appExtension.capabilities.getSpaceContextInitializationEnabled().value).toBe(false);
  });

  it('槽位整体为 null（任务栈/TC 选路不进 collab 产物）', () => {
    expect(getExtensionSlots()).toEqual({
      groupTasks: null,
      groupServiceBotRouting: null,
      taskSessionNavigationObserver: null,
      taskPanelBootstrap: null,
    });
    expect(appExtension.avatarEditor).toBeNull();
  });

  it('registerSidePanelWiring 为可调用函数（UMD 轨保留，任务轨缺席）', () => {
    expect(typeof registerSidePanelWiring).toBe('function');
  });
});
```

Note: this test imports the collab file directly; after it runs, the slot registry is nulled for the rest of that jest worker's module registry, so keep the file dedicated to collab assertions (other test files get fresh module registries).

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/extensions/collab.test.ts`
Expected: FAIL — `@/extensions/collab` not found.

- [ ] **Step 3: Create the collab assembly**

`src/extensions/collab.ts`:

```ts
// collab（协作群极简站）装配：capabilities 覆盖 + 副屏接线精简（保留 UMD 轨，无任务面板轨）。
// 本文件绝不 import 任务栈/TC 选路实现——槽位置 null，相关源码不进 collab 产物。
// 形态差异只许出现在本文件、config/routes.collab.ts 与 config/config.ts 的 FRONTEND_FORM 分支。
import { defaultCapabilities } from '@/capabilities';
import type { AppCapabilities, CapabilityResult, ShellVisibility } from '@/capabilities';
import { registerUmdPanelHandler } from '@/services/bcs/UmdPanel';
import '@/services/workspace/chatBridge';
import { ensureReactGlobal } from '@/services/workspace/ensureReactGlobal';
import type { NavigationItem } from '@/shell/navigation';
import * as SidePanelApi from '@tc-chat/ui/es/SidePanel';
import { PlugZap, Users } from 'lucide-react';
import type { AppExtension } from './contract';
import { registerExtensionSlots } from './slots';

type SidePanelConfigureFn = (opts: { maxTabLabelLength?: number }) => void;

const collabNavigationItems: NavigationItem[] = [
  {
    id: 'collaboration',
    label: '协作群',
    path: '/workspace/collaboration',
    icon: Users,
    section: 'collab',
    description: '与用户和 Bot 即时协作',
  },
  {
    id: 'external-bots',
    label: '外部 Bot',
    path: '/external-bots',
    icon: PlugZap,
    section: 'bot',
    description: '接入、查看和配置外部 Bot',
  },
];

const collabCapabilities: AppCapabilities = {
  ...defaultCapabilities,
  getSpaceContextInitializationEnabled: () => ({ status: 'available', value: false }),
  getPrimaryNavigationItems: (): CapabilityResult<NavigationItem[] | null> => ({
    status: 'available',
    value: collabNavigationItems,
  }),
  getShellVisibility: (): CapabilityResult<ShellVisibility> => ({
    status: 'available',
    value: { spaceSwitcher: false, notificationBell: false, globalSearch: false },
  }),
};

/**
 * 副屏 SDK 能力装配（collab）：chatBridge（AixUI panel 消息桥）+ React 全局兜底 +
 * UMD 轨（BCS manifest CDN 面板）。任务轨（registerTaskPanel/TaskLoopCard/TaskPanelAdapter）
 * 缺席：任务副屏内容在本形态不会产生，对应 UMD 资产与 styled-components 依赖不进产物。
 */
export function registerSidePanelWiring(): void {
  const configureSidePanel = Reflect.get(SidePanelApi, 'configureSidePanel') as SidePanelConfigureFn | undefined;
  if (typeof configureSidePanel === 'function') {
    configureSidePanel({ maxTabLabelLength: 10 });
  }
  ensureReactGlobal();
  registerUmdPanelHandler();
}

// 槽位显式置空：任务栈/TC 选路/任务会话观察者/任务副屏 bootstrap 不进 collab 产物。
registerExtensionSlots({
  groupTasks: null,
  groupServiceBotRouting: null,
  taskSessionNavigationObserver: null,
  taskPanelBootstrap: null,
});

export const appExtension: AppExtension = {
  avatarEditor: null,
  capabilities: collabCapabilities,
};
```

Also confirm `src/extensions/index.ts` keeps re-exporting from `./empty` only (its facade role is unchanged; the alias selects the per-form file).

- [ ] **Step 4: Run the assembly test**

Run: `npx jest test/extensions/collab.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/src/extensions/collab.ts \
        apps/frontend-nextgen/test/extensions/collab.test.ts
git commit -m "feat(frontend-nextgen): add collab extension assembly"
```

---

### Task 13: `config/routes.collab.ts` + `FRONTEND_FORM` build switch

**Files:**
- Create: `config/routes.collab.ts`
- Modify: `config/config.ts` (lines 5, 10-13)
- Modify: `package.json` (scripts)
- Test: `test/routes/collabRoutes.test.ts` (create)

- [ ] **Step 1: Write the failing route test**

```ts
// collab 形态路由表契约（纯数据断言，node 环境）：白名单内路由 + 默认入口 + 404 兜底。
import { describe, expect, it } from '@jest/globals';
import { routes } from '../../config/routes.collab';

interface RouteEntry {
  path: string;
  component?: string;
  redirect?: string;
}

const topLevel: RouteEntry[] = routes as RouteEntry[];
const appLayoutEntry = topLevel.find((route) => Array.isArray((route as { routes?: unknown }).routes)) as {
  routes: RouteEntry[];
};
const appLayoutRoutes = appLayoutEntry.routes;
const findRoute = (path: string): RouteEntry | undefined => appLayoutRoutes.find((route) => route.path === path);

describe('collab 形态路由表', () => {
  it('默认入口重定向到协作群', () => {
    expect(topLevel.find((route) => route.path === '/')).toEqual({
      path: '/',
      redirect: '/workspace/collaboration',
    });
    expect(findRoute('/workspace')).toEqual({ path: '/workspace', redirect: '/workspace/collaboration' });
  });

  it('协作群主路由与聚焦深链注册', () => {
    expect(findRoute('/workspace/collaboration')).toEqual({
      path: '/workspace/collaboration',
      component: '@/pages/Workspace/Collaboration',
    });
    for (const path of ['/workspace/collaboration-only', '/workspace/collaboration/group', '/workspace/collaboration/session']) {
      expect(findRoute(path)?.component).toBe('@/pages/Workspace/Collaboration/Focused');
    }
  });

  it('邀请/BCN 深链与外部 Bot 页注册', () => {
    expect(findRoute('/workspace/invite/:type/:token')?.component).toBe('@/pages/Workspace/InviteAcceptPanel');
    expect(findRoute('/workspace/bcn/chat/detail')?.component).toBe('@/pages/Workspace/BcnChatDetail');
    expect(findRoute('/external-bots')).toEqual({ path: '/external-bots', component: '@/pages/ExternalBots' });
  });

  it('白名单：不含单聊/BotWorkshop/Lab/进化工厂/管理域/欢迎页', () => {
    const allPaths = appLayoutRoutes.map((route) => route.path);
    const forbidden = [
      '/workspace/chat',
      '/bot-workshop',
      '/collaboration-square',
      '/lab',
      '/evolution-factory',
      '/work/my-task',
      '/space-admin',
      '/ticket-center',
      '/components',
      '/private-chat/chat',
    ];
    for (const prefix of forbidden) {
      expect(allPaths.some((path) => path === prefix || path?.startsWith(`${prefix}/`))).toBe(false);
    }
    expect(topLevel.some((route) => route.path === '/welcome' || route.path === '/design-system')).toBe(false);
  });

  it('404 兜底为 NotFound', () => {
    expect(findRoute('*')).toEqual({ path: '*', component: '@/pages/NotFound' });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx jest test/routes/collabRoutes.test.ts`
Expected: FAIL — `../../config/routes.collab` not found.

- [ ] **Step 3: Create the collab route table**

`config/routes.collab.ts`:

```ts
// collab（协作群极简站）路由表：FRONTEND_FORM=collab 时整表替换 config/routes.ts。
// 与 internal overlay 的覆盖语义无关——本表是独立装配形态的全量路由（不含 design-system/welcome）。
export const routes = [
  // 默认入口：协作群。
  { path: '/', redirect: '/workspace/collaboration' },
  {
    path: '/',
    component: '@/layouts/AppLayout',
    routes: [
      { path: '/workspace', redirect: '/workspace/collaboration' },
      { path: '/workspace/collaboration', component: '@/pages/Workspace/Collaboration' },
      // 聚焦/嵌入深链（无全局导航）原样保留。
      { path: '/workspace/collaboration-only', component: '@/pages/Workspace/Collaboration/Focused' },
      { path: '/workspace/collaboration/group', component: '@/pages/Workspace/Collaboration/Focused' },
      { path: '/workspace/collaboration/session', component: '@/pages/Workspace/Collaboration/Focused' },
      // 邀请加群链路与 BCN 外部协作会话外链落地页保留。
      { path: '/workspace/invite/:type/:token', component: '@/pages/Workspace/InviteAcceptPanel' },
      { path: '/workspace/bcn/chat/detail', component: '@/pages/Workspace/BcnChatDetail' },
      // 外部 Bot 管理（Bot 管理「外部 Bot」面板的独立薄页面）。
      { path: '/external-bots', component: '@/pages/ExternalBots' },
      // 404 兜底：本形态不含的页面（单聊/BotWorkshop/Lab/…）统一落到 NotFound。
      { path: '*', component: '@/pages/NotFound' },
    ],
  },
];
```

- [ ] **Step 4: Wire the build switch in config.ts and the scripts**

In `config/config.ts`:

- Replace line 5 (`import { routes } from './routes';`) and the config head with:

```ts
import tailwindcss from '@tailwindcss/postcss';
import { defineConfig } from '@umijs/max';
import path from 'path';
import { a2aClientBuildDefault } from './a2aBuildDefault';
import { routes as openRoutes } from './routes';
import { routes as collabRoutes } from './routes.collab';

// 装配形态开关：open（默认，Open Core 全量）/ collab（协作群极简站）。
// 仅切换路由表（产物页面集）与 @/extensions 装配别名；业务组件内不出现形态分支。
const frontendForm = process.env.FRONTEND_FORM === 'collab' ? 'collab' : 'open';

const bcsEndpointPre = process.env.BCS_ENDPOINT_PRE || 'http://127.0.0.1:21000';
const bcsEndpointProd = process.env.BCS_ENDPOINT_PROD || 'http://127.0.0.1:21000';

export default defineConfig({
  routes: frontendForm === 'collab' ? collabRoutes : openRoutes,
  alias: {
    // 保持 Task 3 锁定的精确匹配键 `$`（组件以 `@/extensions/slots`、`@/extensions/contract` 子路径直连注册表/契约）。
    '@/extensions$': path.resolve(
      __dirname,
      frontendForm === 'collab' ? '../src/extensions/collab' : '../src/extensions/empty',
    ),
```

(The `@avernet/a2a-chat-client` alias entries follow unchanged, and the rest of the config object is untouched.)

In `package.json` scripts, add after `"build": "max build",`:

```json
    "dev:collab": "cross-env FRONTEND_FORM=collab max dev",
    "build:collab": "cross-env FRONTEND_FORM=collab max build",
    "check:collab-closure": "node scripts/check-collab-closure.cjs",
```

(`cross-env` is already used by `dev:local`; the closure script is created in Task 14 — add the script entry there if you prefer one diff here and one diff there; simplest is to add all three entries now and let Task 14 create the file.)

- [ ] **Step 5: Run route tests + existing route suite**

Run: `npx jest test/routes`
Expected: all PASS (base `config/routes.ts` untouched).

- [ ] **Step 6: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/config/routes.collab.ts \
        apps/frontend-nextgen/config/config.ts \
        apps/frontend-nextgen/package.json \
        apps/frontend-nextgen/test/routes/collabRoutes.test.ts
git commit -m "feat(frontend-nextgen): add collab form route table and FRONTEND_FORM build switch"
```

---

### Task 14: AST closure guard, negative fixtures and CI wiring

**Files:**
- Create: `scripts/check-collab-closure.cjs`
- Modify: `package.json` (`ci` chain)
- Create: `scripts/check-collab-closure.test.cjs` (Node fixture tests, executed in CI)

- [ ] **Step 1: Create the closure crawler**

`scripts/check-collab-closure.cjs`:

```js
#!/usr/bin/env node
// Runtime source closure: static imports/re-exports, import() and require().
// Type-only edges are erased. Missing local code and opaque bundled loads fail.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const FORBIDDEN = [
  'src/pages/BotWorkshop/',
  'src/pages/Lab',
  'src/pages/EvolutionFactory/',
  'src/pages/CollaborationSquare/',
  'src/pages/Admin/',
  'src/pages/SpaceAdmin/',
  'src/pages/TicketCenter/',
  'src/pages/Welcome/',
  'src/pages/MyTask/',
  'src/pages/ScheduledTasks/',
  'src/pages/Demo/',
  'src/pages/ModuleOverview/',
  'src/pages/ComponentExamples/',
  'src/components/BotWorkshop/',
  'src/components/TaskCards/',
  'src/components/Workspace/TaskComposerMenu/',
  'src/components/Workspace/TaskLinkedSession',
  'src/components/Workspace/TaskPanelSwitcherSlot',
  'src/services/botWorkshop/',
  'src/services/botHealthCheck/',
  'src/services/engine/',
  'src/services/workspace/groupExecuteService',
  'src/services/workspace/groupTaskAdapter',
  'src/services/backendApi/tasks/',
  'src/services/backendApi/workflows/',
  'src/services/backendApi/bots/botClassificationController',
  'src/services/workspace/botClassificationService',
  'src/assets/TaskPanel/',
  'src/hooks/useTaskExecution',
  'src/hooks/useTaskExecuteFromCard',
  'src/hooks/useWorkflowList',
  'src/hooks/useTaskSwitcherTasks',
  'src/hooks/useSessionTaskPanelBootstrap',
  'src/hooks/taskPanelNavBinding',
  'src/pages/Workspace/hooks/useGroupTaskExecution',
  'src/pages/Workspace/hooks/useManagerWorkerTasks',
  'src/pages/Workspace/hooks/useServiceBotRouting',
  'src/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks',
  'src/pages/Workspace/components/GroupChatPane/GroupTaskRow',
  'src/pages/Workspace/components/Modals/ServiceBotRoutingControl',
  // 单聊整树禁入；唯二正向复用文件经 ALLOW 豁免（spec acceptance 1 的机器可证口径）。
  'src/pages/Workspace/Chat/',
];

/** 正向复用白名单：禁入前缀下的豁免（其余 pages/Workspace/Chat 一律违例）。新增豁免 = 扩 spec，不许随手加。 */
const ALLOW = [
  'src/pages/Workspace/Chat/components/SessionHistorySearchPanel',
  'src/pages/Workspace/Chat/hooks/sidePaneContent',
];


const ASSET = /\.(?:css|less|scss|sass|svg|png|jpe?g|gif|webp|avif|woff2?|ttf|ico|json|wasm)$/i;

function runtimeImport(node) {
  const clause = node.importClause;
  if (!clause) return true; // Side-effect import.
  if (clause.isTypeOnly) return false;
  const bindings = clause.namedBindings;
  return Boolean(clause.name) || !bindings || ts.isNamespaceImport(bindings)
    || bindings.elements.length === 0 || bindings.elements.some((item) => !item.isTypeOnly);
}

function extractEdges(file, source) {
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const edges = new Set();
  const issues = [];
  const literal = (node, context) => {
    if (node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))) {
      edges.add(node.text);
    } else {
      issues.push(`${file}: nonliteral ${context} cannot prove bundled closure`);
    }
  };
  function visit(node) {
    if (ts.isImportDeclaration(node) && runtimeImport(node)) {
      literal(node.moduleSpecifier, 'import');
    } else if (ts.isExportDeclaration(node) && !node.isTypeOnly && node.moduleSpecifier) {
      const clause = node.exportClause;
      if (!clause || !ts.isNamedExports(clause) || clause.elements.length === 0
          || clause.elements.some((item) => !item.isTypeOnly)) {
        literal(node.moduleSpecifier, 're-export');
      }
    } else if (ts.isCallExpression(node)) {
      const dynamic = node.expression.kind === ts.SyntaxKind.ImportKeyword;
      const requireCall = ts.isIdentifier(node.expression) && node.expression.text === 'require';
      if (dynamic || requireCall) {
        // Detect webpackIgnore only in the argument's comment trivia.
        // Runtime URL loads need a reviewed exception; do not silently skip them.
        const trivia = node.arguments[0]
          ? source.slice(node.arguments[0].pos, node.arguments[0].getStart(ast)) : '';
        if (dynamic && /webpackIgnore\s*:\s*true/.test(trivia)) {
          issues.push(`${file}: webpackIgnore runtime import requires an explicit reviewed exception`);
        } else {
          literal(node.arguments[0], dynamic ? 'import()' : 'require()');
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return { edges: [...edges], issues };
}

function checkClosure(root) {
  const src = path.join(root, 'src');
  const configFile = ts.readConfigFile(path.join(root, 'tsconfig.json'), ts.sys.readFile);
  if (configFile.error) throw new Error(ts.flattenDiagnosticMessageText(configFile.error.messageText, '\n'));
  const config = ts.parseJsonConfigFileContent(configFile.config, ts.sys, root);
  if (config.errors.length) throw new Error(config.errors.map((e) => ts.flattenDiagnosticMessageText(e.messageText, '\n')).join('\n'));
  const failures = [];
  const visited = new Set();
  const relative = (file) => path.relative(root, file).split(path.sep).join('/');

  function resolve(specifier, from) {
    if (ASSET.test(specifier)) return null;
    // Umi-generated runtime is supplied by the build; not application source.
    if (specifier.startsWith('@@/')) return null;
    // Replay the exact facade alias; other aliases use tsconfig paths, including
    // @avernet/a2a-chat-client workspace source. Never redirect slots/contract.
    const spec = specifier === '@/extensions'
      ? path.join(src, 'extensions/collab.ts') : specifier;
    const resolved = ts.resolveModuleName(spec, from, config.options, ts.sys).resolvedModule;
    if (!resolved) throw new Error(`unresolved code import ${specifier} from ${relative(from)}`);
    if (/\.d\.(?:ts|mts|cts)$/.test(resolved.resolvedFileName)) return null;
    if (resolved.isExternalLibraryImport && resolved.resolvedFileName.includes('/node_modules/')) return null;
    return path.resolve(resolved.resolvedFileName);
  }

  const routesFile = path.join(root, 'config/routes.collab.ts');
  const routesAst = ts.createSourceFile(routesFile, fs.readFileSync(routesFile, 'utf8'), ts.ScriptTarget.Latest, true);
  const entries = ['@/layouts/AppLayout', '@/app'];
  function visitRoute(node) {
    if (ts.isPropertyAssignment(node) && node.name.getText(routesAst).replace(/['"]/g, '') === 'component') {
      if (!ts.isStringLiteral(node.initializer)) throw new Error('Route component must be a literal source entry');
      entries.push(node.initializer.text);
    }
    ts.forEachChild(node, visitRoute);
  }
  visitRoute(routesAst);
  if (entries.length === 2) throw new Error('No route component entries found');
  const queue = entries.map((spec) => ({ spec, from: routesFile, chain: [] }));
  while (queue.length) {
    const { spec, from, chain } = queue.shift();
    let file;
    try { file = resolve(spec, from); }
    catch (error) { failures.push(`${chain.join(' -> ')}: ${error.message}`); continue; }
    if (!file || visited.has(file)) continue;
    visited.add(file);
    const rel = relative(file);
    const next = [...chain, rel];
    if (FORBIDDEN.some((prefix) => rel.startsWith(prefix)) && !ALLOW.some((prefix) => rel.startsWith(prefix))) {
      failures.push(next.join(' -> '));
      continue;
    }
    const parsed = extractEdges(file, fs.readFileSync(file, 'utf8'));
    failures.push(...parsed.issues.map((issue) => `${next.join(' -> ')}: ${issue}`));
    for (const edge of parsed.edges) queue.push({ spec: edge, from: file, chain: next });
  }
  return { entryCount: entries.length, visitedCount: visited.size, failures };
}

module.exports = { checkClosure, extractEdges };
if (require.main === module) {
  try {
    const result = checkClosure(path.resolve(__dirname, '..'));
    console.log(`[collab-closure] ${result.entryCount} entries, ${result.visitedCount} files, ${result.failures.length} violations`);
    for (const failure of result.failures) console.error(`  ${failure}`);
    if (result.failures.length) process.exitCode = 1;
  } catch (error) {
    console.error(`[collab-closure] FAIL: ${error.message}`);
    process.exitCode = 1;
  }
}
```

- [ ] **Step 2: Add executable guard fixtures**

`scripts/check-collab-closure.test.cjs`:

```js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { checkClosure } = require('./check-collab-closure.cjs');

function fixture(source, extra = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'collab-closure-'));
  const files = {
    'tsconfig.json': JSON.stringify({ compilerOptions: {
      baseUrl: '.', paths: { '@/*': ['./src/*'] }, moduleResolution: 'node',
    } }),
    'config/routes.collab.ts': "export const routes = [{ component: '@/pages/Keep' }];",
    'src/app.tsx': 'export {};',
    'src/layouts/AppLayout.tsx': 'export default function AppLayout() {}',
    'src/pages/Keep.tsx': source,
    'src/pages/BotWorkshop/index.tsx': 'export interface Bot {}',
    'src/shared/allowed.ts': 'export const allowed = true;',
    ...extra,
  };
  try {
    for (const [name, content] of Object.entries(files)) {
      const file = path.join(root, name);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, content);
    }
    return checkClosure(root);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
}

for (const source of [
  "import '@/pages/BotWorkshop';",
  "const Page = lazy(() => import('@/pages/BotWorkshop'));",
  "const Page = require('@/pages/BotWorkshop');",
  "export * from '@/pages/BotWorkshop';",
  "import '@/shared/bridge';",
]) {
  test(`rejects forbidden runtime edge: ${source}`, () => {
    const result = fixture(source, { 'src/shared/bridge.ts': "export * from '@/pages/BotWorkshop';" });
    assert.ok(result.failures.some((failure) => failure.includes('src/pages/BotWorkshop/')));
  });
}

test('erases type-only edges and follows allowed dynamic modules', () => {
  const result = fixture(`
    import type { Bot } from '@/pages/BotWorkshop';
    export type { Bot as Other } from '@/pages/BotWorkshop';
    import { type Bot as Inline } from '@/pages/BotWorkshop';
    const allowed = import('@/shared/allowed');
  `);
  assert.deepEqual(result.failures, []);
});

test('rejects missing local code', () => {
  assert.ok(fixture("import '@/shared/missing';").failures.some((f) => f.includes('unresolved code import')));
});

test('rejects opaque bundled imports', () => {
  assert.ok(fixture('const page = import(target);').failures.some((f) => f.includes('nonliteral')));
});

test('unreviewed runtime URL imports cannot silently bypass the guard', () => {
  assert.ok(fixture('const page = import(/* webpackIgnore: true */ url);').failures.some((f) => f.includes('explicit reviewed exception')));
});

test('exact facade alias does not rewrite registry subpaths', () => {
  const result = fixture("import '@/extensions'; import '@/extensions/slots';", {
    'src/extensions/collab.ts': 'export {};',
    'src/extensions/slots.ts': 'export {};',
    'src/extensions/empty.ts': "import '@/pages/BotWorkshop';",
    'src/extensions/index.ts': "export * from './empty';",
  });
  assert.deepEqual(result.failures, []);
});
```

Run: `node --test scripts/check-collab-closure.test.cjs`. Every fixture must pass.
The reviewed `lazy(() => import('@/pages/BotWorkshop'))` counterexample must be
rejected. If actual kept code contains webpack-ignored runtime URL loads,
record the exact loader and its non-bundled purpose in the spec before adding
an exact exception and a matching fixture; do not globally skip dynamic loads.
Generated Umi runtime and installed external packages are outside this source
checker; the two real builds remain mandatory integration checks.

- [ ] **Step 3: Run the guard and fix what it surfaces**

Run: `node scripts/check-collab-closure.cjs --verbose`
Expected: zero violations, or diagnostic chains pointing at residual runtime edges (static or dynamic). **Fix each violation by rewiring the import; never weaken FORBIDDEN or silently skip unresolved source** — common expected leftovers and their fixes:
- A kept file importing `@/services/backendApi/tasks/...` → route through the slot registry or delete the dead import.
- `src/utils/collaborationSquare.ts` or shell modules importing forbidden pages → move the deep-link helpers behind precise imports.
- 群头部 hook / `pages/Workspace/Chat` 相关的违例 → 说明 Task 8 未落地，回到 Task 8；新增豁免须先改 spec acceptance 1 的 ALLOW 口径。
- `services/botWorkshop/*` 相关违例 → 说明 Task 10 未落地，回到 Task 10；不要为放行而删 FORBIDDEN 条目。
- `groupExecuteService` / `groupTaskAdapter` / task controller 违例 → 回到 Task 7A，检查 BCS 读模块是否仍 re-export 或 import executor；不能延后处理。
The `resolveUserId` extraction and `services/workspace/index.ts` barrel tightening
from spec §4 are required work in this loop: move `resolveUserId` into
`src/services/workspace/resolveUserId.ts`, preserve its delegation to
`resolveOpenApiUserId`, and retarget runtime callers. Replace broad workspace
imports with exact module paths before removing the single-chat re-exports.
Inspect runtime imports of `GlobalSearch` and other shell consumers as well;
hiding a component does not cut its static dependencies.

Only after the guard runs green, proceed. Iterating here is the acceptance loop for the whole pruning work.

- [ ] **Step 4: Wire into the ci chain**

In `package.json`, change the `ci` script to:

```json
    "ci": "npm run ci:guardrails && npm run typecheck && npm run lint && npm test -- --runInBand && node --test scripts/check-collab-closure.test.cjs && npm run check:collab-closure && npm run build",
```

- [ ] **Step 5: Run the full checks**

Run: `npm run check:collab-closure && npm run typecheck`
Expected: OK + no type errors.

- [ ] **Step 6: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/scripts/check-collab-closure.cjs \
        apps/frontend-nextgen/scripts/check-collab-closure.test.cjs \
        apps/frontend-nextgen/package.json
git commit -m "test(frontend-nextgen): add collab-form static closure guard"
```

---

### Task 15: Docker build-arg + CI workflow collab steps

This validates builds from the complete public repository. It does not replace
the private export's dedicated Docker build from only its output context in
Tasks 19–20; both gates are required.

**Files:**
- Modify: `Dockerfile` (after line 12)
- Modify: `.github/workflows/frontend-nextgen.yml` (after "Run frontend CI" step)
- Modify (mirror): `docker/services/frontend-nextgen.dockerfile` — inspect first (`grep -n "A2A_CLIENT_DEFAULT\|ARG\|ENV" docker/services/frontend-nextgen.dockerfile`) and add the same ARG/ENV pair.

- [ ] **Step 1: Add the build arg to the Dockerfile**

In `apps/frontend-nextgen/Dockerfile`, after the `ENV A2A_CLIENT_DEFAULT=${A2A_CLIENT_DEFAULT}` line (lines 10–11, `ARG`+`ENV` pair), add:

```dockerfile
# 装配形态（spec 2026-11-03-collab-form）：open（默认）/ collab（协作群极简站）。
# 构建协作群极简站镜像：docker build --build-arg FRONTEND_FORM=collab -f apps/frontend-nextgen/Dockerfile .
ARG FRONTEND_FORM=open
ENV FRONTEND_FORM=${FRONTEND_FORM}
```

(`RUN cd apps/frontend-nextgen && npm run build` needs no change — `config/config.ts` reads the env directly. Default `open` keeps the existing product behavior.)

Mirror the same ARG/ENV pair into `docker/services/frontend-nextgen.dockerfile` next to its `A2A_CLIENT_DEFAULT` lines.

- [ ] **Step 2: Add the collab build to the CI workflow**

In `.github/workflows/frontend-nextgen.yml`, after the `Run frontend CI` step, add:

```yaml
      - name: Build collab form
        run: npm run build:collab
```

(`npm run check:collab-closure` already runs inside `npm run ci`; the extra build step proves the second assembly compiles without re-running tests.)

- [ ] **Step 3: Verify both builds locally (the real acceptance gate)**

Run: `npm run build` then `FRONTEND_FORM=collab npx max build` (i.e. `npm run build:collab`)
Expected: both succeed. The closure guard must pass for both static and dynamic
bundled edges. Hashed chunk filenames or a grep for component display names are
not exclusion evidence; record the guard's entry count, visited count and zero
violations, plus both build results. The build proves compilability, while the
guard enforces the declared application-source import boundary.

- [ ] **Step 4: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/Dockerfile \
        docker/services/frontend-nextgen.dockerfile \
        .github/workflows/frontend-nextgen.yml
git commit -m "ci(frontend-nextgen): add FRONTEND_FORM build arg and collab build gate"
```

---

### Task 16: Docs + full-form verification

**Files:**
- Modify: `apps/frontend-nextgen/specs/2026-11-03-collab-form/spec.md` (Status line)
- Modify: `apps/frontend-nextgen/CLAUDE.md` (assembly-forms section)
- Modify: `apps/frontend-nextgen/docs/deployment.md` (add collab form section)
- Modify: `docs/singlebox-nextgen-local.md` (full-source startup and export distinction)

- [ ] **Step 1: Update spec status**

After Step 4, record only this phase's validation evidence. Keep the overall
status pending until `export-plan.md` Tasks 17–20 have also passed, then record
`implementation complete`, the date and PR/commit references. If Singlebox or
private runtime acceptance cannot run, name the unverified boundary explicitly;
neither these builds nor the public smoke alone complete the feature.

- [ ] **Step 2: Document the form in CLAUDE.md**

In `apps/frontend-nextgen/CLAUDE.md`, in the internal-overlay seams section (near the `@/extensions` alias description), add:

```markdown
- Third assembly form `collab`（协作群极简站，specs/2026-11-03-collab-form）：
  `FRONTEND_FORM=collab` switches `config/routes.collab.ts` + alias `@/extensions → src/extensions/collab`.
  Form differences live only in the route table, the extension assembly, and the config switch;
  in-page pruning goes through `src/extensions/slots.ts` (collab registers null slots).
  Guard: `npm run check:collab-closure` must stay green.
- Avernet 保留完整 nextgen 源码与默认 Open 路由，Singlebox 使用
  `FRONTEND_VARIANT=nextgen FRONTEND_FORM=open`。内部协作站由 OCB nextgen
  专用装配导出至独立目录；导出不覆盖本目录、不调用或复制 `apps/frontend`。
```

- [ ] **Step 3: Document deployment**

Append to `apps/frontend-nextgen/docs/deployment.md`:

```markdown
## Collab form（协作群极简站）

构建：`docker build --build-arg FRONTEND_FORM=collab -f apps/frontend-nextgen/Dockerfile .`
（本地：`npm run build:collab` / `npm run dev:collab`。）

以上为完整公共源码上的 collab 构建。独立内部站点使用 OCB nextgen 的
`export:collab` 产物及其专用 Dockerfile，具体契约见本 spec 的 `export-plan.md`。
公共源码及 Singlebox 默认 Open 产品不因导出而裁剪，旧 `apps/frontend` 不参与。

镜像形态与默认一致（同一 nginx 模板）；仅页面装配不同：协作群 + 外部 Bot 两个一级页。
必需 upstream 环境变量不变；通过本形态的网络验收后，以下 upstream 可指向有效且可解析的本地占位（如 `http://127.0.0.1:9`）：`TASK_ENGINE_UPSTREAM`、
`CLAWWEB_UPSTREAM`、`PRIVATE_CHAT_*_UPSTREAM`、`AIXCORE_UPSTREAM`、`TEAMCLAW_ADMIN_UPSTREAM`
（collab 通过 capability 禁止空间初始化，通过槽位移除 execute 建群；不是请求失败后降级）。
不要用任意不可解析域名占位，否则 nginx 可能无法启动。
```

- [ ] **Step 4: Full verification run (both forms)**

Run in `apps/frontend-nextgen`:
1. `npm run ci` — guardrails, typecheck, lint, full jest, Open build: all green.
2. `npm run build:collab` — collab build green.
3. `npm run check:collab-closure --verbose` style: `node scripts/check-collab-closure.cjs --verbose` — print stats for the record.
4. Manual smoke (document results in the PR description): `npm run dev:collab` against the singlebox gateway → login → `/` lands on `/workspace/collaboration` → sidebar shows exactly 协作群/外部 Bot, no search icon, no bell → create group, add external bot, send message, open/close side panel → `/external-bots` opens the panel and the registration dialog → invite/focused deep links render; `/workspace/chat` renders NotFound.
5. Exercise retained state-machine creation with an eligible Bot identity:
   YAML validation, binding and preview work; execute checkbox is absent;
   creation uses the BCS group endpoint. Select an image from the input menu,
   paste an image, and upload a file with task slots null.
6. Record a sanitized network trace from authenticated cold entry through
   `/external-bots` and the above creation flow. Assert no task execute,
   workflows, spaces or work-order requests. Capture path/method/count only,
   never cookies, tokens or request bodies. Point unused upstreams at the
   documented placeholder and confirm no corresponding error notifications.
7. Run `npm run check:open-core` and check every added/modified source file's
   line count (≤1,000, plus module-specific guardrails). Only after all required
   checks pass, record this phase's evidence and continue with Tasks 17–20.

In `docs/singlebox-nextgen-local.md`, retain the existing nextgen startup
commands and document `FRONTEND_FORM=open` for full-product regression. Explain
that the private export is a separate artifact and never becomes the public
Singlebox source directory. Task 20 supplies the actual startup evidence.

- [ ] **Step 5: Commit (needs user confirmation)**

```bash
git add apps/frontend-nextgen/specs/2026-11-03-collab-form \
        apps/frontend-nextgen/CLAUDE.md \
        apps/frontend-nextgen/docs/deployment.md
git commit -m "docs(frontend-nextgen): document collab assembly form"
```

---

## Required continuation: Tasks 17–20

Execute `export-plan.md` in order after this phase. Public source changes from
Task 17 must pass full Open CI again; the private tasks are implemented in OCB's
nextgen module and verified there. Do not mark them complete merely because
they are documented here or because the public bundle is already minimal.

## Spec coverage checklist (self-review result)

| Spec section | Tasks |
| --- | --- |
| §1 Form switch (FRONTEND_FORM, route table, alias, scripts, open-core scan) | T13, T14, T15；alias `$` 键提前在 T3 锁定。check-open-core 只校验固定 forbidden 路径与 package.json 正则（不扫全树），新增文件均为 Open Core 资产，天然通过，无需改动 |
| §2 Route table | T13 (+ whitelist test) |
| §3 Navigation/shell (getPrimaryNavigationItems, globalSearch, space initialization gate) | T1, T2, T2A, T12 |
| §4 Slot pruning, nullable avatar consumer, attachment fallback, BCS-read/executor split, service relocations | T3–T10 including T7A; acceptance guard T14 |
| §5 Runtime surface | T2A/T7A consumer tests + T16 explicit network evidence; not inferred from import closure |
| Deployment (build-arg, nginx unchanged) | T15, T16 |
| Acceptance criteria 1–8 | T14 (1), T16 (2–8), T1/T2/T3 (6), T2A/T5/T7A (3, 8) |
| Full public source retained; independent nextgen-only artifact | T17–T19; source inventory and isolation tests |
| Acceptance criteria 9–11 | T19 (9), T20 (10–11); full Singlebox plus isolated export/deployment |
| Required paired private assembly | T18–T20 in OCB; full internal regression, exact alias and private collab entry |

## Deferred items (explicitly out of this plan, per spec)

- Slimmed collab nginx template / reduced env contract.

## Risk notes for the executor

- **Import cycles:** never import real slot implementations from components or from `slots.ts`; only assembly files (`empty.ts`/`collab.ts`/test setup) may statically import them.
- **Hook rule:** slot-resolved hooks (`useResolvedGroupTaskExecution`, `useResolvedServiceBotRouting`, `useResolvedTaskPanelBootstrap`) are module-level constants per build — do not resolve them inside render bodies.
- **Alias exactness:** the `@/extensions` facade alias must keep its `$` (Task 3 locks it, Task 13 preserves it in the form ternary). Without it, every `@/extensions/slots` import fails `max build` while jest stays green. The internal repo's overlay repointing needs the same `$`.
- **Slot completeness:** `registerExtensionSlots` takes the full `AppExtensionSlots` object; `GroupTasksExtension` includes `createGroupViaExecute`. Update every real registration and test fixture together. Typecheck enforces this; never widen a site with a partial literal.
- **Runtime boundary:** hidden navigation or a caught error does not mean zero requests. Preserve the explicit space/execute gates and the Task 16 network assertions.
- **Test isolation:** `test/extensions/collab.test.ts` nulls the registry for its worker; do not import other component suites from that file.
