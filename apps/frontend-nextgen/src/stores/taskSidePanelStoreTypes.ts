/** Host route targets and linked-session rendering container; no task navigation state. */

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
