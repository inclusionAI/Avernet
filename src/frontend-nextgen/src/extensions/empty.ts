import { registerTaskPanel, TaskPanelAdapter } from '@/assets/TaskPanel';
import { defaultCapabilities, getCapabilities } from '@/capabilities';
import { TaskLoopCard } from '@/components/TaskCards';
import { registerUmdPanelHandler } from '@/services/bcs/UmdPanel';
import { resolveTaskApiBase } from '@/services/tasks/taskConfig';
import { ensureReactGlobal } from '@/services/workspace';
import '@/services/workspace/chatBridge';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import * as SidePanelApi from '@tc-chat/ui/es/SidePanel';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import React, { type ComponentType } from 'react';

type SidePanelConfigureFn = (opts: { maxTabLabelLength?: number }) => void;

/**
 * 任务副屏登录用户工号注入（业务层装配 wrapper）。
 *
 * 协作群后端 opening_message.params 不下发 userId，而副屏「节点下钻」需要「当前登录人工号」：
 * - 协作群消息查询：在群成员中找归属本人的 bot 作 view_bot_id（actor_id 末段 === 登录人工号）。
 * - 跨用户单聊：会话归属人 ≠ 登录人 时置无权限提示。
 *
 * assets 守卫禁止 TaskPanelAdapter 反查 teamclaw 业务层（stores/capabilities/services），故在此 wrapper 注入：
 * - userId：从登录态 human identity 取纯工号塞入 params.userId；params 已带（发起方/后端注入）优先沿用，缺省才回填登录人工号。
 * - taskApiBase：task API 路径前缀由 capability getTaskApiBase 解析（Open Core → /openapi/v1/collaboration/tasks、
 *   内部 overlay → /api/v1/collaboration/tasks），渲染期注入而不落库到持久 params——部署路由随环境变化，
 *   渲染期取当前 capability 最准，旧副屏消息切环境后仍命中正确路由。
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
  // userId：已带沿用，缺省回填登录人工号；taskApiBase：capability 渲染期注入（部署路由随环境取最准）。
  const params = {
    ...incoming,
    userId: incoming.userId ?? loginUserId,
    taskApiBase: resolveTaskApiBase(),
  };
  return React.createElement(TaskPanelAdapter, { ...props, params } as PanelContentProps);
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
 *   随后业务层覆盖为 TaskPanelAdapterWithUser，注入登录用户工号（assets 守卫不能反查业务层）。
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

export const appExtension = {
  capabilities: defaultCapabilities,
};
