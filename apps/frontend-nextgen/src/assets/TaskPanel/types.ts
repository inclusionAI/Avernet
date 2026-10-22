// @asset-migrated: teamclaw 自研资产
/**
 * 任务副屏前端视图模型 —— 由 taskPanelMapper 从 TaskDashboardResponse 映射而来。
 * 字段命名对齐 TeamClaw-v3 PRD demo 的渲染模型，便于 UI 一致。
 */
import type { ExecDagEdge, ExecDagStep, PresentEdgeSemantics, PresentNodeStatus } from './statusDerivation';

export type TaskStatus = 'DRAFTING' | 'DEFINED' | 'EXECUTING' | 'REVIEWING' | 'DONE' | 'FAILED' | 'CANCELLED';

/** 节点视图态（归一化小写，对齐 PRD demo F3/B3） */
export type NodeStatus = 'done' | 'running' | 'failed' | 'hung' | 'cancelled' | 'pending';

export interface TaskArtifactView {
  id: string;
  name: string;
  type: 'document' | 'report' | 'link' | 'file' | 'other';
  /** V2 产物形态(Descriptor 口径);legacy 产物为 null */
  kind?: 'text' | 'file' | 'url_link' | 'code' | null;
  url?: string | null;
  summary?: string | null;
  /** 归属节点(产物区按 node 过滤用);legacy 回落入参 nodeId */
  nodeId?: string | null;
  /** 产出者(Descriptor.creator);legacy 为 null */
  owner?: string | null;
  updatedAt: string; // ISO 或格式化后的展示串
  /** file 形态的存储寻址(使用既有资源/会话文件服务读取,后端不给现成 URL) */
  fileRef?: {
    storageType: 'bot_workspace' | 'session_file';
    storageId: string;
    fileId?: string | null;
    fileType?: string | null;
  } | null;
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
  /** V2 PRD 展示态派生(rework/merged 等,spec §3.3);老渲染层继续用 status */
  present?: PresentNodeStatus;
  /** 返工次数(action_log[].attempt 聚合) */
  attemptCount?: number;
  /** 节点级 HITL 徽章 */
  needsHitl?: boolean;
  /** 可下钻(协作群/bbs 节点) */
  drillable?: boolean;
  /** 节点内部执行流程(action_log 派生) */
  execDag?: { nodes: ExecDagStep[]; edges: ExecDagEdge[] };
  /** 原始毫秒时间戳(P4 时间窗定位用;startedAt/endAt 为展示串) */
  startedAtMs?: number | null;
  endAtMs?: number | null;
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
    verdict: 'DONE' | 'FAILED' | null;
    doneItems: unknown[];
    gapItems: unknown[];
  } | null;
}

export interface DagNodeView {
  id: string;
  label: string;
  status: NodeStatus;
  x: number;
  y: number;
  isCurrent: boolean;
  /** V2 派生展示态/下钻热区/短标题(面包屑与 DAG 用) */
  present?: PresentNodeStatus;
  drillable?: boolean;
  needsHitl?: boolean;
  short?: string;
}
export interface DagEdgeView {
  from: string;
  to: string;
  label?: string;
  /** V2 边语义派生(spec §3.3):rework/split_work/none… */
  semantics?: PresentEdgeSemantics;
  /** rework 边的返工原因(来自 action_log 事件描述) */
  reason?: string;
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
  /** 资源归属用户；读取 bot_workspace 产物时作为 owner_id 传给既有资源接口。 */
  ownerUserId: string;
  createdAt: string;
  finishedAt: string | null;
  loopRound: number;
  needsAttention: boolean;
  statusReason?: string | null;
  template?: string | null;
  parentTaskId?: string | null;
  mainSessionName?: string | null;
  /** 主会话 id(P4 节点→会话跳转的最重要兜底) */
  mainSessionId?: string | null;
  /** 任务来源群 id */
  sourceGroupId?: string | null;
  /** 派生会话 id 列表(coop_group/bbs 节点定位候选) */
  derivedSessionIds?: string[];
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
