import { createGroupTaskHistoryReader } from '@/services/tasks/panel/taskPanelHistoryAdapter';
import { registerTaskPanel } from '@/services/tasks/panel/registerTaskPanel';
import { TaskPanelAdapter } from '@/components/Workspace/TaskPanelAdapter';
import { defaultCapabilities, getCapabilities } from '@/capabilities';
import { TaskLoopCard } from '@/components/TaskCards';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import { useSessionTaskPanelBootstrap } from '@/hooks/useSessionTaskPanelBootstrap';
import { useSessionTaskPanelTab } from '@/hooks/useSessionTaskPanelTab';
import { ManagerWorkerTasks } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import { ServiceBotRoutingControl } from '@/pages/Workspace/components/Modals/ServiceBotRoutingControl';
import { useGroupTaskExecution } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import { useServiceBotRouting } from '@/pages/Workspace/hooks/useServiceBotRouting';
import { registerUmdPanelHandler } from '@/services/bcs/UmdPanel';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { buildTaskInstructions } from '@/services/tasks/taskMapper';
import { ensureReactGlobal } from '@/services/workspace/ensureReactGlobal';
import { createGroupViaExecute } from '@/services/workspace/groupExecuteService';
import '@/services/workspace/chatBridge';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import * as SidePanelApi from '@tc-chat/ui/es/SidePanel';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import React, { type ComponentType, useMemo } from 'react';
import type { SessionTaskPanelTabHook, TaskPanelBootstrapHook } from './contracts/taskPanelBootstrap';
import { registerExtensionSlots } from './slots';
import type {
  AppExtension,
  GroupServiceBotRoutingExtension,
  GroupTasksExtension,
} from './contract';

type SidePanelConfigureFn = (opts: { maxTabLabelLength?: number }) => void;

/**
 * 任务副屏登录用户工号注入（业务层装配 wrapper）。
 *
 * 协作群后端 opening_message.params 不下发 userId，而副屏「节点下钻」需要「当前登录人工号」：
 * - 协作群消息查询：在群成员中找归属本人的 bot 作 view_bot_id（actor_id 末段 === 登录人工号）。
 * - 跨用户单聊：会话归属人 ≠ 登录人 时置无权限提示。
 *
 * 宿主 wrapper 负责产品身份与 API 配置，TaskPanelAdapter 仅转换 SDK props：
 * - userId：从登录态 human identity 取纯工号塞入 params.userId；params 已带（发起方/后端注入）优先沿用，缺省才回填登录人工号。
 * - taskApiBase：副屏的 dashboard/list 读接口统一走 /openapi/v1/collaboration/tasks 读公开面
 *   （前端经 gateway spanner 鉴权），不随 capability 切到内面 /api/v1——对齐后端安全收敛（删除非 openapi 接口，
 *   仅保留公开面，杜绝公网出口上内部 /api/v1 裸奔）。渲染期注入而不落库到持久 params，旧副屏消息仍命中正确路由。
 *
 * 用 getHumanIdentity 而非 getCurrentOpenApiUserId：后者依赖 activeIdentityId，群聊副屏以 bot 身份
 * 渲染时取不到工号；前者直接从 workspaceStore.identities 取 kind=user 的登录人，不受当前身份影响。
 */
const TaskPanelAdapterWithUser: ComponentType<PanelContentProps> = (props) => {
  // 订阅 identities：登录态变化时重渲染（getHumanIdentity 同步读 store）。
  useWorkspaceStore((s) => s.identities);
  const human = getCapabilities().getHumanIdentity();
  const loginUserId = human.status === 'available' ? human.value?.userId?.trim() || undefined : undefined;
  const incoming = props.params ?? {};
  const params = {
    ...incoming,
    userId: incoming.userId ?? loginUserId,
    taskApiBase: TASK_READ_API_BASE,
  };
  // V2 多任务切换条:listTasks 数据 + openPanelTab 幂等 upsert 切换(内部无任务时自渲染 null)
  return React.createElement(TaskPanelAdapter, { ...props, params });
};

