import {
  HELP_ROLES,
  HELP_SOURCES,
  type HelpArtifact,
  type HelpBoard,
  type HelpBoardDraft,
  type HelpBot,
  type HelpRole,
  type HelpTask,
  type HelpTaskDraft,
  type HelpTaskStatus,
} from './types';

export const HELP_STATUS: Record<HelpTaskStatus, { label: string; className: string }> = {
  pending: { label: '待处理', className: 'bg-muted text-muted-foreground' },
  running: { label: '协作中', className: 'bg-primary/10 text-primary' },
  attention: { label: '需关注', className: 'bg-destructive/10 text-destructive' },
  done: { label: '已完成', className: 'bg-success/10 text-success' },
};
const ROLE_DETAILS: Record<HelpRole, { instruction: string; output: string; kind: HelpArtifact['kind'] }> = {
  需求采集: {
    instruction: '梳理问题背景、复现条件和验收标准。',
    output: '已整理问题边界、复现路径与验收清单。',
    kind: '需求文档',
  },
  架构: {
    instruction: '分析影响范围，设计技术方案与模块边界。',
    output: '已完成模块边界分析与技术方案，明确变更范围。',
    kind: '方案文档',
  },
  应用: {
    instruction: '对齐应用场景、页面交互及集成方案。',
    output: '已确认应用集成方案与关键交互，覆盖异常场景。',
    kind: '应用方案',
  },
  代码: {
    instruction: '按照方案实现变更，并补充对应测试。',
    output: '已完成示例代码变更与对应测试用例。',
    kind: '代码变更',
  },
  verify: {
    instruction: '执行功能验证、回归测试和验收检查。',
    output: '示例验收检查已通过，验证报告已整理（非真实测试结果）。',
    kind: '验证报告',
  },
  pr: {
    instruction: '整理变更说明、验证记录和 PR 摘要。',
    output: 'PR 摘要已整理，等待人工评审（未创建真实 PR）。',
    kind: 'PR 摘要',
  },
};

export function validateHelpBoard(
  draft: HelpBoardDraft,
  boards: HelpBoard[],
  bots: HelpBot[],
  editingId?: string,
): string | null {
  if (!draft.name.trim()) return '请填写版面名称';
  if (
    boards.some(
      (board) => board.id !== editingId && board.name.trim().toLowerCase() === draft.name.trim().toLowerCase(),
    )
  )
    return '版面名称已存在';
  if (!draft.sources.length || draft.sources.some((source) => !HELP_SOURCES.includes(source)))
    return '请至少启用一个有效任务来源';
  if (!draft.members.length) return '请至少选择一个 Bot';
  if (new Set(draft.members.map((member) => member.botId)).size !== draft.members.length) return '请勿重复选择 Bot';
  if (draft.members.some((member) => !bots.some((bot) => bot.id === member.botId))) return '请选择有效的 Mock Bot';
  if (draft.members.some((member) => !member.roles.length || member.roles.some((role) => !HELP_ROLES.includes(role))))
    return '请为每个选中的 Bot 分配至少一个角色';
  return null;
}

export function createHelpTask(
  id: string,
  board: HelpBoard,
  draft: HelpTaskDraft,
  bots: HelpBot[],
  at: string,
): HelpTask {
  if (!draft.title.trim()) throw new Error('请填写任务标题');
  if (!draft.description.trim()) throw new Error('请填写任务说明');
  if (!board.sources.includes(draft.source)) throw new Error('请选择当前版面已启用的任务来源');
  const stages = HELP_ROLES.filter((role) => board.members.some((member) => member.roles.includes(role))).map(
    (role, index) => ({
      id: `${id}-stage-${index}`,
      role,
      botNames: board.members
        .filter((member) => member.roles.includes(role))
        .map((member) => bots.find((bot) => bot.id === member.botId)?.name ?? member.botId),
      status: 'pending' as const,
      instruction: ROLE_DETAILS[role].instruction,
      output: '',
    }),
  );
  if (!stages.length) throw new Error('请先为版面配置协作 Bot 和角色');
  return {
    ...draft,
    title: draft.title.trim(),
    description: draft.description.trim(),
    sourceRef: draft.sourceRef.trim(),
    id,
    boardId: board.id,
    status: 'pending',
    createdAt: at,
    stages,
    activities: [{ id: `${id}-created`, at, message: `从 ${draft.source} 创建 Mock 求助任务，等待协作。` }],
    artifacts: [],
  };
}

export function helpTaskProgress(task: HelpTask): number {
  return task.stages.length
    ? Math.round((task.stages.filter((stage) => stage.status === 'done').length / task.stages.length) * 100)
    : 0;
}

/** 每次显式操作只模拟一个状态转换，不使用轮询或真实执行接口。 */
export function advanceHelpTask(task: HelpTask, at: string): HelpTask {
  if (task.status === 'done') return task;
  const index = task.stages.findIndex((stage) => stage.status !== 'done');
  if (index < 0) return task;
  const current = task.stages[index];
  const activityId = `${task.id}-activity-${task.activities.length}`;
  if (task.status === 'pending' || task.status === 'attention') {
    return {
      ...task,
      status: 'running',
      stages: task.stages.map((stage, i) => (i === index ? { ...stage, status: 'running', output: '' } : stage)),
      activities: [
        {
          id: activityId,
          at,
          message: `${task.status === 'attention' ? '已模拟解除阻塞，恢复' : '已模拟开始'}「${current.role}」阶段。`,
        },
        ...task.activities,
      ],
    };
  }
  const done = index === task.stages.length - 1;
  const details = ROLE_DETAILS[current.role];
  return {
    ...task,
    status: done ? 'done' : 'running',
    stages: task.stages.map((stage, i) =>
      i === index
        ? { ...stage, status: 'done', output: details.output }
        : i === index + 1
        ? { ...stage, status: 'running' }
        : stage,
    ),
    activities: [
      {
        id: activityId,
        at,
        message: `「${current.role}」已模拟完成。${
          done ? '全部阶段完成，任务已解决。' : `开始「${task.stages[index + 1].role}」阶段。`
        }`,
      },
      ...task.activities,
    ],
    artifacts: [
      ...task.artifacts,
      {
        id: `${current.id}-artifact`,
        name: `${current.role} · ${details.kind}`,
        kind: details.kind,
        content: `【Mock 示例产物】${details.output}\n\n对应任务：${task.title}\n负责 Bot：${current.botNames.join(
          '、',
        )}\n\n此内容仅演示交付形态，不代表已执行真实工作。`,
      },
    ],
  };
}
