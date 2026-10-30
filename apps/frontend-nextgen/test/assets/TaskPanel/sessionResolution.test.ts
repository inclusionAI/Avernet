/** @jest-environment node */
import type { TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import { resolveNodeSessionTarget } from '@/assets/TaskPanel/sessionResolution';

const baseView = (overrides?: Partial<TaskView>): TaskView =>
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
    ownerBotName: '规划师',
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
    startedAtMs: 1700000000000,
    endAtMs: 1700000005000,
    ...overrides,
  }) as TaskNodeView;

describe('resolveNodeSessionTarget', () => {
  it('①节点自带 session 优先:群节点 → kind group(带 groupId)', () => {
    const t = resolveNodeSessionTarget(
      node({ sessionId: 'bcs_grp_902:round:2', groupId: 'g-902', startedAtMs: 1, endAtMs: 2 }),
      baseView(),
    );
    expect(t).toMatchObject({ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902', startTime: 1, endTime: 2 });
  });

  it('①节点自带单聊 session → kind bot(botId 取节点 assignee 优先)', () => {
    const t = resolveNodeSessionTarget(
      node({ sessionId: 'agent:main:session:s-1', assignee: 'bot-worker' }),
      baseView(),
    );
    expect(t).toMatchObject({ sessionId: 'agent:main:session:s-1', kind: 'bot', botId: 'bot-worker' });
  });

  it('①群节点缺 groupId → kind group,groupId 落 view.sourceGroupId', () => {
    const t = resolveNodeSessionTarget(
      node({ sessionId: 'bcs_grp_902:round:2', groupId: null }),
      baseView({ sourceGroupId: 'g-902' }),
    );
    expect(t).toMatchObject({ kind: 'group', groupId: 'g-902' });
  });

  it('②无节点 session 且单聊 run_mode → 任务 main_session 兜底', () => {
    const t = resolveNodeSessionTarget(node({ runMode: 'single_bot' }), baseView());
    expect(t).toMatchObject({ sessionId: 'agent:main:session:s-main', kind: 'bot', botId: 'bot-owner' });
  });

  it('③群/bbs 无节点 session:derived_session_ids 唯一 → 精确;多个 → 回退 main_session(无法唯一定位)', () => {
    const unique = resolveNodeSessionTarget(
      node({ runMode: 'coop_group' }),
      baseView({ derivedSessionIds: ['bcs_grp_x:round:1'], sourceGroupId: 'g-x' }),
    );
    expect(unique).toMatchObject({ sessionId: 'bcs_grp_x:round:1', kind: 'group', groupId: 'g-x' });

    const ambiguous = resolveNodeSessionTarget(
      node({ runMode: 'coop_group' }),
      baseView({ derivedSessionIds: ['bcs_grp_x:round:1', 'bcs_grp_y:round:1'] }),
    );
    expect(ambiguous).toMatchObject({ sessionId: 'agent:main:session:s-main', kind: 'bot' });
  });

  it('全链无果 → null(调用方不跳转,Review Focus #2 前置)', () => {
    expect(resolveNodeSessionTarget(node({ runMode: 'coop_group' }), baseView({ mainSessionId: null, derivedSessionIds: [] }))).toBeNull();
    expect(resolveNodeSessionTarget(node({ runMode: 'single_bot' }), baseView({ mainSessionId: null }))).toBeNull();
    expect(resolveNodeSessionTarget(node({}), baseView({ mainSessionId: null }))).toBeNull();
  });

  it('缺失时间窗与节点 assignee 时回落到任务 owner', () => {
    const target = resolveNodeSessionTarget(
      node({ sessionId: 'session-bot', assignee: null, startedAtMs: null, endAtMs: null }),
      baseView(),
    );
    expect(target).toMatchObject({ kind: 'bot', botId: 'bot-owner', startTime: null, endTime: null });

    const groupWithoutId = resolveNodeSessionTarget(
      node({ sessionId: 'bcs_grp_902:round:1', groupId: null, startedAtMs: null, endAtMs: null }),
      baseView({ sourceGroupId: null }),
    );
    expect(groupWithoutId).toMatchObject({ kind: 'group', groupId: null, botId: null, startTime: null, endTime: null });
  });

  it('single_bot 缺 assignee 时回落到 owner Bot', () => {
    const target = resolveNodeSessionTarget(node({ runMode: 'single_bot', assignee: null }), baseView());
    expect(target).toMatchObject({ kind: 'bot', botId: 'bot-owner' });
  });

  it('派生单会话非群串时 kind unknown，空派生会话回落群主会话', () => {
    const uniqueBot = resolveNodeSessionTarget(
      node({ runMode: 'coop_group', groupId: null }),
      baseView({ derivedSessionIds: ['not-group-session'], sourceGroupId: 'g-source' }),
    );
    expect(uniqueBot).toMatchObject({ kind: 'unknown', groupId: 'g-source', botId: null });

    const groupMain = resolveNodeSessionTarget(
      node({ runMode: 'coop_group', groupId: null }),
      baseView({ derivedSessionIds: undefined, mainSessionId: 'bcs_grp_main', sourceGroupId: 'g-main' }),
    );
    expect(groupMain).toMatchObject({ kind: 'group', groupId: 'g-main', botId: 'bot-owner' });
  });

  it('owner 与 sourceGroupId 均缺失时可解析为 null', () => {
    const directBot = resolveNodeSessionTarget(
      node({ sessionId: 'session-bot', assignee: null }),
      baseView({ ownerBotId: null as never }),
    );
    expect(directBot).toMatchObject({ kind: 'bot', botId: null });

    const singleBot = resolveNodeSessionTarget(
      node({ runMode: 'single_bot', assignee: null }),
      baseView({ ownerBotId: null as never }),
    );
    expect(singleBot).toMatchObject({ kind: 'bot', botId: null });

    const uniqueUnknown = resolveNodeSessionTarget(
      node({ runMode: 'coop_group', groupId: null }),
      baseView({ derivedSessionIds: ['plain-session'], sourceGroupId: null, ownerBotId: null as never }),
    );
    expect(uniqueUnknown).toMatchObject({ kind: 'unknown', groupId: null, botId: null });

    const groupMain = resolveNodeSessionTarget(
      node({ runMode: 'coop_group', groupId: null }),
      baseView({ derivedSessionIds: [], mainSessionId: 'bcs_grp_fallback', sourceGroupId: null, ownerBotId: null as never }),
    );
    expect(groupMain).toMatchObject({ kind: 'group', groupId: null, botId: null });
  });

  it('未知 run_mode 分别回落群主会话与单 Bot 主会话', () => {
    const groupFallback = resolveNodeSessionTarget(
      node({ runMode: 'unknown_mode', groupId: null }),
      baseView({ mainSessionId: 'bcs_grp_fallback', sourceGroupId: null, ownerBotId: null as never }),
    );
    expect(groupFallback).toMatchObject({ kind: 'group', groupId: null, botId: null });

    const botFallback = resolveNodeSessionTarget(
      node({ runMode: 'unknown_mode', groupId: 'ignored' }),
      baseView({ mainSessionId: 'agent:main:session:bot', sourceGroupId: 'g-ignored' }),
    );
    expect(botFallback).toMatchObject({ kind: 'bot', groupId: null, botId: 'bot-owner' });
  });
});
