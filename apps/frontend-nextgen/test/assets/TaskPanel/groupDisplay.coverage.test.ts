/** @jest-environment node */
import type { GroupMemberInfoByGroupId } from '@/assets/TaskPanel/groupDisplay';
import { collectTaskGroupIds, enrichTaskGroupDisplay } from '@/assets/TaskPanel/groupDisplay';
import type { TaskArtifactView, TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import { describe, expect, it } from '@jest/globals';

// groupDisplay 富化分支补充:resolveArtifactOwnerLabel 的全部回落序(owner 缺失 → 群名册 →
// actor 名册 → 任务 Owner → 无节点宿主 → 群节点成员 → 节点 assignee → 节点群名 → 原样 ID)。

const taskView = (overrides?: Partial<TaskView>): TaskView =>
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
    ownerBotName: '任务OwnerBot',
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
    sourceGroupId: null,
    mainSessionName: null,
    mainSessionId: null,
    derivedSessionIds: [],
    ...overrides,
  }) as TaskView;

const nodeView = (overrides?: Partial<TaskNodeView>): TaskNodeView =>
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

const artifact = (id: string, owner: string | null): TaskArtifactView =>
  ({
    id,
    name: id,
    type: 'document',
    updatedAt: '2026-11-01T00:00:00Z',
    owner,
    kind: 'text',
  }) as TaskArtifactView;

const groups: GroupMemberInfoByGroupId = {
  'g-912': { names: ['甲', '乙'], nameByActorId: { 'actor-9': '甲', '': '占位无效名' } },
};

describe('collectTaskGroupIds 群抽取', () => {
  it('按 bbs/群节点/可下钻节点抽取 group_id/assignee,并 trim 去重', () => {
    const ids = collectTaskGroupIds(
      taskView({
        nodes: [
          // bbs 执行模态也是群节点 → assignee 作为群 id
          nodeView({ id: 'n-bbs', runMode: 'bbs', groupId: null, assignee: ' bbs-grp ' }),
          // 协作群节点带 group_id
          nodeView({ id: 'n-coop', runMode: 'coop_group', groupId: 'g-coop' }),
          // 可下钻但无 group_id/assignee → 跳过
          nodeView({ id: 'n-drill-empty', drillable: true }),
          // 单聊节点不参与
          nodeView({ id: 'n-solo', runMode: 'single_bot' }),
        ],
      }),
    );
    expect(ids).toEqual(['bbs-grp', 'g-coop']);
  });
});

