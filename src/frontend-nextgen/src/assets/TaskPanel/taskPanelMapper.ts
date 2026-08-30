// @asset-migrated: teamclaw 自研资产
/**
 * taskPanelMapper —— 后端 TaskDashboardResponse → 前端 TaskView 视图模型。
 * - 状态归一化：兼容旧运行时状态与新产品状态；节点状态转 NodeStatus(小写)，图级状态转产品 TaskStatus。
 * - DAG 布局：relations 只有 src/dst，无坐标 → 前端分层布局计算 x/y（垂直流向）。
 * - relations 缺失 → dagNodes/dagEdges 为空（上层降级「暂无 DAG 数据」）。
 * - 时间：start_time/end_time 毫秒戳 → 格式化 + 计算 timeConsuming。
 */
import { normalizeTaskStatus } from '@/shared/taskStatus';
import type { TaskDashboardResponse, TaskNodeDto, TaskStatusCode } from './contract';
import { ARTIFACT_TYPE_LABELS, SOURCE_LABELS, TASK_STATUS_TONES, TASK_TYPE_LABELS } from './tokens';
import type { DagEdgeView, DagNodeView, NodeStatus, StepTraceView, TaskNodeView, TaskStatus, TaskView } from './types';

/** 执行模态 → 头像底色 */
function executorColor(runMode?: string | null): string | null {
  switch (runMode) {
    case 'single_bot':
      return '#165DFF';
    case 'coop_group':
      return '#722ED1';
    case 'bbs':
      return '#F53F3F';
    default:
      return null;
  }
}

