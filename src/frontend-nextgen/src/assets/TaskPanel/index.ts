// @asset-migrated: teamclaw 自研资产（任务协作执行 workflow 副屏，路 A 本地注册，进 Open Core）
/**
 * 任务协作执行 workflow 副屏注册入口（路 A，方案 B 第三轨）。
 *
 * - 组件源码由 teamclaw 仓持有（src/assets/TaskPanel），进 Open Core（开源 + 内源均可）。
 * - 经 `registerPanelContent("taskPanel.TaskLoopView", TaskPanelAdapter)` 本地注册（方式③ 本地兜底）：
 *   前端 submitPanelMessage 发声明式 `<AixUI type="panel" component="taskPanel.TaskLoopView" ...>`，
 *   任务执行成功后回落库进 history → loadHistory 拉回持久（切会话/刷新可恢复）；引擎 resolveBusinessEntry
 *   对点号 key CDN 优先：aixLibraryCdnMap['taskPanel'] 命中 → 远程 UMD 热更（不绑应用发版），当前无 UMD
 *   通道休眠 → 回退本地注册渲染。与 bcsPanel.StateMachineRunView 同构（点号 + CDN 优先 + 本地兜底）。
 * - legacy 别名 `task-loop`：兼容改名前已落库的副屏消息仍可渲染，旧消息老化后可删（参考已删 bcs-workflow）。
 * - 数据流：副屏从 params 取 apiBaseUrl/taskId，组件内自管轮询 dashboard（路 A，不依赖 taskService）。
 * - 与 BCS 状态机副屏（协作群 YAML workflow，bcsPanel.StateMachineRunView）互不干扰。
 *
 * 设计约束（src/assets 资产目录守卫）：
 * - 本模块不得反向 import teamclaw 业务层（src/components/hooks/stores/pages/domain/internal）。
 * - 依赖白名单：仅 react / react-dom / styled-components / @tc-chat/ui(engine SDK 注册)。
 */
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import type { ComponentType } from 'react';
import { TaskPanelAdapter } from './TaskPanelAdapter';

export { GroupDrillDownPanel } from './GroupDrillDown';
export { TaskPanel } from './TaskPanel';
export { TaskPanelAdapter } from './TaskPanelAdapter';
export { TaskPanelFetcher } from './TaskPanelFetcher';
export { TaskPanelHeader } from './TaskPanelHeader';
export { mapDashboard, mapTaskStatus } from './taskPanelMapper';
export { TaskSubTaskPanel } from './TaskSubTaskPanel';
export type {
  DagEdgeView,
  DagNodeView,
  NodeStatus,
  TaskArtifactView,
  TaskNodeView,
  TaskStatus,
  TaskView,
} from './types';

/**
 * 注册任务副屏模块。由 `src/extensions/empty.ts` 在 app 初始化时调用一次（与 bcsPanel.StateMachineRunView 并列）。
 */
export function registerTaskPanel(): void {
  // 任务副屏挂载用全路径组件名 taskPanel.TaskLoopView（对齐 bcsPanel.StateMachineRunView：lib.Component
  // 点号 key，引擎 resolveBusinessEntry 对点号 key CDN 优先）。window.aixLibraryCdnMap['taskPanel'] 命中
  // → 远程 UMD 热更；当前无 TaskPanel UMD → CDN 通道休眠 → 回退本地注册（与 bcsPanel 未配 manifest 同构）。
  registerPanelContent('taskPanel.TaskLoopView', TaskPanelAdapter as ComponentType<PanelContentProps>);
  // 兼容已落库的旧 <AixUI component="task-loop"> 历史消息：loadHistory 拉回需仍能渲染。迁移过渡，
  // 旧消息老化后可删（同 bcs-workflow 删除套路）。
  registerPanelContent('task-loop', TaskPanelAdapter as ComponentType<PanelContentProps>);
}
