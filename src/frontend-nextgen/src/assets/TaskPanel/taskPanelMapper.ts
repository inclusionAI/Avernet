// @asset-migrated: teamclaw 自研资产
/**
 * taskPanelMapper —— 后端 TaskDashboardResponse → 前端 TaskView 视图模型。
 * - 状态归一化：兼容旧运行时状态与新产品状态；节点状态转 NodeStatus(小写)，图级状态转产品 TaskStatus。
 * - DAG 布局：relations 只有 src/dst，无坐标 → 前端分层布局计算 x/y（垂直流向）。
 * - relations 缺失 → dagNodes/dagEdges 为空（上层降级「暂无 DAG 数据」）。
 * - 时间：start_time/end_time 毫秒戳 → 格式化 + 计算 timeConsuming。
 */
import { normalizeTaskStatus } from '@/shared/taskStatus';
import type { TaskDashboardResponse, TaskNodeDto, TaskStatusCode, TaskType } from './contract';
import { renderableSource, unwrapHttpEnvelope } from './outputEnvelope';
import { ARTIFACT_TYPE_LABELS, SOURCE_LABELS, TASK_STATUS_TONES, TASK_TYPE_LABELS } from './tokens';
import type {
  DagEdgeView,
  DagNodeView,
  NodeStatus,
  StepTraceView,
  TaskNodeView,
  TaskOutputDimension,
  TaskStatus,
  TaskView,
} from './types';

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

/** 节点输出摘要（顶部 2 行预览用）：剥信封后取 payload，仅当它为字符串时返回；对象/数组则留空（预览不渲染长内容）。
 *  注意：预览只用 unwrapHttpEnvelope 取字符串载荷，不调用 renderableSource——避免把对象结果包成 ```json 代码块塞进 2 行截断。 */
function resolveOutputSummary(output: unknown, outputSummary: string | null | undefined): string | null {
  if (outputSummary) return outputSummary;
  const payload = unwrapHttpEnvelope(output);
  if (typeof payload === 'string' && payload.trim()) return payload;
  return null;
}

/** 判断根节点 output 是否实质为「无产物」：空字符串/空对象/空数组(且无 output_summary)视为无产物,产物页展示空态。 */
function isEmptyOutput(output: unknown, outputSummary: string | null | undefined): boolean {
  if (outputSummary && outputSummary.trim()) return false;
  if (output === null || output === undefined) return true;
  if (typeof output === 'string') return output.trim() === '';
  if (Array.isArray(output)) return output.length === 0;
  if (typeof output === 'object') return Object.keys(output as Record<string, unknown>).length === 0;
  return false;
}

/** 节点输出整块渲染源（详情抽屉整块渲染用）：剥信封（含裸 JSON 字符串解析）后取 payload——
 *  字符串 → 当 markdown 渲染；对象/数组 → 包成 ```json 代码块；不再回退原始 response 信封当文本输出。 */
function resolveOutputRender(output: unknown, outputSummary: string | null | undefined): string | null {
  if (outputSummary) return outputSummary;
  return renderableSource(output);
}

/** 将根节点 output 的顶层对象拆成多个产出维度，优先读取每个维度下的 summary；非结构化输出返回空数组。 */
function resolveOutputDimensions(output: unknown): TaskOutputDimension[] {
  let payload: unknown = output;
  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload);
    } catch {
      return [];
    }
  }
  payload = unwrapHttpEnvelope(payload);
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return [];

  return Object.entries(payload as Record<string, unknown>)
    .map(([key, value]) => {
      const summary =
        value && typeof value === 'object' && !Array.isArray(value)
          ? (value as Record<string, unknown>).summary
          : undefined;
      const content = typeof summary === 'string' && summary.trim() ? summary : renderableSource(value);
      return content?.trim() ? { key, content } : null;
    })
    .filter((item): item is TaskOutputDimension => item !== null);
}