/**
 * 副屏 SDK 能力装配（Open Core 默认）。
 *
 * 引擎 SDK 侧三轨中，Open Core 装配：
 * - 导入 `@/services/workspace/chatBridge`（模块级副作用）：实例化全局单例 ChatBridge
 *   （installGlobal:true → 写 window.aixBridge + chatBridgeHelper.set('main')），使任意会话页挂载前
 *   window.aixBridge 就位。aixcore 卡片沙箱（方式① AixUIRenderer→ReactRender）从此变量取真桥，
 *   避免退化成 {}（见 services/workspace/chatBridge.ts 注释）。
 * - ensureReactGlobal()：兜底 window.React/ReactDOM，供方式②/第三轨 external-React UMD 消费
 * - registerUmdPanelHandler()：注册方式② CDN UMD 加载器（registerPanelContent('umd', UmdPanel)），
 *   接管 type='umd' tab，修复引擎 resolveBusinessEntry entry 丢 libraryName 前缀的缺陷
 *   （`<AixUI component="lib.Comp">` → finalType='umd' → UmdPanel 按 _componentKey 重建点路径导出）
 * - registerTaskPanel()：第三轨任务协作 workflow 副屏本地注册（taskPanel.TaskLoopView → TaskPanelAdapter；legacy 别名 task-loop 兜底旧落库消息）
 *   随后业务层覆盖为 TaskPanelAdapterWithUser，注入登录用户工号，展示仍由共享资产负责。
 *
 * 方式②数据桥（BCS manifest / Bot render screens → window.aixLibraryCdnMap）见 services/bcs/libraryCdnInjector，
 * 由 useGroupChat / useBotChat 进入会话时触发 queryAndRegister*() 接入。
 * 方式①卡片市场（@alipay/tc-chat-extensions）仅内源版，由 extensions/internal.ts 追加，Open Core 不 import。
 */
export function registerSidePanelWiring(): void {
  // 副屏 tab 标签截断(opt-in)。configureSidePanel 由 @tc-chat/ui 提供:
  // 用 namespace + 运行时存在性判断做前向兼容——该 API 尚未发布到 dev 版时安全跳过,
  // SDK 发版后自动生效,业务代码无需再改。SDK 默认不截断 = 对其它消费方零影响。
  const configureSidePanel = Reflect.get(SidePanelApi, 'configureSidePanel') as SidePanelConfigureFn | undefined;
  if (typeof configureSidePanel === 'function') {
    configureSidePanel({ maxTabLabelLength: 10 });
  }
  // chatBridge 经顶部 import 副作用实例化（早于下方注册），此处无需显式调用，仅注释说明时序。
  ensureReactGlobal();
  registerUmdPanelHandler();
  registerTaskPanel();
  // Open Core task-loop skill card: public component key replaces the internal cardId loader.
  registerPanelContent('taskCard.TaskLoopCard', TaskLoopCard as ComponentType<PanelContentProps>);
  // 业务层覆盖注入登录用户工号：协群后端 opening_message.params 不带 userId，副屏下钻需登录人工号
  // 判定群成员归属本人 bot（view_bot_id）与跨用户单聊权限；缺省回填，已有 userId 则沿用。
  registerPanelContent('taskPanel.TaskLoopView', TaskPanelAdapterWithUser);
  registerPanelContent('task-loop', TaskPanelAdapterWithUser);
}


// Open Core 槽位注册：任务栈/TC 选路的真实实现（组件经 slots.ts 解析，import 单侧无环）。
// 四个槽位族全部经类型显式注解（contracts/*，collab-form Task 17 硬化）：实现签名漂移时在
// 具名常量处 typecheck 失配，而非深嵌在 registerExtensionSlots 的整包字面量里难以定位。
// collab 形态（Task 12）注册全 null 槽位，任务栈代码不进其 import graph 与产物。
const openGroupTasksSlots: GroupTasksExtension = {
  createGroupViaExecute,
  useGroupTaskExecution,
  ComposerCapabilitiesMenu,
  buildTaskInstructions,
  TaskLinkedSession,
  ManagerWorkerTasks,
};
const openGroupServiceBotRoutingSlots: GroupServiceBotRoutingExtension = {
  useServiceBotRouting,
  ServiceBotRoutingControl,
};
// 任务会话联动观察者按槽位形状双边注解；任务副屏 bootstrap 直接以契约 hook 名注解（contracts/taskPanelBootstrap）。
const openTaskSessionNavigationObserver = null;
const openTaskPanelBootstrap: TaskPanelBootstrapHook = (options) => {
  const history = useMemo(() => createGroupTaskHistoryReader(null), [options.sessionId]);
  return useSessionTaskPanelBootstrap({ ...options, kind: 'group', history });
};
const openSessionTaskPanelTab: SessionTaskPanelTabHook = useSessionTaskPanelTab;
registerExtensionSlots({
  groupTasks: openGroupTasksSlots,
  groupServiceBotRouting: openGroupServiceBotRoutingSlots,
  taskSessionNavigationObserver: openTaskSessionNavigationObserver,
  taskPanelBootstrap: openTaskPanelBootstrap,
  sessionTaskPanelTab: openSessionTaskPanelTab,
});

export const appExtension: AppExtension = {
  avatarEditor: React.lazy(() => import('@/components/BotWorkshop/BotAvatar/AvatarEditor')),
  capabilities: defaultCapabilities,
};
