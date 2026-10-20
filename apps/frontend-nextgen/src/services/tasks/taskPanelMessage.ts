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
  // defaultWidth: 420 = V2 副屏初始宽(spec §3.1-2);SDK 支持用户拖宽,420 为初始值。
  const tab = JSON.stringify({ id: `task-${taskId}`, title: safeTitle, closable: true, defaultWidth: 420 });
  const paramsAttr = JSON.stringify(params);
  return `<AixUI type="panel" component="taskPanel.TaskLoopView" ` + `tab='${tab}' params='${paramsAttr}'></AixUI>`;
}

export interface TaskLaunchMessage {
  content: string;
  shouldTriggerBot: boolean;
}

/**
 * 把 Backend 生成的 relay 根任务指令交给当前主 Bot 会话直接执行。
 * Frontend 不解释或重建接力协议，只负责隐藏转发并防止用户内容提前结束 HTML 注释。
 */
export function buildRelayRootExecutionMessage(panelMessage: string, executionInstruction: string): string {
  const relayInstruction = executionInstruction.replace(
    /--!?>/g,
    (closing) => `${closing.slice(0, 2)}\u200b${closing.slice(2)}`,
  );
  return `${panelMessage}\n<!--\n${relayInstruction}\n-->`;
}

/** 中心化保持原副屏消息；relay 返回需要触发当前 Bot 执行的首棒消息。 */
export function buildTaskLaunchMessage(panelMessage: string, record: TaskRecord): TaskLaunchMessage {
  const executionConfig = record.task_info.execution_config;
  if (executionConfig.orchestration_mode !== 'relay') {
    return { content: panelMessage, shouldTriggerBot: false };
  }
  const instruction = executionConfig.root_execution_instruction;
  if (typeof instruction !== 'string' || !instruction.trim()) {
    throw new Error('Backend 未返回 relay 根节点执行指令');
  }
  return {
    content: buildRelayRootExecutionMessage(panelMessage, instruction),
    shouldTriggerBot: true,
  };
}
