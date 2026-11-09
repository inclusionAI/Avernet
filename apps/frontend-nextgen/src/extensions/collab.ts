// collab（协作群极简站）装配：capabilities 覆盖 + 副屏接线精简（保留 UMD 轨，无任务面板轨）。
// 本文件绝不 import 任务栈/TC 选路实现——槽位置 null，相关源码不进 collab 产物。
// 形态差异只许出现在本文件、config/routes.collab.ts 与 config/config.ts 的 FRONTEND_FORM 分支。
import type { AppCapabilities, CapabilityResult, ShellVisibility } from '@/capabilities';
import { defaultCapabilities } from '@/capabilities';
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

// 未列出的 capability 一律沿用 Open Core 默认（spread 继承）；新增能力需复核 collab 经营面是否收纳。
const collabCapabilities: AppCapabilities = {
  ...defaultCapabilities,
  // BCN-only 不接入组织目录（继承默认 false），也不查询引擎资产目录补充标签。
  getCollaborationBotEngineLabelEnabled: () => ({ status: 'available', value: false }),
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

// 槽位显式置空：任务栈/TC 选路/任务会话观察者/任务副屏 bootstrap/任务页签快照不进 collab 产物。
registerExtensionSlots({
  groupTasks: null,
  groupServiceBotRouting: null,
  taskSessionNavigationObserver: null,
  taskPanelBootstrap: null,
  sessionTaskPanelTab: null,
});

export const appExtension: AppExtension = {
  avatarEditor: null,
  capabilities: collabCapabilities,
};
