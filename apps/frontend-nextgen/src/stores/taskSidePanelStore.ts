import { useTaskUpdateAttentionStore } from '@/stores/taskUpdateAttentionStore';
import { create } from 'zustand';

/**
 * 任务副屏 V2 导航 Store —— 面包屑两级栈 / 选中节点 / 待消费会话跳转。
 * 状态按 task 分片（聊天内每任务一个面板 tab，共享单份下钻栈会串场）。
 * src/assets 不直接 import 本文件：宿主（extensions / 抽屉）经 props 注入资产层
 * （模式同 TaskPanelAdapterWithUser）。
 * 守卫：Store ≤ 150 行。
 */
export interface TaskNavRecord {
  /** 下钻栈：空 = Level1 任务总览；顶 = 当前 Level2 节点 */
  drillStack: string[];
  /** 总览层选中节点（高亮 + 跳转信号源），《drillInto 不改》 */
  selectedNodeId: string | null;
}

/**
 * 节点选中 / 下钻附带的会话跳转目标。
 * kind/groupId/botId 供 P4 宿主消费（group 需先选群再选会话）；
 * startTime/endTime 为节点执行时间窗（毫秒），消息定位用，允许 null。
 */
export interface TaskNavTarget {
  taskId?: string;
  sessionId: string;
  /** 根节点恢复原宿主会话时使用的精确站内地址。 */
  routeUrl?: string;
  kind: 'group' | 'bot' | 'friend' | 'unknown';
  groupId?: string | null;
  botId?: string | null;
  nodeId?: string;
  startTime?: number | null;
  endTime?: number | null;
}

/**
 * 任务节点对应的联动会话目标。
 *
 * 旧版 ChatArea 原位联动目标，保留用于兼容已挂载状态；新节点导航统一切真实路由。
 */
export interface TaskLinkedSessionTarget {
  sessionId: string;
  kind: 'group' | 'bot' | 'friend' | 'unknown';
  groupId?: string | null;
  botId?: string | null;
}

export interface TaskSidePanelState {
  /** 焦点任务：副屏当前渲染/用户当前关注的任务；同一会话可承载多个任务但不并发展示。 */
  activeTaskId: string | null;
  /** 按 task 分片的导航状态 */
  navByTask: Record<string, TaskNavRecord>;
  /** 待宿主消费的会话跳转；消费方调 consumePendingNavigation 置空 */
  pendingNavigation: TaskNavTarget | null;
  /** 一次 openTab 的显式来源标记，用于区分用户切换与历史消息恢复。 */
  taskPanelOpenIntentTaskId: string | null;
  /** 选中信号计数器（+1 = 消息流滚动定位一次），demo nodeScrollSignal 同源 */
  nodeScrollSignal: number;
  /** 会话宿主左侧 ChatArea 的联动会话；null = 展示宿主当前真实会话 */
  linkedSession: TaskLinkedSessionTarget | null;
  setActiveTask: (taskId: string) => void;
  markTaskPanelOpenIntent: (taskId: string) => void;
  consumeTaskPanelOpenIntent: (taskId: string) => boolean;
  selectRootNode: (taskId: string, nodeId: string, target?: TaskNavTarget | null) => void;
  selectNode: (taskId: string, nodeId: string, target: TaskNavTarget | null) => void;
  requestTaskNavigation: (target: TaskNavTarget) => void;
  drillInto: (taskId: string, nodeId: string, target: TaskNavTarget | null) => void;
  backToOverview: (taskId: string) => void;
  consumePendingNavigation: () => void;
  openLinkedSession: (target: TaskLinkedSessionTarget) => void;
  closeLinkedSession: () => void;
  resetAll: () => void;
}

/** 下钻封顶 2 层（Level1 总览 + Level2 节点副屏；L2 无再下钻热区，此为防御） */
const MAX_DRILL_DEPTH = 2;

const emptyRecord: TaskNavRecord = { drillStack: [], selectedNodeId: null };

const initial = {
  activeTaskId: null,
  navByTask: {} as Record<string, TaskNavRecord>,
  pendingNavigation: null,
  taskPanelOpenIntentTaskId: null,
  nodeScrollSignal: 0,
  linkedSession: null as TaskLinkedSessionTarget | null,
};

export const useTaskSidePanelStore = create<TaskSidePanelState>((set, get) => ({
  ...initial,
  setActiveTask: (taskId) => {
    // 切到/打开任务时重置导航，让面板默认选中第一节点。
    useTaskUpdateAttentionStore.getState().clearTaskUpdate(taskId);
    set((s) => {
      const navByTask = { ...s.navByTask, [taskId]: emptyRecord };
      return { activeTaskId: taskId, pendingNavigation: null, taskPanelOpenIntentTaskId: null, navByTask };
    });
  },
  markTaskPanelOpenIntent: (taskId) => set({ taskPanelOpenIntentTaskId: taskId }),
  consumeTaskPanelOpenIntent: (taskId) => {
    const matched = get().taskPanelOpenIntentTaskId === taskId;
    if (matched) set({ taskPanelOpenIntentTaskId: null });
    return matched;
  },
  selectRootNode: (taskId, nodeId, target = null) =>
    set((s) => {
      const prev = s.navByTask[taskId] ?? emptyRecord;
      return {
        navByTask: { ...s.navByTask, [taskId]: { ...prev, selectedNodeId: nodeId } },
        pendingNavigation: target ? { ...target, taskId } : null,
        linkedSession: null,
      };
    }),
  selectNode: (taskId, nodeId, target) =>
    set((s) => {
      const prev = s.navByTask[taskId] ?? emptyRecord;
      return {
        navByTask: { ...s.navByTask, [taskId]: { ...prev, selectedNodeId: nodeId } },
        pendingNavigation: target ? { ...target, taskId } : null,
        nodeScrollSignal: s.nodeScrollSignal + 1,
      };
    }),
  drillInto: (taskId, nodeId, target) =>
    set((s) => {
      const prev = s.navByTask[taskId] ?? emptyRecord;
      const drillStack = prev.drillStack.includes(nodeId)
        ? prev.drillStack
        : prev.drillStack.length >= MAX_DRILL_DEPTH
          ? prev.drillStack
          : [...prev.drillStack, nodeId];
      return {
        navByTask: { ...s.navByTask, [taskId]: { ...prev, drillStack } },
        pendingNavigation: target ? { ...target, taskId } : null,
        // demo 语义：下钻不额外触发消息滚动信号
      };
    }),
  backToOverview: (taskId) =>
    set((s) => {
      const prev = s.navByTask[taskId] ?? emptyRecord;
      return { navByTask: { ...s.navByTask, [taskId]: { ...prev, drillStack: [] } } };
    }),
  requestTaskNavigation: (target) => set({ pendingNavigation: target }),
  consumePendingNavigation: () => set({ pendingNavigation: null }),
  openLinkedSession: (target) => set({ linkedSession: target }),
  closeLinkedSession: () => set({ linkedSession: null }),
  resetAll: () => {
    useTaskUpdateAttentionStore.getState().resetAll();
    set(initial);
  },
}));
