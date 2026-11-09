/** @jest-environment node */
import { resolveNodeSessionTarget } from '@/assets/TaskPanel/sessionResolution';
import type { TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import { describe, expect, it } from '@jest/globals';

// 补充分支:未知 run_mode 节点在存在主会话时的保守回退(bot 形态与 bcs_grp 群形态主会话)。

const view = (overrides?: Partial<TaskView>): TaskView =>
  ({
    id: 't1',
    name: '任务',
    description: '',
    goal: '',
    objective: '',
    acceptances: [],
    status: 'EXECUTING',
    taskType: 'dynamic',
    taskTypeLabel: '动态',
    sourceLabel: '',
    ownerBotName: 'OwnerBot',
    ownerBotId: 'bot-owner',
    ownerUserId: 'user-1',
    createdAt: '',
    finishedAt: null,
    loopRound: 1,
    needsAttention: false,
    progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    artifacts: [],
    nodes: [],
    dagNodes: [],
    dagEdges: [],
    mainSessionId: 'agent:main:session:s-main',
    sourceGroupId: null,
    derivedSessionIds: [],
    ...overrides,
  }) as TaskView;

const node = (overrides?: Partial<TaskNodeView>): TaskNodeView =>
  ({
    id: 'n1',
    name: '节点',
    sequence: 1,
    status: 'running',
    artifacts: [],
    hasSubTask: false,
    stepTraces: [],
    ...overrides,
  }) as TaskNodeView;

describe('未知 run_mode 的主会话保守回退', () => {
  it('run_mode 缺失且主会话为单聊 → kind bot,botId 取任务 Owner', () => {
    const t = resolveNodeSessionTarget(
      node({ runMode: null, sessionId: null, startedAtMs: 1, endAtMs: 2 }),
      view(),
    );
    expect(t).toMatchObject({
      sessionId: 'agent:main:session:s-main',
      kind: 'bot',
      groupId: null,
      botId: 'bot-owner',
      startTime: 1,
      endTime: 2,
    });
  });

  it('run_mode 缺失且主会话为 bcs_grp 群 → kind group,groupId 回退任务来源群', () => {
    const t = resolveNodeSessionTarget(
      node({ runMode: null, sessionId: null }),
      view({ mainSessionId: 'bcs_grp_m1:round', sourceGroupId: 'g-src' }),
    );
    expect(t).toMatchObject({
      sessionId: 'bcs_grp_m1:round',
      kind: 'group',
      groupId: 'g-src',
      // 未知 run_mode 的保守回退统一按任务 Owner 定位 bot(与 coop_group/bbs 分支口径不同)
      botId: 'bot-owner',
    });
  });

  it('bbs 节点无自身 session:派生会话回退主会话时群形态同样归一为 group', () => {
    const t = resolveNodeSessionTarget(
      node({ runMode: 'bbs', sessionId: null }),
      view({ mainSessionId: 'bcs_grp_m2:round', sourceGroupId: 'g-src' }),
    );
    // coop_group/bbs 的主会话回退按任务 Owner 定位 bot
    expect(t).toMatchObject({ sessionId: 'bcs_grp_m2:round', kind: 'group', groupId: 'g-src', botId: 'bot-owner' });
  });
});
