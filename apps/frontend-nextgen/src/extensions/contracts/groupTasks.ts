// 协作群任务栈槽位契约（collab-form Task 17）：纯类型文件，运行时零字节。
// 从被 collab 切片裁掉的任务栈实现原样搬出共享类型（useGroupTaskExecution / useTaskExecution /
// TaskComposerMenu / TaskLinkedSession / ManagerWorkerTasks / groupExecuteService / workflowController），
// 实现文件反向 import 本文件注解（type-only，编译期擦除），签名漂移在 typecheck 阶段失配。
//
// 依赖红线（源切片类型完备，见 test/extensions/featureContracts.test.ts）：
// 只允许 domain / React / SDK 类型与既定纯类型家（taskMapper、CapabilitySetViewer/types、
// taskSidePanelStore、groupCreateRequest、identityService）；本文件零 import 进
// pages/任务 hooks/TaskPanel 资产/BotWorkshop 组件/backendApi 任务控制器——collab 裁剪掉
// 这些实现后，契约 + 保留消费方仍可独立 typecheck。
import type { ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import type { CreateGroupInput } from '@/services/workspace/groupCreateRequest';
import type { DomainResult } from '@/services/workspace/identityService';
import type { ComposerSelection, TaskComposerForm, WorkflowSelection } from '@/services/tasks/taskMapper';
import type { ReactNode } from 'react';
import type { TaskLinkedSessionTarget } from '@/stores/taskSidePanelStore';
import type { ChatMessage, PanelHandle } from '@tc-chat/core';
import type { ComponentType, RefObject } from 'react';

/** 工作流列表行（原样搬自 backendApi/workflows/workflowController；该实现属任务栈裁剪面）。 */
export interface WorkflowListItem {
  workflowId: string;
  title: string;
  packId: string | null;
  updatedAt: number;
}

/** 任务执行编排结果（原样搬自 useTaskExecution；槽位 useGroupTaskExecution 的返回面）。 */
export interface UseTaskExecutionResult {
  submitting: boolean;
  error: string | null;
  /** 当前 Bot/协作群 Driver Bot 是否已安装可用的 task-intent-recognition。 */
  dynamicTaskEnabled: boolean;
  dynamicTaskChecking: boolean;
  lastTaskId: string | null;
  workflows: WorkflowListItem[];
  workflowsLoading: boolean;
  /** 鼠标进入工作流菜单时按当前 Bot 懒加载工作流列表。 */
  loadWorkflows: () => Promise<void>;
  /** 受控选中态（可叠加胶囊）：workflow 按 workflowId 去重、动态任务全场至多一个（驱动输入区 chip 与发送拦截）。 */
  selections: ComposerSelection[];
  selectWorkflow: (w: WorkflowSelection) => void;
  selectDynamic: () => void;
  /** 清除指定胶囊（key 见 composerSelectionKey：'dynamic' | workflowId）。 */
  removeSelection: (key: string) => void;
  clearSelection: () => void;
  /** 提交任务并打开副屏。form 校验不通过返回明确原因字符串，成功返回 task_id。 */
  submit: (form: TaskComposerForm) => Promise<{ ok: true; taskId: string } | { ok: false; reason: string }>;
  /** 不发请求，仅校验 form 并返回 disabledReason。 */
  validate: (form: TaskComposerForm) => string | null;
}

/** 协作群任务执行装配入参（原样搬自 useGroupTaskExecution）。 */
export interface GroupTaskExecutionOptions {
  group: GroupView | null;
  session: SessionView | null;
  activeIdentity?: IdentityView | null;
  panelRef: RefObject<PanelHandle>;
  submitPanelMessage: (content: string) => void;
  submitTaskExecutionMessage: (content: string, holderId: string) => void;
  appendAssistantMessage: (content: string) => void;
  streamAssistantMessage?: (content: string) => Promise<void>;
}

/** 协作群任务执行 hook 契约（useGroupTaskExecution 签名；composer 能力菜单数据源）。 */
export type GroupTaskExecutionHook = (options: GroupTaskExecutionOptions) => UseTaskExecutionResult;

/** 输入区能力菜单 props（原样搬自 TaskComposerMenu/composerCapabilitiesMenuTypes）。 */
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
  /** Agent Compute 入口回调；不传时该行不出现。 */
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

/** 输入区能力菜单组件类型（槽位 groupTasks.ComposerCapabilitiesMenu 的载荷签名）。 */
export type ComposerCapabilitiesMenuComponent = ComponentType<ComposerCapabilitiesMenuProps>;

/** 任务指令构造契约（任务胶囊 → /task、/task-intent-recognition 指令消息）。 */
export type BuildTaskInstructions = (content: string, selections: ComposerSelection[]) => string[];

/** 任务节点下钻单 bot/群会话视图 props（原样搬自 TaskLinkedSession 的内联对象类型）。 */
export interface TaskLinkedSessionProps {
  target: TaskLinkedSessionTarget;
  userId?: string;
  onClose: () => void;
}

/** 任务节点下钻联动会话视图组件类型（覆盖整个聊天区的渲染载荷）。 */
export type TaskLinkedSessionComponent = ComponentType<TaskLinkedSessionProps>;

/** task_master_slave 群 manager/worker 任务栏 props（原样搬自 ManagerWorkerTasks 的内联对象类型）。 */
export interface ManagerWorkerTasksProps {
  session: SessionView;
  messages: ChatMessage[];
  connected?: boolean;
}

/** manager/worker 任务栏组件类型。 */
export type ManagerWorkerTasksComponent = ComponentType<ManagerWorkerTasksProps>;

/**
 * 经 task execute 创建自定义协作群（state_machine）的执行器契约。
 * 签名原样对应 groupExecuteService.createGroupViaExecute：非自定义协作群等场景由
 * 注入的 fallbackCreateGroup 回落原链路（依赖注入规避循环依赖）。
 */
export type CreateGroupViaExecute = (
  input: CreateGroupInput,
  ownerUserId: string,
  fallbackCreateGroup: (input: CreateGroupInput) => Promise<DomainResult<GroupView>>,
) => Promise<DomainResult<GroupView>>;