function msToDisplay(ms?: number | null): string | null {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return null;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return String(ms);
  const M = d.getMonth() + 1;
  const day = d.getDate();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${M}月${day}日 ${hh}:${mm}`;
}

function durationDisplay(start?: number | null, end?: number | null): string | null {
  if (start === null || start === undefined) return null;
  const e = end ?? Date.now();
  const diff = e - start;
  if (diff < 0) return null;
  if (diff < 60_000) return `${Math.max(1, Math.round(diff / 1000))}秒`;
  const m = Math.round(diff / 60_000);
  if (m < 60) return `${m}分钟`;
  return `${Math.floor(m / 60)}小时${m % 60}分钟`;
}

/** 解析节点输出摘要：优先 output_summary；否则从 output.data.result 提取实际内容。 */
function resolveOutputSummary(output: unknown, outputSummary: string | null | undefined): string | null {
  if (outputSummary) return outputSummary;
  if (!output || typeof output !== 'object') return null;
  const obj = output as Record<string, unknown>;
  const data = obj.data;
  if (data && typeof data === 'object' && data !== null) {
    const result = (data as Record<string, unknown>).result;
    if (typeof result === 'string' && result.trim()) return result;
  }
  // 兜底：output.data.result 不存在时，尝试 output.result
  const directResult = obj.result;
  if (typeof directResult === 'string' && directResult.trim()) return directResult;
  return null;
}

function getExtendString(extendProps: Record<string, unknown> | undefined, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = extendProps?.[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}

function resolveNodeExecutor(runInfo: TaskNodeDto['run_info']): string | null {
  if (!runInfo) return null;
  const extendProps = runInfo.extend_props;
  const assigneeName = runInfo.assignee_name ?? getExtendString(extendProps, 'assignee_name');

  // 单 Bot 节点的 assignee 可能只是 Bot ID，优先使用 extend_props.assignee_name 展示可读名称。
  if (runInfo.run_mode === 'single_bot' && assigneeName) return assigneeName;

  const namedExecutor =
    assigneeName ??
    getExtendString(
      extendProps,
      'master_bot_name',
      'driver_bot_name',
      'originator_bot_name',
      'initiator_bot_name',
      'bot_name',
    );
  if (runInfo.run_mode === 'coop_group') return namedExecutor;

  return namedExecutor ?? runInfo.assignee ?? getExtendString(extendProps, 'bot_id');
}

function mapNodeTaskSpec(spec: TaskNodeDto['task_spec']): TaskNodeView['taskSpec'] {
  const acceptances = (spec?.goal?.acceptances ?? [])
    .map((acceptance) =>
      typeof acceptance === 'string' ? acceptance : acceptance.acceptance ?? acceptance.description ?? '',
    )
    .filter(Boolean);

  return {
    title: spec?.metadata?.title ?? null,
    instruction: spec?.metadata?.instruction ?? null,
    target: spec?.goal?.objective ?? null,
    acceptances,
  };
}

function mapNodeStatus(s: TaskStatusCode | 'SKIPPED'): NodeStatus {
  switch (s) {
    case 'DONE':
      return 'done';
    case 'RUNNING':
    case 'PLANNING':
    case 'EXECUTING':
      return 'running';
    case 'FAILED':
      return 'failed';
    case 'SKIPPED':
    case 'CANCELLED':
      return 'skipped';
    case 'PENDING':
    case 'HUNG':
    case 'REVIEWING':
    case 'DRAFTING':
    case 'DEFINED':
    default:
      return 'pending';
  }
}

/** 产品层与旧运行时状态统一映射到任务副屏状态。 */
export function mapTaskStatus(s: TaskStatusCode | string): TaskStatus {
  return normalizeTaskStatus(s);
}

function mapActionLog(
  log?: TaskNodeDto['run_info'] extends infer R ? (R extends { action_log?: infer A } ? A : undefined) : undefined,
): StepTraceView[] {
  if (!log || !Array.isArray(log)) return [];
  return log.map((e) => ({
    id: `${e.seq}-${e.action}`,
    seq: e.seq,
    title: e.action || 'system',
    type: e.action === 'dispatch' || e.action === 'tool' ? 'tool_call' : 'system',
    timestamp: msToDisplay(e.ts) ?? '',
    content:
      e.status_from || e.status_to
        ? `${e.status_from ?? '—'} → ${e.status_to ?? '—'}`
        : JSON.stringify(e.payload ?? {}),
    toolName: e.action,
  }));
}

function mapArtifacts(list?: TaskDashboardResponse['artifacts']): TaskView['artifacts'] {
  return (list ?? []).map((a) => ({
    id: a.artifact_id,
    name: a.name,
    type: a.type,
    url: a.url ?? null,
    summary: a.summary ?? null,
    updatedAt: a.created_at,
  }));
}

/** 分层布局：按 relations 拓扑分层，每层上→下排列；无 relations 返回空数组。 */
function layoutDag(
  nodes: TaskNodeView[],
  relations: { src_id: string; dst_id: string }[],
): { dagNodes: DagNodeView[]; dagEdges: DagEdgeView[] } {
  if (!nodes.length) {
    return { dagNodes: [], dagEdges: [] };
  }
  // relations 是后端关系事实；缺失时不猜测边，交给 DAG 视图展示明确降级态。
  const effectiveRelations = relations;
  const nodeIds = new Set(nodes.map((n) => n.id));

  const indeg = new Map<string, number>();
  const adj = new Map<string, string[]>();
  nodeIds.forEach((id) => {
    indeg.set(id, 0);
    adj.set(id, []);
  });
  effectiveRelations.forEach((r) => {
    if (nodeIds.has(r.src_id) && nodeIds.has(r.dst_id)) {
      adj.get(r.src_id)!.push(r.dst_id);
      indeg.set(r.dst_id, (indeg.get(r.dst_id) ?? 0) + 1);
    }
  });
  const level = new Map<string, number>();
  const queue: string[] = [];
  indeg.forEach((v, id) => {
    if (v === 0) {
      level.set(id, 0);
      queue.push(id);
    }
  });
  if (queue.length === 0) {
    queue.push(nodes[0].id);
    level.set(nodes[0].id, 0);
  }
  let head = 0;
  // 分层 BFS（允许重复入队以取最大层级，环兜底无死循环：已入队且层级不变则跳过）
  const pushed = new Set<string>(queue);
  while (head < queue.length) {
    const cur = queue[head++];
    const lv = level.get(cur) ?? 0;
    adj.get(cur)!.forEach((nb) => {
      const nl = Math.max(level.get(nb) ?? 0, lv + 1);
      level.set(nb, nl);
      indeg.set(nb, (indeg.get(nb) ?? 1) - 1);
      if (indeg.get(nb) === 0 && !pushed.has(nb)) {
        queue.push(nb);
        pushed.add(nb);
      }
    });
  }
  nodeIds.forEach((id) => {
    if (!level.has(id)) level.set(id, 0);
  });

  const layerRows = new Map<number, string[]>();
  level.forEach((lv, id) => {
    if (!layerRows.has(lv)) layerRows.set(lv, []);
    layerRows.get(lv)!.push(id);
  });
  const layers = [...layerRows.keys()].sort((a, b) => a - b);
  // 竖向 DAG：层级(深度) → y 轴（自上而下），同层多节点 → x 轴（横向并列）
  const COL_W = 160; // 同层节点横向间距(含节点宽)
  const ROW_H = 100; // 层级纵向间距(含节点高)
  const X_GAP = 40;
  const Y_GAP = 40;
  const pos = new Map<string, { x: number; y: number }>();
  layers.forEach((lv) => {
    layerRows.get(lv)!.forEach((id, idx) => {
      pos.set(id, { x: idx * (COL_W + X_GAP), y: lv * (ROW_H + Y_GAP) });
    });
  });

  const runningId = nodes.find((n) => n.status === 'running')?.id;
  const dagNodes: DagNodeView[] = nodes.map((n) => {
    const p = pos.get(n.id) ?? { x: 0, y: 0 };
    return {
      id: n.id,
      label: n.name,
      status: n.status,
      x: p.x + X_GAP,
      y: p.y + Y_GAP,
      isCurrent: n.id === runningId,
    };
  });
  const dagEdges: DagEdgeView[] = effectiveRelations
    .filter((r) => nodeIds.has(r.src_id) && nodeIds.has(r.dst_id))
    .map((r) => ({ from: r.src_id, to: r.dst_id }));

  return { dagNodes, dagEdges };
}

/** 后端 top-level 无 progress 时，从 tasks 节点状态统计进度。 */
function computeProgress(d: TaskDashboardResponse, nodes: TaskNodeView[]): TaskView['progress'] {
  if (d.progress) {
    return {
      total: d.progress.total ?? 0,
      pending: d.progress.pending ?? 0,
      planning: d.progress.planning ?? 0,
      running: d.progress.running ?? 0,
      done: d.progress.done ?? 0,
      failed: d.progress.failed ?? 0,
      hung: d.progress.hung ?? 0,
      skipped: d.progress.skipped ?? 0,
      percent: d.progress.percent ?? 0,
    };
  }
  const total = nodes.length;
  const counts = { pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0 };
  for (const n of nodes) {
    const st = n.status;
    if (st in counts) counts[st as keyof typeof counts] += 1;
  }
  const finished = counts.done + counts.failed + counts.skipped;
  const percent = total > 0 ? Math.round((finished / total) * 100) : 0;
  return { total, ...counts, percent };
}

export function mapDashboard(d: TaskDashboardResponse): TaskView {
  // 后端 dashboard 实际返回 TaskExecutionGraphDTO（精简结构）：
  // top-level 仅 run_id/loop_round/status/output/tasks/extend_props，
  // task_spec/owner/progress 等需从根节点（tasks[0]）与 extend_props 推导。
  const taskList = d.tasks ?? [];
  const rootNode = taskList[0];
  // task_spec 直接从根节点（tasks[0]）取：后端 top-level 不返回 task_spec。
  const spec = (rootNode?.task_spec ?? {}) as TaskDashboardResponse['task_spec'];
  const meta = spec.metadata ?? { title: d.task_id ?? rootNode?.node_id ?? '', instruction: '' };
  const goal = spec.goal ?? { objective: '', acceptances: [] };
  const ctx = spec.context ?? {};
  const tcCtx = ctx?.extend_props?.teamclaw_context as
    | { main_session_name?: string; parent_task_id?: string }
    | undefined;

  const extProps = d.extend_props as Record<string, unknown> | undefined;
  const execCfg = d.execution_config ?? extProps?.execution_config;
  const template =
    execCfg && typeof execCfg === 'object'
      ? (execCfg as { workflow_id?: string }).workflow_id ?? ((execCfg as { yaml?: string }).yaml ? 'yaml' : null)
      : null;

  const nodes: TaskNodeView[] = taskList
    .slice()
    .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
    .map((n) => {
      const ri = n.run_info ?? {};
      const title = n.task_spec?.metadata?.title ?? n.node_id;
      const groupId = getExtendString(ri.extend_props, 'group_id', 'coop_group_id', 'source_group_id');
      const groupName =
        ri.run_mode === 'coop_group' || groupId ? getExtendString(ri.extend_props, 'group_name') ?? 'BCS协作群' : null;
      return {
        id: n.node_id,
        name: title,
        sequence: n.sequence ?? 0,
        status: mapNodeStatus(n.status),
        executor: resolveNodeExecutor(ri),
        executorColor: executorColor(ri.run_mode),
        runMode: ri.run_mode ?? null,
        // 下钻触发条件 = session_id 存在（与 run_mode 无关）：
        // - group_id 非空（群执行）→ 查群成员
        // - group_id 空 + assignee 非空（单 bot 执行）→ 按 assignee 查 bot 信息
        // - 统一按 session_id 拉对话消息
        groupId,
        groupName,
        sessionId: getExtendString(ri.extend_props, 'session_id', 'group_session_id', 'main_session_id'),
        assignee: ri.assignee ?? null,
        startedAt: msToDisplay(ri.start_time),
        endAt: msToDisplay(ri.end_time),
        timeConsuming: durationDisplay(ri.start_time, ri.end_time),
        output: ri.output ? JSON.stringify(ri.output) : null,
        outputSummary: resolveOutputSummary(ri.output, ri.output_summary),
        taskSpec: mapNodeTaskSpec(n.task_spec),
        artifacts: (ri.artifacts ?? []).map((a) => ({
          id: a.artifact_id,
          name: a.name,
          type: a.type,
          url: a.url ?? null,
          summary: a.summary ?? null,
          updatedAt: a.created_at,
        })),
        hasSubTask: !!ri.child_task_id,
        subTaskId: ri.child_task_id ?? null,
        stepTraces: mapActionLog(ri.action_log),
        acceptanceResult: ri.acceptance_result
          ? {
              verdict: ri.acceptance_result.verdict,
              acceptancesMetric: ri.acceptance_result.acceptances_metric ?? [],
              gaps: ri.acceptance_result.gaps ?? [],
            }
          : null,
      };
    });

  const { dagNodes, dagEdges } = layoutDag(nodes, d.relations ?? []);

  const productStatus = mapTaskStatus(d.status);
  // 以下 void 引用避免未使用告警（色表/label 由 UI 层消费）
  void TASK_STATUS_TONES;
  void ARTIFACT_TYPE_LABELS;

  return {
    id: d.task_id ?? rootNode?.task_id ?? rootNode?.node_id ?? '',
    name: meta.title ?? d.task_id ?? rootNode?.task_id ?? '',
    description: meta.instruction ?? ctx.background ?? '',
    goal: goal.objective ?? '',
    objective: goal.objective ?? '',
    acceptances: (goal.acceptances ?? [])
      .map((a) => (typeof a === 'string' ? a : a.acceptance ?? a.description ?? ''))
      .filter(Boolean),
    status: productStatus,
    taskType: d.task_type ?? 'dynamic',
    taskTypeLabel: TASK_TYPE_LABELS[d.task_type ?? 'dynamic'] ?? d.task_type ?? '',
    sourceLabel: SOURCE_LABELS[d.source_type ?? 'bot'] ?? d.source_type ?? '',
    ownerBotName: d.owner_bot?.name ?? d.owner_bot_id ?? '',
    ownerBotId: d.owner_bot_id ?? '',
    createdAt: d.create_time,
    finishedAt: d.finish_time,
    loopRound: d.loop_round ?? 0,
    needsAttention: !!d.needs_attention,
    statusReason: d.status_reason ?? null,
    template,
    parentTaskId: d.parent_task_id ?? tcCtx?.parent_task_id ?? null,
    mainSessionName: d.main_session?.name ?? tcCtx?.main_session_name ?? null,
    progress: computeProgress(d, nodes),
    artifacts: mapArtifacts(d.artifacts),
    nodes,
    dagNodes,
    dagEdges,
  };
}