describe('enrichTaskGroupDisplay 成员名富化', () => {
  it('groups 为空时原样返回 task', () => {
    const task = taskView({
      nodes: [nodeView({ id: 'n-coop', runMode: 'coop_group', groupId: 'g-912' })],
    });
    expect(enrichTaskGroupDisplay(task, {})).toBe(task);
  });

  it('群节点补充 executorLabel;owner 命中群名册/actor 名册/任务 Owner 的产物补 label', () => {
    const groupNode = nodeView({ id: 'n-group', runMode: 'coop_group', groupId: 'g-912', assignee: 'fallback' });
    const enriched = enrichTaskGroupDisplay(
      taskView({
        nodes: [groupNode],
        artifacts: [
          artifact('a-none', null), // owner 缺失 → 不动
          artifact('a-group', 'g-912'), // 命中群名册 → 成员名列表
          artifact('a-actor', 'actor-9'), // 命中 actor 名册 → 成员名
          artifact('a-owner', 'bot-owner'), // 命中任务 Owner bot
        ],
      }),
      groups,
    );
    // 群节点 executorLabel = 群成员名列表
    expect(enriched.nodes[0]!.executorLabel).toBe('甲、乙');
    const byId = new Map(enriched.artifacts.map((a) => [a.id, a]));
    // owner 为空,label 也为空 → 与 owner 相等,保持原对象
    expect(byId.get('a-none')).not.toHaveProperty('ownerLabel');
    expect(byId.get('a-none')!.owner).toBeNull();
    expect(byId.get('a-group')!.ownerLabel).toBe('甲、乙');
    expect(byId.get('a-actor')!.ownerLabel).toBe('甲');
    expect(byId.get('a-owner')!.ownerLabel).toBe('任务OwnerBot');
  });

  it('无节点宿主(任务级产物):owner 命中来源群时回退主会话名,否则原样', () => {
    const enriched = enrichTaskGroupDisplay(
      taskView({
        sourceGroupId: 'g-src',
        mainSessionName: '主会话名称',
        artifacts: [artifact('a-src-group', 'g-src'), artifact('a-unknown', 'no-such-owner')],
      }),
      groups,
    );
    const byId = new Map(enriched.artifacts.map((a) => [a.id, a]));
    expect(byId.get('a-src-group')!.ownerLabel).toBe('主会话名称');
    // 未命中任何名册 → label 兜底回 owner 本身 → 对象保持原样
    expect(byId.get('a-unknown')).not.toHaveProperty('ownerLabel');
    expect(byId.get('a-unknown')!.owner).toBe('no-such-owner');
  });

  it('节点级产物:群节点命中群体名册;单聊节点回退 assigneeName/群名/原样', () => {
    const groupNode = nodeView({
      id: 'n-group',
      runMode: 'coop_group',
      groupId: 'g-other', // 不在名册内 → memberLabel 为空
      groupName: '名册外自定义群',
      assignee: 'some-assignee',
      assigneeName: '群执行者',
      artifacts: [
        // 群节点但群不在名册 → 沿解析链回退 owner 命中 assigneeName
        artifact('g-fall-assignee', 'some-assignee'),
        // 无人认领 → 原样 ID
        artifact('g-raw', 'who'),
      ],
    });
    const soloNode = nodeView({
      id: 'n-solo',
      runMode: 'single_bot',
      assignee: 'bot-x',
      assigneeName: '执行者Bot',
      artifacts: [
        artifact('s-assignee', 'bot-x'), // 命中节点 assignee → assigneeName
        artifact('s-raw', 'who'), // 全链未命中 → 原样
      ],
    });
    const namedGroupNode = nodeView({
      id: 'n-named',
      runMode: 'coop_group',
      groupId: 'g-912',
      artifacts: [
        artifact('group-direct', 'nobody'), // 群节点命中名册 → 群成员名列表
      ],
    });
    const defaultNameNode = nodeView({
      id: 'n-default',
      runMode: 'coop_group',
      groupId: 'g-default',
      groupName: 'BCS协作群', // 默认占位群名不算可读名 → 原样
      artifacts: [artifact('d-bcs-default', 'g-default')],
    });
    const realGroupNode = nodeView({
      id: 'n-real',
      runMode: 'coop_group',
      // 群 id 不在名册内,避免 owner 命中直接走群体名册;命中节点自定义群名 → 群名
      groupId: 'g-custom',
      groupName: '自定义群名',
      artifacts: [artifact('r-group-name', 'g-custom')],
    });

    const groups2: GroupMemberInfoByGroupId = {
      ...groups,
      'g-empty': { names: [], nameByActorId: {} },
    };
    // 单独校验:g-default 节点的「BCS协作群」占位群名 → label 原样保留
    const enrichedDefault = enrichTaskGroupDisplay(
      taskView({ nodes: [defaultNameNode] }),
      groups2,
    );
    const dBcs = new Map(enrichedDefault.nodes[0]!.artifacts.map((a) => [a.id, a]));
    expect(dBcs.get('d-bcs-default')).not.toHaveProperty('ownerLabel');

    const enriched = enrichTaskGroupDisplay(
      taskView({ nodes: [groupNode, soloNode, namedGroupNode, realGroupNode] }),
      groups2,
    );
    const pick = (nodeIdx: number, artifactId: string) =>
      enriched.nodes[nodeIdx]!.artifacts.find((a) => a.id === artifactId);

    // 群节点群 id 不在名册 → memberLabel null → owner 命中 assigneeName
    expect(pick(0, 'g-fall-assignee')!.ownerLabel).toBe('群执行者');
    // 全链未命中 → 原样
    expect(pick(0, 'g-raw')).not.toHaveProperty('ownerLabel');
    // 单聊节点 assignee 命中 → assigneeName
    expect(pick(1, 's-assignee')!.ownerLabel).toBe('执行者Bot');
    expect(pick(1, 's-raw')).not.toHaveProperty('ownerLabel');
    // 群节点群 id 在名册 → 群成员名列表
    expect(pick(2, 'group-direct')!.ownerLabel).toBe('甲、乙');
    // owner 命中节点自定义群名(非「BCS协作群」占位)→ 直接用群名
    expect(pick(3, 'r-group-name')!.ownerLabel).toBe('自定义群名');
  });

  it('群名册里成员名全为空/空白时群节点不补 executorLabel', () => {
    const task = enrichTaskGroupDisplay(
      taskView({
        nodes: [nodeView({ id: 'n-empty', runMode: 'coop_group', groupId: 'g-empty', assignee: 'g-empty' })],
      }),
      { 'g-empty': { names: ['', '  '], nameByActorId: {} } },
    );
    // joinNames 全空白 → memberLabel null → 节点保持原样
    expect(task.nodes[0]).not.toHaveProperty('executorLabel');
  });
});
