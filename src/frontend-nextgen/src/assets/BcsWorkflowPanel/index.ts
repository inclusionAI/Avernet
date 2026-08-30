// @asset-migrated: Avernet src/bcs/assets/panel/src/index.ts (引擎自带副屏模块，方案B本地注册)
/**
 * 引擎自带副屏模块注册入口（方案 B，第三轨）。
 *
 * 自定义 yaml 协作群执行 workflow 的状态机运行态 DAG 副屏：
 * - 组件源码由 teamclaw 仓持有（迁自 Avernet bcs/assets/panel），进 Open Core（本地兜底）。
 * - 经 `registerPanelContent("bcsPanel.StateMachineRunView", BcsWorkflowPanelAdapter)` 本地注册：
 *   后端 bcs state_machine 执行器硬编码开群默认 opening message 为声明式
 *   `<AixUI type="panel" component="bcsPanel.StateMachineRunView" params={runId}>`（跨产品 UMD 库契约，
 *   与 @alipay/bcn-panel-asset 的 vite lib.name='bcsPanel' + 导出 StateMachineRunView 一致）。
 *   引擎 resolveBusinessEntry 对点号 key CDN 优先：manifest 配 bcsPanel → 远程 UMD（日常热更、不绑
 *   teamclaw 应用发版）；manifest 未配（如当前 pre / 开源）→ 回退本本地注册渲染。
 *
 * 注意：TaskPanel（任务协动态 workflow）本期仅预留空目录，不实现。
 */
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import type { ComponentType } from 'react';
import { BcsWorkflowPanelAdapter } from './BcsWorkflowPanelAdapter';

export { default as StateMachineRunView } from './StateMachineRunView';
export type {
  StateMachineDefinition,
  StateMachineEdge,
  StateMachineJudgeOutput,
  StateMachineNode,
  StateMachineNodeDetailNode,
  StateMachineNodeDetailResponse,
  StateMachineNodeStatus,
  StateMachineNodeSubStatus,
  StateMachineRun,
  StateMachineRunGraph,
  StateMachineRunStatus,
  StateMachineRunViewData,
  StateMachineRunViewProps,
} from './StateMachineRunView';

/**
 * 注册引擎自带副屏模块。由 `src/extensions/empty.ts` 在 app 初始化时调用一次。
 *
 * 设计约束（src/assets 资产目录守卫）：
 * - 本模块不得反向 import teamclaw 业务层(src/components/hooks/store/internal)。
 * - 依赖白名单：仅 react / react-dom / styled-components。
 */
export function registerBuiltinSidePanels(): void {
  // 后端 bcs state_machine 执行器下发的副屏挂载用全路径组件名 bcsPanel.StateMachineRunView。
  // 引擎 resolveBusinessEntry 在 CDN map 未命中(bcsPanel 经方案B去重,不在 manifest)后,
  // 按完整 component 字符串查本地 registry;若未以该精确键注册,会落入 CDN umd 分支并因
  // cdn 为空报「副屏组件加载失败 · 缺少 CDN 地址」。故以该键本地注册,走本地渲染。
  // (assets 目录守卫禁止反向 import 业务层,故用字面量、不引 GROUP_PANEL_COMPONENT_NAME。)
  registerPanelContent('bcsPanel.StateMachineRunView', BcsWorkflowPanelAdapter as ComponentType<PanelContentProps>);
}
