// @asset-migrated: teamclaw 自研资产
/**
 * 任务副屏前端视图模型 —— 由 taskPanelMapper 从 TaskDashboardResponse 映射而来。
 * 字段命名对齐 TeamClaw-v3 PRD demo 的渲染模型，便于 UI 一致。
 */

export type TaskStatus = 'DRAFTING' | 'DEFINED' | 'EXECUTING' | 'REVIEWING' | 'DONE' | 'FAILED' | 'CANCELLED';

/** 节点视图态（归一化小写，对齐 PRD demo F3/B3） */
export type NodeStatus = 'done' | 'running' | 'failed' | 'hung' | 'cancelled' | 'pending';

export interface TaskArtifactView {
  id: string;
  name: string;
  type: 'document' | 'report' | 'link' | 'file' | 'other';
  url?: string | null;
  summary?: string | null;
  updatedAt: string; // ISO 或格式化后的展示串
}

export interface StepTraceView {
  id: string;
  seq: number;
  title: string; // action 描述
  type: 'system' | 'tool_call'; // action_log 无 ai_reply/thinking，统一映射
  timestamp: string;
  content: string; // status_from → status_to + payload 摘要
  toolName?: string;
}

export interface TaskNodeView {
  id: string;
  name: string;
  sequence: number;
  status: NodeStatus;
  executor?: string | null;
  executorColor?: string | null;
  runMode?: string | null;
  startedAt?: string | null;
  endAt?: string | null;
  timeConsuming?: string | null;
  output?: string | null;
  outputSummary?: string | null;
  outputRender?: string | null;
  tokens?: number | null;
  artifacts: TaskArtifactView[];
  groupId?: string | null;
  groupName?: string | null;
  sessionId?: string | null;
  assignee?: string | null;
  /** run_info.assignee_name:绕过群执行时 assignee 常为 bcs 群 id,用此可读 bot 名展示执行者。 */
  assigneeName?: string | null;
  /** 派发未命中事件(extend_props.miss_events):非空表示该节点未真正分配 bot,不应回退任务归属 bot 当执行人。 */
  missEvents?: string[];
  /** 节点挂起原因(extend_props.hung_reason):MISS+HUNG 时配合状态展示。 */
  hungReason?: string | null;
  hasSubTask: boolean;
  subTaskId?: string | null;
  taskSpec?: {
    title?: string | null;
    instruction?: string | null;
    target?: string | null;
    acceptances?: string[];
  };
  stepTraces: StepTraceView[];
  acceptanceResult?: {
    verdict: 'PASS' | 'FAIL' | 'DONE' | null;
    acceptancesMetric: string[];
    gaps: string[];
  } | null;
}

export interface DagNodeView {
  id: string;
  label: string;
  status: NodeStatus;
  x: number;
  y: number;
  isCurrent: boolean;
}
export interface DagEdgeView {
  from: string;
  to: string;
  label?: string;
}

export interface TaskOutputDimension {
  key: string;
  content: string;
}

export interface TaskView {
  id: string;
  name: string;
  description: string;
  goal: string;
  objective: string;
  acceptances: string[];
  status: TaskStatus;
  taskType: string;
  taskTypeLabel: string;
  sourceLabel: string;
  ownerBotName: string;
  ownerBotId: string;
  createdAt: string;
  finishedAt: string | null;
  loopRound: number;
  needsAttention: boolean;
  statusReason?: string | null;
  template?: string | null;
  parentTaskId?: string | null;
  mainSessionName?: string | null;
  /** 任务根节点(data.tasks[0].run_info)产物的渲染源：剥 HTTP 信封后按 markdown 渲染，用于「产物」Tab。 */
  rootOutputRender?: string | null;
  /** 根节点 output 按顶层维度拆分的可读内容(结构化产物展示为多张维度卡片)。 */
  rootOutputDimensions?: TaskOutputDimension[];
  progress: {
    total: number;
    pending: number;
    planning: number;
    running: number;
    done: number;
    failed: number;
    hung: number;
    skipped: number;
    percent: number;
  };
  artifacts: TaskArtifactView[];
  nodes: TaskNodeView[];
  dagNodes: DagNodeView[];
  dagEdges: DagEdgeView[];
}
