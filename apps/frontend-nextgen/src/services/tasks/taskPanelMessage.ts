import type { TaskRecord } from '@/domain/tasks/models';

/**
 * 构造任务副屏声明式 <AixUI type="panel"> 消息串（多任务多 tab）。
 *
 * 机制：引擎 openBusinessPanel 调 `panel.openTab({ id: tab.id || component, title, closable, params })`，
 * 按 id 去重——同 id 更新数据、不同 id 新建 tab。若 <AixUI> 不带 tab.id，则回落常量 component
 * （"taskPanel.TaskLoopView"）→ 所有任务挤同一 tab 互相覆盖（即"副屏只能展示一个任务"）。
 *
 * 故给每个任务 tab 显式 id = `task-${taskId}`：
 * - 每个任务独占一个 tab，切 tab 看不同任务执行详情；
 * - 任务重渲染（loadHistory 拉回 / 重复执行）同 id 只更新不重复开；
 * - 消息进会话 history → loadHistory 拉回 → 各自 tab.id 还原对应 tab，刷新/切会话可恢复。
 *
 * 健壮性：tab.title 是用户可见标签，剔除会破坏单引号 HTML 属性 / JSON 的字符（' < > " `）。
 * 不在此截断——完整标题由副屏 SDK 侧统一做「≤10 字 + hover 全文」展示。
 */
export function buildTaskPanelAixUI(taskId: string, title: string, params: Record<string, unknown>): string {
  // 保留完整标题：副屏 SDK 侧负责「最多展示 10 个字符 + hover 展示全部」，
  // 这里只剔除会破坏 JSON / 单引号 HTML 属性的字符，不再截断。
  const safeTitle = (title ?? '').replace(/[<>'"`]/g, ' ').trim() || '任务';
  const tab = JSON.stringify({ id: `task-${taskId}`, title: safeTitle, closable: true });
  const paramsAttr = JSON.stringify(params);
  return `<AixUI type="panel" component="taskPanel.TaskLoopView" ` + `tab='${tab}' params='${paramsAttr}'></AixUI>`;
}

export interface RelayRootExecutionInput {
  taskId: string;
  nodeId: string;
  holderId: string;
  instruction: string;
  objective: string;
  acceptances: string[];
}

export interface TaskLaunchMessageInput {
  holderId: string;
  instruction: string;
  objective: string;
  acceptances: string[];
}

export interface TaskLaunchMessage {
  content: string;
  shouldTriggerBot: boolean;
}

/**
 * 把 relay 根任务交给当前主 Bot 会话直接执行。
 * `<AixUI>` 保持用户可见；执行模式与协议放在隐藏注释中，不进入确认卡或聊天正文。
 */
export function buildRelayRootExecutionMessage(panelMessage: string, input: RelayRootExecutionInput): string {
  const relayInstruction = [
    '[task-execute]',
    '【分布式接力闭环】orchestration_mode=relay；这是主 Bot 当前会话中的首棒任务，不要再创建任务，也不要等待 TaskService 派发根节点。',
    `task_id=${input.taskId}; node_id=${input.nodeId}; holder_id=${input.holderId}`,
    `目标：${input.objective}`,
    `执行指令：${input.instruction}`,
    `验收标准：${JSON.stringify(input.acceptances)}`,
    '直接执行本棒；加载 task-loop 的分布式接力段后，严格按固定 S1-S8 执行，不得创建第二个任务，不得改走中心化节点终态协议。',
    `S1/8 解析任务最新上下文：GET {backend}/api/v1/collaboration/tasks/${input.taskId}/context；{backend} 必须解析为 task-loop 分布式接力协议常量 RELAY_BACKEND_BASE_URL，不得使用页面 origin 或注入环境值替代。本棒正常链路只读取这一次，之后不得重复 GET context。`,
    'S2/8 计算当前GAP：基于 S1 TaskContext 本地推理当前未覆盖范围；禁止调用上报接口。',
    'S3/8 Bot能力匹配：基于 IDENTITY.md 职责、已激活 Skills 和工具真实可用状态计算 actual_goal 或 DECLINED；禁止调用上报接口。',
    'S4/8 任务执行并统一上报：只执行 actual_goal，执行完成后 POST EXECUTION_RESULT 记录 actual_goal/output/acceptance_result 并保存 relay_turn；DECLINED 只上报 execution_decision。',
    'S5/8 更新GAP：用 S1 的 all_done_output 合并本节点事实，本地重算 gaps；禁止再次 GET context，也禁止调用上报接口。',
    'S6/8 解析下一棒：有 GAP 则本地构造唯一 next_task_spec，无 GAP 则 next_task_spec=null；禁止调用上报接口。',
    'S7/8 搜推并指定执行者：先 POST PLAN_RESULT 持久化 gaps/next_task_spec 并保存 target_node_id；仍有 GAP 时 POST /search，再 POST DISPATCH_RESULT 记录 HIT/MISS 与 run_mode/assignee。',
    'S8/8 实际交接：HIT 时 POST /dispatch；MISS 表示已发布 BBS，不得再调用 /dispatch；无 GAP 表示任务收口。',
    '会话阶段输出契约：只允许使用【S1/8 ...】到【S8/8 ...】连续编号，每个阶段最多输出一行事实结论。Skill 加载、协议阅读、工具调用和相同 event_id 的重试都不是新的步骤编号；禁止输出步骤0、步骤0确认、S2-S3 合并编号或协议章节号。',
    'HTTP 调用预算：正常有 GAP 链路最多 6 次调用：1次 context + 3类必要事实上报 EXECUTION_RESULT/PLAN_RESULT/DISPATCH_RESULT + 1次 search + 1次 dispatch；无 GAP 链路不得调用 search、DISPATCH_RESULT 或 dispatch。除确定性错误重试外，不得增加进度型或探测型调用。',
  ]
    .join('\n')
    .replace(/--!?>/g, (closing) => `${closing.slice(0, 2)}\u200b${closing.slice(2)}`);
  return `${panelMessage}\n<!--\n${relayInstruction}\n-->`;
}

/** 中心化保持原副屏消息；relay 返回需要触发当前 holder 执行的首棒消息。 */
export function buildTaskLaunchMessage(
  panelMessage: string,
  record: TaskRecord,
  input: TaskLaunchMessageInput,
): TaskLaunchMessage {
  const executionConfig = record.task_info.execution_config;
  if (executionConfig.orchestration_mode !== 'relay') {
    return { content: panelMessage, shouldTriggerBot: false };
  }
  return {
    content: buildRelayRootExecutionMessage(panelMessage, {
      ...input,
      taskId: record.task_id,
      nodeId: executionConfig.root_node_id ?? record.task_id,
    }),
    shouldTriggerBot: true,
  };
}