function getExtendString(extendProps: Record<string, unknown> | undefined, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = extendProps?.[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}

function mapSourceTypeToRunMode(sourceType: unknown): 'single_bot' | 'coop_group' | null {
  switch (sourceType) {
    case 'coop_group':
      return 'coop_group';
    case 'bot':
    case 'api':
      return 'single_bot';
    default:
      return null;
  }
}

function hasNonEmptyValue(value: unknown): boolean {
  if (typeof value === 'string') return value.trim().length > 0;
  if (value && typeof value === 'object') return Object.keys(value).length > 0;
  return value !== null && value !== undefined;
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

  const assigneeBotId = getExtendString(extendProps, 'assignee_bot_id');
  const binding =
    runInfo.assignee && typeof runInfo.assignee === 'object'
      ? (runInfo.assignee as { binding?: string }).binding ?? null
      : null;
  const assigneeStr = typeof runInfo.assignee === 'string' ? runInfo.assignee : null;
  return namedExecutor ?? assigneeBotId ?? binding ?? assigneeStr ?? getExtendString(extendProps, 'bot_id');
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
    case 'SUCCESS':
      return 'done';
    case 'RUNNING':
    case 'PLANNING':
    case 'EXECUTING':
      return 'running';
    case 'FAILED':
      return 'failed';
    case 'CANCELLED':
      return 'cancelled';
    case 'HUNG':
    case 'REVIEWING':
      return 'hung';
    case 'PENDING':
    case 'DRAFTING':
    case 'DEFINED':
    case 'SKIPPED':
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
  // 优先使用根节点 task_spec；根节点字段不完整时，使用 dashboard 顶层 task_spec 补齐。
  // 部分任务只在顶层下发背景/目标，不能因为根节点存在就丢失这些字段。
  const rootSpec = rootNode?.task_spec;
  const topLevelSpec = d.task_spec;
  const spec = {
    metadata: rootSpec?.metadata ?? topLevelSpec?.metadata,
    context: rootSpec?.context ?? topLevelSpec?.context,
    goal: rootSpec?.goal ?? topLevelSpec?.goal,
  } as TaskDashboardResponse['task_spec'];
  const meta = spec.metadata ?? { title: d.task_id ?? rootNode?.node_id ?? '', instruction: '' };
  const goal = spec.goal ?? { objective: '', acceptances: [] };
  const acceptances = (goal.acceptances ?? [])
    .map((a) => (typeof a === 'string' ? a : a.acceptance ?? a.description ?? ''))
    .filter(Boolean);
  const ctx = spec.context ?? {};
  const tcCtx = ctx?.extend_props?.teamclaw_context as
    | { main_session_name?: string; parent_task_id?: string }
    | undefined;

  const extProps = d.extend_props as Record<string, unknown> | undefined;
  // execution_config 优先取顶层(后端 dashboard 归一投影);历史记录回退 extend_props.execution_config。
  const execCfgRaw = d.execution_config ?? extProps?.execution_config;
  const execCfg = (execCfgRaw && typeof execCfgRaw === 'object' ? execCfgRaw : {}) as Record<string, unknown>;
  const template =
    execCfg && typeof execCfg === 'object'
      ? (execCfg as { workflow_id?: string }).workflow_id ?? ((execCfg as { yaml?: string }).yaml ? 'yaml' : null)
      : null;

  // task_type 真值在 execution_config(顶层 task_type 后端常不下发);yaml/workflow 为编排类任务。
  const taskType = (execCfg.task_type as TaskType | undefined) ?? d.task_type ?? 'dynamic';
  // yaml/workflow 任务:execution_graph 非空时下钻展示其内部执行图(状态机节点),否则回退顶层 tasks/relations。
  // 动态任务无 execution_graph,展示逻辑不变(用顶层 tasks/relations)。
  const executionGraph = d.execution_graph;
  const hasExecGraph = !!executionGraph && Array.isArray(executionGraph.tasks) && executionGraph.tasks.length > 0;
  const useExecGraph = (taskType === 'yaml' || taskType === 'workflow') && hasExecGraph;
  const graphSource = useExecGraph ? executionGraph! : d;
  const graphExtProps = {
    ...((graphSource?.extend_props ?? {}) as Record<string, unknown>),
    ...(extProps ?? {}),
  };
  const graphTasks = graphSource.tasks ?? [];
  const graphRelations = graphSource.relations ?? [];
  // workflow/yaml 的 execution_graph 可能只返回内部节点，未复用顶层 tasks[0] 的 node_id。
  // 此时将 execution_graph 的 task_id 节点（缺失时首节点）视为展示根节点，确保 graph 级兜底生效。
  const graphRootNode = useExecGraph
    ? graphTasks.find((node) => node.node_id === d.task_id) ?? graphTasks[0]
    : rootNode;

  // 自定义 yaml/workflow:execution_graph 节点不含会话信息,协群会话在顶层根节点 data.tasks[0].run_info。
  // 把根节点 group_id/session_id/group_name 回填到 execution_graph 节点,使其可下钻同一协群会话;
  // 动态/单 bot 节点自带 session,优先取自身(回填仅作兜底,不影响)。
  const rootRunInfo = rootNode?.run_info;
  const rootExt = rootRunInfo?.extend_props;
  const rootGroupId =
    getExtendString(rootExt, 'group_id', 'coop_group_id', 'source_group_id') ??
    getExtendString(graphExtProps, 'group_id', 'coop_group_id', 'source_group_id');
  const rootSessionId =
    getExtendString(rootExt, 'session_id', 'group_session_id', 'main_session_id') ??
    getExtendString(graphExtProps, 'session_id', 'group_session_id', 'main_session_id');
  const rootGroupName = getExtendString(rootExt, 'group_name');
  // 默认产物 Tab 展示根节点(d.tasks[0].run_info)的 output：与节点详情「输出摘要」同套渲染逻辑。
  const defaultRootOutputRender = isEmptyOutput(rootRunInfo?.output, rootRunInfo?.output_summary)
    ? null
    : resolveOutputRender(rootRunInfo?.output, rootRunInfo?.output_summary);

  const nodes: TaskNodeView[] = graphTasks
    .slice()
    .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
    .map((n) => {
      const ri = n.run_info ?? {};
      const title = n.task_spec?.metadata?.title ?? n.node_id;
      const isRootNode = n.node_id === graphRootNode?.node_id || n.node_id === d.task_id;
      // 派发未命中事件透出为节点元数据(视图层仅作展示/提示用);「未分配」判定统一由赋值是否为空决定,
      // 不在此处按 miss_events 清空 session 继承(避免影响 yaml/workflow 共享根会话的回填)。
      const nodeMissEventsRaw = ri.extend_props?.miss_events;
      const nodeMissEvents = Array.isArray(nodeMissEventsRaw)
        ? nodeMissEventsRaw.filter((e): e is string => typeof e === 'string')
        : [];
      const nodeHungReason = getExtendString(ri.extend_props, 'hung_reason');
      // 根节点的运行信息可能没有完整落在 run_info 中：用 graph 级 extend_props 做只读兜底，
      // 不覆盖节点自身已有值。子节点仍保持原有节点级字段解析逻辑。
      const effectiveExtendProps = isRootNode ? { ...graphExtProps, ...(ri.extend_props ?? {}) } : ri.extend_props;
      // 因权限绕过,后端可能统一把 run_mode 标记为 coop_group;真实执行模式以 extend_props.actual_run_mode 为准(存在则覆盖 run_mode)。
      const baseRunMode =
        (hasNonEmptyValue(ri.run_mode) ? ri.run_mode : null) ??
        (isRootNode ? mapSourceTypeToRunMode(getExtendString(graphExtProps, 'source_type') ?? d.source_type) : null);
      // actual_run_mode 仅接受已知执行模态,未知/非法值降级到 baseRunMode,避免脏数据污染下游单/群判别。
      const actualRunMode = getExtendString(effectiveExtendProps, 'actual_run_mode');
      const effectiveRunMode =
        (actualRunMode === 'single_bot' || actualRunMode === 'coop_group' || actualRunMode === 'bbs'
          ? actualRunMode
          : null) ?? baseRunMode;
      const effectiveAssignee =
        (hasNonEmptyValue(ri.assignee) ? ri.assignee : null) ??
        (isRootNode ? getExtendString(graphExtProps, 'owner_bot_id') ?? d.owner_bot_id ?? null : null);
      const effectiveRunInfo = {
        ...ri,
        run_mode: effectiveRunMode,
        assignee: effectiveAssignee,
        extend_props: effectiveExtendProps,
      };
      // execution_graph(状态机)节点 assignee 是 bot_binding 对象{type,binding},规范化为字符串:
      // 优先 assignee_bot_id(实际 bot)→ binding(逻辑角色);单/动态节点 assignee 本就是字符串。
      const assigneeRaw = effectiveRunInfo.assignee;
      const assigneeBotId = getExtendString(effectiveRunInfo.extend_props, 'assignee_bot_id');
      const assigneeBinding =
        assigneeRaw && typeof assigneeRaw === 'object' ? (assigneeRaw as { binding?: string }).binding ?? null : null;
      const assigneeNorm = typeof assigneeRaw === 'string' ? assigneeRaw : assigneeBotId ?? assigneeBinding ?? null;
      // 透出 assignee_name:绕过群执行时 assignee 常为 bcs 群 id,视图层需用可读 bot 名展示执行者。
      const nodeAssigneeName =
        (typeof effectiveRunInfo.assignee_name === 'string' && effectiveRunInfo.assignee_name.trim()
          ? effectiveRunInfo.assignee_name
          : null) ?? getExtendString(effectiveRunInfo.extend_props, 'assignee_name');
      // 根节点会话/群信息可能在 graph 级 extend_props 兜底继承;非根节点只用自身值,不继承根节点
      // (避免 MISS/未分配节点张冠李戴继承根会话而被当成可下钻)。yaml/workflow 子节点应自带 session。
      const rawGroupId =
        getExtendString(effectiveRunInfo.extend_props, 'group_id', 'coop_group_id', 'source_group_id') ??
        (isRootNode ? rootGroupId : null);
      const nodeSessionId =
        getExtendString(effectiveRunInfo.extend_props, 'session_id', 'group_session_id', 'main_session_id') ??
        (isRootNode ? rootSessionId : null);
      // 单/群判别:协作群 session_id 形如 bcs_grp_xxx:round,单聊形如 agent:main:session:...:user:xxx。
      // 不能再用 group_id 是否存在判断——单 bot 执行也会在 extend_props 带 group_id(群上下文泄漏),
      // 会导致会话消息查询误走协作群端点。统一以 run_mode=coop_group 或 session_id 以 bcs_grp_ 开头为准。
      const isGroupNode = effectiveRunMode === 'coop_group' || nodeSessionId?.startsWith('bcs_grp_') === true;
      const groupId = isGroupNode ? rawGroupId : null;
      const groupName = isGroupNode
        ? getExtendString(effectiveRunInfo.extend_props, 'group_name') ??
          (isRootNode ? rootGroupName : null) ??
          'BCS协作群'
        : null;
      return {
        id: n.node_id,
        name: title,
        sequence: n.sequence ?? 0,
        status: mapNodeStatus(n.status),
        executor: resolveNodeExecutor(effectiveRunInfo),
        executorColor: executorColor(effectiveRunMode),
        runMode: effectiveRunMode ?? null,
        // 下钻触发条件 = session_id 存在(与单/群判别无关):
        // - 协作群(isGroupNode) → 按 group_id 查群成员 + 协作群消息端点
        // - 单 bot → 按 assignee 内部 id(去 :user_id 后缀)走 bots sessions 消息端点
        groupId,
        groupName,
        sessionId: nodeSessionId,
        assignee: assigneeNorm,
        assigneeName: nodeAssigneeName,
        missEvents: nodeMissEvents,
        hungReason: nodeHungReason,
        startedAt: msToDisplay(effectiveRunInfo.start_time),
        endAt: msToDisplay(effectiveRunInfo.end_time),
        timeConsuming: durationDisplay(effectiveRunInfo.start_time, effectiveRunInfo.end_time),
        output: effectiveRunInfo.output ? JSON.stringify(effectiveRunInfo.output) : null,
        outputSummary: resolveOutputSummary(effectiveRunInfo.output, effectiveRunInfo.output_summary),
        outputRender: resolveOutputRender(effectiveRunInfo.output, effectiveRunInfo.output_summary),
        taskSpec: mapNodeTaskSpec(n.task_spec),
        artifacts: (effectiveRunInfo.artifacts ?? []).map((a) => ({
          id: a.artifact_id,
          name: a.name,
          type: a.type,
          url: a.url ?? null,
          summary: a.summary ?? null,
          updatedAt: a.created_at,
        })),
        hasSubTask: !!effectiveRunInfo.child_task_id,
        subTaskId: effectiveRunInfo.child_task_id ?? null,
        stepTraces: mapActionLog(effectiveRunInfo.action_log),
        acceptanceResult: effectiveRunInfo.acceptance_result
          ? {
              verdict: effectiveRunInfo.acceptance_result.verdict,
              acceptancesMetric: effectiveRunInfo.acceptance_result.acceptances_metric ?? [],
              gaps: effectiveRunInfo.acceptance_result.gaps ?? [],
            }
          : null,
      };
    });

  const { dagNodes, dagEdges } = layoutDag(nodes, graphRelations);
  // OKR/多节点任务若存在已完成的「投放实施」节点，产物 Tab 绑定该节点 output；否则回退根节点 output。
  const implementationNode = nodes.find((node) => node.name.includes('投放实施') || node.name.includes('实施投放'));
  const selectedOutputNode = implementationNode?.status === 'done' ? implementationNode : undefined;
  const rootOutputRender = selectedOutputNode?.outputRender ?? defaultRootOutputRender;
  const rootDimensions = resolveOutputDimensions(selectedOutputNode?.output ?? rootRunInfo?.output);
  const rootOutputDimensions = rootDimensions.length ? rootDimensions : undefined;

  const graphOwnerBotId = getExtendString(graphExtProps, 'owner_bot_id') ?? d.owner_bot_id ?? '';
  const graphSourceType =
    (getExtendString(graphExtProps, 'source_type') as TaskDashboardResponse['source_type'] | null) ?? d.source_type;
  const graphOwnerBotName =
    getExtendString(graphExtProps, 'owner_bot_name', 'bot_name') ?? d.owner_bot?.name ?? graphOwnerBotId;
  const graphCreateTime =
    d.create_time ??
    getExtendString(graphExtProps, 'create_time', 'gmt_create') ??
    (rootRunInfo?.start_time ? new Date(rootRunInfo.start_time).toISOString() : '');
  const graphFinishTime =
    d.finish_time ??
    getExtendString(graphExtProps, 'finish_time', 'gmt_modified') ??
    (rootRunInfo?.end_time ? new Date(rootRunInfo.end_time).toISOString() : null);

  const productStatus = mapTaskStatus(d.status);
  // 以下 void 引用避免未使用告警（色表/label 由 UI 层消费）
  void TASK_STATUS_TONES;
  void ARTIFACT_TYPE_LABELS;

  return {
    id: d.task_id ?? rootNode?.task_id ?? rootNode?.node_id ?? '',
    name: meta.title ?? d.task_id ?? rootNode?.task_id ?? '',
    description: ctx.background ?? '',
    goal: goal.objective ?? '',
    objective: goal.objective ?? '',
    acceptances,
    status: productStatus,
    taskType: taskType,
    taskTypeLabel: TASK_TYPE_LABELS[taskType] ?? taskType,
    sourceLabel: SOURCE_LABELS[graphSourceType ?? 'bot'] ?? graphSourceType ?? '',
    ownerBotName: graphOwnerBotName,
    ownerBotId: graphOwnerBotId,
    createdAt: graphCreateTime,
    finishedAt: graphFinishTime,
    loopRound: d.loop_round ?? 0,
    needsAttention: !!d.needs_attention,
    statusReason: d.status_reason ?? null,
    template,
    parentTaskId: d.parent_task_id ?? (execCfg.parent_task_id as string | null) ?? tcCtx?.parent_task_id ?? null,
    mainSessionName: d.main_session?.name ?? (execCfg.main_session_name as string) ?? tcCtx?.main_session_name ?? null,
    progress: computeProgress(d, nodes),
    rootOutputRender,
    rootOutputDimensions,
    artifacts: mapArtifacts(d.artifacts),
    nodes,
    dagNodes,
    dagEdges,
  };
}
