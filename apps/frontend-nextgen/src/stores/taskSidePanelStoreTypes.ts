/**
 * 任务副屏 V2 store 的纯类型契约。
 *
 * 从 taskSidePanelStore 拆出（按职责分文件，store 文件守卫 ≤150 行）：
 * 数据契约与运行时 store 逻辑分离；调用方继续从 '@/stores/taskSidePanelStore'
 * 导入（store 内 re-export），import 面不变。
 */

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

/**
 * 按 task 分片的导航状态记录。
 */
export interface TaskNavRecord {
  /** 下钻栈：空 = Level1 任务总览；顶 = 当前 Level2 节点 */
  drillStack: string[];
  /** 总览层选中节点（高亮 + 跳转信号源），《drillInto 不改》 */
  selectedNodeId: string | null;
}
