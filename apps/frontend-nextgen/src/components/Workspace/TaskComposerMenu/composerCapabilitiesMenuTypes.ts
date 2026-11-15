// @asset-migrated: teamclaw 自研
// ComposerCapabilitiesMenu props 契约（add-spm-tracking 拆出：TC-G005 组件 300 行红线）。
import type { ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import type { ComposerSelection, WorkflowSelection } from '@/services/tasks/taskMapper';
import type { ReactNode } from 'react';

export interface ComposerCapabilitiesMenuProps {
  execution?: UseTaskExecutionResult;
  onUpload?: () => void;
  /** 添加图片回调（打开文件选择器）。单聊设计稿未含此项；群域传入时仍保留「添加图片」行。 */
  onAddImage?: () => void;
  /** 文件管理回调（单聊头部资源面板承接，见 chat-header-panels）。传入时保留「文件管理」行（群域）。 */
  onManageFiles?: () => void;
  enableWorkflow?: boolean;
  disabled?: boolean;
  disabledReason?: string | null;
  /** 可叠加选中项（胶囊 chips）：workflow 按 workflowId 去重、动态任务至多一个。 */
  selections?: ComposerSelection[];
  onWorkflowSelected?: (w: WorkflowSelection) => void;
  onDynamicSelected?: () => void;
  /** Agent Compute 入口回调：与「任务」「工作流任务」并列渲染；不传时该行不出现。 */
  onAgentCompute?: () => void;
  /** 清除指定胶囊（key 见 composerSelectionKey：'dynamic' | workflowId）。 */
  onRemoveSelection?: (key: string) => void;
  /** 能力集只读查看装配（useBotCapabilitySets；单聊 我的/团队空间 Bot 传入）。
   * 不传时「通用协作能力」文案区不渲染——好友 Bot 会话与协作群即此形态，＋ 区独立保留。 */
  capabilitySets?: ComposerCapabilitySetsState | null;
  /** 调试会话仅复用附件与能力集入口，不暴露任务创建菜单。 */
  showTaskEntries?: boolean;
  /** 平台注入的额外菜单行（capability 覆盖式，2026-11-04）：渲染函数收 close——
   *  行点击后可收起外层「＋」菜单（如知识库行收起后唤起命令面板）。行样式与视觉
   *  一致性由调用方负责（复用 MenuRow）。不传行为不变。 */
  extraMenuRows?: (close: () => void) => ReactNode;
  className?: string;
  /**
   * ＋ 菜单打开回调（add-spm-tracking：调用方语义注入,如对话页埋「打开工作流菜单」）。
   * 可选;不传行为不变。
   */
  onOpened?: () => void;
}
