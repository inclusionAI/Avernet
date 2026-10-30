/** @jest-environment node */
// 协作群展示名富化的分组、成员映射与产物 Owner 兜底。
import { collectTaskGroupIds, enrichTaskGroupDisplay } from '@/assets/TaskPanel/groupDisplay';
import type { TaskArtifactView, TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import { describe, expect, it } from '@jest/globals';

const task = (overrides: Partial<TaskView> = {}, nodes: TaskNodeView[] = []): TaskView =>
  ({
    id: 'task-1',
    name: '任务',
    description: '',
    goal: '',
    objective: '',
    acceptances: [],
    status: 'EXECUTING',
    taskType: 'dynamic',
    taskTypeLabel: '动态',
    sourceLabel: '',
    ownerBotName: '',
    ownerBotId: 'bot-owner',
    ownerUserId: 'user-1',
    createdAt: '',
    finishedAt: null,
    loopRound: 1,
    needsAttention: false,
    progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    artifacts: [],
    nodes,
    dagNodes: [],
    dagEdges: [],
    mainSessionName: null,
    sourceGroupId: null,
    ...overrides,
  }) as TaskView;

const node = (overrides: Partial<TaskNodeView> = {}): TaskNodeView =>
  ({
    id: 'node-1',
    name: '节点',
    sequence: 1,
    status: 'running',
    artifacts: [],
    hasSubTask: false,
    stepTraces: [],
    ...overrides,
  }) as TaskNodeView;

const artifact = (owner: string | null): TaskArtifactView =>
  ({
    id: 'artifact-1',
    name: '产物',
    type: 'document',
    owner,
    updatedAt: '',
  }) as TaskArtifactView;

describe('collectTaskGroupIds', () => {
  it('只收集协作群节点的 groupId/assignee 并去重', () => {
    const ids = collectTaskGroupIds(
      task({}, [
        node({ id: 'g1', runMode: 'coop_group', groupId: ' g-1 ' }),
        node({ id: 'g1-dup', runMode: 'coop_group', groupId: 'g-1' }),
        node({ id: 'g2', drillable: true, groupId: null, assignee: ' g-2 ' }),
        node({ id: 'bbs', runMode: 'bbs', groupId: '   ', assignee: null }),
        node({ id: 'session', groupId: null, sessionId: 'bcs_grp_session' }),
        node({ id: 'bot', runMode: 'single_bot', groupId: 'g-bot' }),
      ]),
    );
    expect(ids).toEqual(['g-1', 'g-2', 'bcs_grp_session']);
  });

  it('group 节点 sessionId 非 bcs_grp_ 前缀 → groupSessionGroupId 返回 null,不计入', () => {
    const ids = collectTaskGroupIds(
      task({}, [
        // runMode 判定为群节点,但 groupId/assignee 缺省、sessionId 非 bcs_grp_ → id 解析为 null,不收集
        node({ id: 'plain', runMode: 'coop_group', groupId: null, assignee: null, sessionId: 'regular-session' }),
      ]),
    );
    expect(ids).toEqual([]);
  });
});

describe('enrichTaskGroupDisplay', () => {
  it('groups 为空时直接返回原 task', () => {
    const input = task();
    expect(enrichTaskGroupDisplay(input, {})).toBe(input);
  });

  it('执行体 label 使用群成员名并去重空白', () => {
    const groups = { 'g-1': { names: [' Alice ', 'Alice', ' '], nameByActorId: {} } };
    const result = enrichTaskGroupDisplay(
      task({ sourceGroupId: 'g-1' }, [
        node({ id: 'group', runMode: 'coop_group', groupId: 'g-1' }),
        node({ id: 'assignee-only', drillable: true, groupId: null, assignee: 'g-1' }),
        node({ id: 'no-info', sessionId: 'bcs_grp_x', groupId: null, assignee: null }),
        node({ id: 'bot', runMode: 'single_bot', groupId: 'g-1' }),
      ]),
      groups,
    );
    expect(result.nodes[0].executorLabel).toBe('Alice');
    expect(result.nodes[1].executorLabel).toBe('Alice');
    expect(result.nodes[2].executorLabel).toBeUndefined();
    expect(result.nodes[3].executorLabel).toBeUndefined();
  });

  it('产物 owner 可读名映射优先于任务 Owner 与节点字段', () => {
    const groups = {
      'g-1': { names: ['群成员'], nameByActorId: {} },
      'g-2': { names: [], nameByActorId: { 'bot-worker': '成员甲' } },
    };
    const result = enrichTaskGroupDisplay(
      task(
        { ownerBotName: '规划师', ownerBotId: 'bot-owner' },
        [node({ id: 'group', runMode: 'coop_group', groupId: 'g-2', assignee: 'g-2' })],
      ),
      groups,
    );
    result.artifacts = [
      artifact('g-1'),
      artifact('bot-worker'),
      artifact('bot-owner'),
      artifact(''),
      artifact('unknown'),
    ];
    const enriched = enrichTaskGroupDisplay({ ...result, artifacts: result.artifacts }, groups);
    expect(enriched.artifacts.map((item) => item.ownerLabel)).toEqual([
      '群成员',
      '成员甲',
      '规划师',
      null,
      undefined,
    ]);
  });

  it('无节点时任务主群名或 Owner ID 作为产物兜底', () => {
    const groups = { 'g-1': { names: [], nameByActorId: {} } };
    const withMain = enrichTaskGroupDisplay(
      task({ sourceGroupId: 'g-1', mainSessionName: '主协作群', artifacts: [artifact('g-1'), artifact('unknown')] }),
      groups,
    );
    expect(withMain.artifacts[0].ownerLabel).toBe('主协作群');
    expect(withMain.artifacts[1].ownerLabel).toBeUndefined();

    const withoutMain = enrichTaskGroupDisplay(
      task({ sourceGroupId: 'g-1', mainSessionName: '', artifacts: [artifact('g-1')] }),
      groups,
    );
    expect(withoutMain.artifacts[0].ownerLabel).toBeUndefined();
  });

  it('群节点缺信息时回退 assigneeName 与 groupName', () => {
    const groups = { 'g-x': { names: [], nameByActorId: {} } };
    const result = enrichTaskGroupDisplay(
      task(
        { sourceGroupId: 'g-x' },
        [
          node({
            id: 'group',
            runMode: 'coop_group',
            groupId: 'g-x',
            assignee: 'g-x',
            assigneeName: '节点 Bot',
            groupName: 'BCS协作群',
            artifacts: [artifact('g-x')],
          }),
        ],
      ),
      groups,
    );
    expect(result.nodes[0].artifacts[0].ownerLabel).toBe('节点 Bot');

    const visibleName = enrichTaskGroupDisplay(
      task(
        { sourceGroupId: 'g-x' },
        [node({ id: 'group', runMode: 'coop_group', groupId: 'g-x', assignee: 'g-x', assigneeName: ' ', groupName: '业务群', artifacts: [artifact('g-x')] })],
      ),
      groups,
    );
    expect(visibleName.nodes[0].artifacts[0].ownerLabel).toBe('业务群');
  });

  it('群节点产物 owner 不匹配 assignee/groupId 且群无成员名 → 命中最终 return owner 兜底', () => {
    const groups = { 'g-x': { names: [], nameByActorId: {} } };
    const result = enrichTaskGroupDisplay(
      task({ sourceGroupId: 'g-x' }, [
        node({
          id: 'group',
          runMode: 'coop_group',
          groupId: 'g-x',
          assignee: 'g-x',
          artifacts: [artifact('orphan-owner')],
        }),
      ]),
      groups,
    );
    // owner 'orphan-owner' 既非 assignee('g-x')也非 groupId('g-x'),群无成员名 →
    // resolveArtifactOwnerLabel 走到最后 return owner,label===owner 不变 → ownerLabel 未设置
    expect(result.nodes[0].artifacts[0].ownerLabel).toBeUndefined();
  });

  it('群节点带成员名且产物 owner 陌生 → 命中 line 74 groupLabel 兜底返回节点群名', () => {
    const groups = { 'g-1': { names: ['群成员A'], nameByActorId: {} } };
    const result = enrichTaskGroupDisplay(
      task(
        { ownerBotId: 'bot-owner' },
        [
          node({
            id: 'group',
            runMode: 'coop_group',
            groupId: 'g-1',
            artifacts: [artifact('mystery')], // 非群 key、非 actor、非 ownerBot
          }),
        ],
      ),
      groups,
    );
    // owner 'mystery' 无直配群/actor/ownerBot,directGroupLabel null → 命中 isGroupNode 块,
    // memberLabel(节点群 'g-1') = '群成员A' 非空 → 返回 groupLabel(line 74 true)
    expect(result.nodes[0].artifacts[0].ownerLabel).toBe('群成员A');
  });

  it('非群节点的产物 owner 命中 assigneeName 兜底(命中 isGroupNode false 分支)', () => {
    const groups = { 'g-1': { names: ['群成员'], nameByActorId: {} } };
    const result = enrichTaskGroupDisplay(
      task(
        { ownerBotId: 'bot-owner' },
        [
          node({
            id: 'bot',
            runMode: 'single_bot',
            groupId: null,
            assignee: 'bot-worker',
            assigneeName: '执行Bot',
            artifacts: [artifact('bot-worker')],
          }),
        ],
      ),
      groups,
    );
    // 非群节点 → isGroupNode false → 跳过群名块 → owner===assignee && assigneeName 非空 → 返回 '执行Bot'
    expect(result.nodes[0].artifacts[0].ownerLabel).toBe('执行Bot');
  });

  it('label 与原始 owner 相同时保持产物对象不变', () => {
    const groups = { 'g-1': { names: ['g-1'], nameByActorId: {} } };
    const input = task({ artifacts: [artifact('g-1')] }, [node({ id: 'group', runMode: 'coop_group', groupId: 'g-1' })]);
    const result = enrichTaskGroupDisplay(input, groups);
    expect(result.artifacts[0]).toBe(input.artifacts[0]);
  });
});
