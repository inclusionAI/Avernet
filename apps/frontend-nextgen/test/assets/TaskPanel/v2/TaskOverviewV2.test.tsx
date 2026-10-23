/** @jest-environment jsdom */
// TaskOverviewV2(L1):双视图节点卡/下钻分离/边语义渲染/产物区/折叠任务信息(PRD #1/2/3/4/9)。
import {
  artifactDescriptorCode,
  artifactDescriptorText,
  artifactDescriptorUrlLink,
} from '@/assets/TaskPanel/testdata/artifactFixtures';
import type { TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import type { ResolvedSessionTarget } from '@/assets/TaskPanel/sessionResolution';
import { TaskOverviewV2, type TaskOverviewV2Props } from '@/assets/TaskPanel/v2/TaskOverviewV2';
import type { TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

const nodeArtifacts = (n: Partial<TaskNodeView>): TaskNodeView =>
  ({
    id: 'n-x',
    name: '节点',
    sequence: 1,
    status: 'running',
    hasSubTask: false,
    artifacts: [],
    stepTraces: [],
    ...n,
  }) as TaskNodeView;

const task = {
  id: 't1',
  name: '竞品分析',
  description: '任务背景描述',
  goal: '输出决策建议',
  objective: '输出决策建议',
  acceptances: ['三家竞品'],
  status: 'EXECUTING',
  taskType: 'dynamic',
  taskTypeLabel: '动态',
  sourceLabel: 'Bot',
  ownerBotName: '规划师Bot',
  ownerBotId: 'bot-1',
  ownerUserId: 'user-1',
  createdAt: '2026-10-20 10:00',
  finishedAt: null,
  loopRound: 1,
  needsAttention: true,
  progress: { total: 2, pending: 0, planning: 0, running: 1, done: 1, failed: 0, hung: 0, skipped: 0, percent: 50 },
  artifacts: [
    {
      id: 'root-legacy-1',
      name: '任务根产物',
      type: 'document',
      url: null,
      summary: '根级 legacy 产物',
      updatedAt: '2026-10-01 09:00',
    },
  ],
  mainSessionId: 'agent:main:session:s-main',
  derivedSessionIds: [],
  nodes: [
    nodeArtifacts({
      id: 'n-solo',
      name: '需求澄清与规划',
      sequence: 1,
      status: 'done',
      present: 'merged',
      executor: '规划师Bot',
      runMode: 'single_bot',
    }),
    nodeArtifacts({
      id: 'n-group',
      name: '并行调研执行',
      sequence: 2,
      status: 'running',
      present: 'running',
      executor: '竞品研究员Group',
      runMode: 'coop_group',
      drillable: true,
      needsHitl: true,
      attemptCount: 2,
      sessionId: 'bcs_grp_902:round:2',
      groupId: 'g-902',
      startedAtMs: 1700000000000,
      endAtMs: 1700000005000,
      artifacts: [
        { id: 'art-url', name: '需求说明书', type: 'link', kind: 'url_link', url: 'https://yuque.example/doc1', owner: 'group-902', updatedAt: '2026-10-20T08:53:20.000Z' },
        { id: 'art-code', name: '重构 PR', type: 'other', kind: 'code', url: 'https://git.example/pr/1024', owner: 'bot-coder', updatedAt: '2026-10-20T08:56:40.000Z' },
        { id: 'art-text', name: '阶段结论', type: 'document', kind: 'text', summary: '一段纯文本结论', owner: 'bot-planner', updatedAt: '2026-10-20T08:58:20.000Z' },
      ],
    }),
  ],
  dagNodes: [
    { id: 'n-solo', label: '需求澄清与规划', status: 'done', x: 0, y: 0, isCurrent: false, present: 'merged', short: '需求澄清与规划' },
    { id: 'n-group', label: '并行调研执行', status: 'running', x: 0, y: 100, isCurrent: false, present: 'running', drillable: true, short: '并行调研执…' },
  ],
  dagEdges: [
    { from: 'n-solo', to: 'n-group', semantics: 'rework', reason: 'Owner Bot 裁决 revise: 返工' },
  ],
} as unknown as TaskView;

// fixture Descriptor 断言备份(kind 徽章依赖)
void artifactDescriptorUrlLink;
void artifactDescriptorCode;
void artifactDescriptorText;

const makeNav = (): TaskPanelNav & {
  calls: { root: string[]; select: Array<[string, ResolvedSessionTarget | null]>; drill: string[] };
} => {
  const calls = {
    root: [] as string[],
    select: [] as Array<[string, ResolvedSessionTarget | null]>,
    drill: [] as string[],
  };
  return {
    selectedNodeId: null,
    drillStack: [],
    calls,
    selectRootNode: (id: string) => calls.root.push(id),
    selectNode: (id: string, target: ResolvedSessionTarget | null) => calls.select.push([id, target]),
    drillInto: (id: string) => calls.drill.push(id),
    backToOverview: () => {},
  } as unknown as TaskPanelNav & { calls: typeof calls };
};

const props = (overrides?: Partial<TaskOverviewV2Props>): TaskOverviewV2Props => ({
  task,
  nav: makeNav(),
  apiBaseUrl: '',
  ...overrides,
});

describe('TaskOverviewV2(L1)', () => {
  it('节点视图默认:卡片渲染执行体 pill/返工徽章/HITL 徽章/完成划线', () => {
    render(<TaskOverviewV2 {...props()} />);
    expect(screen.getByText('需求澄清与规划')).toBeTruthy();
    expect(screen.getByText('并行调研执行')).toBeTruthy();
    expect(screen.getByText('竞品研究员Group')).toBeTruthy();
    expect(screen.getByText('点击查看会话')).toBeTruthy();
    // I2:返工次数 = attemptCount-1(action_log 的 attempt 从 1 起 = 首发);attemptCount=2 → 第 1 次返工
    expect(screen.getByText('第 1 次返工')).toBeTruthy();
    // attemptCount 1(仅首发,无返工)不显示返工徽章
    expect(screen.queryByText('第 0 次返工')).toBeNull();
    expect(screen.getByText('人工处理')).toBeTruthy();
    // demo 语义:完成态(present done/merged)节点标题划线
    const doneTitle = screen.getByText('需求澄清与规划');
    expect((doneTitle as HTMLElement).style.textDecoration).toContain('line-through');
  });

  it('根节点恢复宿主会话；其他节点保持会话联动与下钻', () => {
    const nav = makeNav();
    render(<TaskOverviewV2 {...props({ nav })} />);

    fireEvent.click(screen.getByText('需求澄清与规划'));
    expect(nav.calls.root).toEqual(['n-solo']);
    expect(nav.calls.select).toHaveLength(0);
    expect(nav.calls.drill).toHaveLength(0);

    fireEvent.click(screen.getByTitle('下钻节点副屏'));
    expect(nav.calls.select).toHaveLength(1);
    expect(nav.calls.select[0][0]).toBe('n-group');
    expect(nav.calls.select[0][1]).toMatchObject({ sessionId: 'bcs_grp_902:round:2', kind: 'group' });
    expect(nav.calls.drill).toEqual(['n-group']);
  });

  it('下钻按钮仅协作群节点渲染(单聊节点无热区)', () => {
    render(<TaskOverviewV2 {...props()} />);
    const drillButtons = screen.queryAllByTitle('下钻节点副屏');
    expect(drillButtons).toHaveLength(1);
  });

  it('视图切换:切到 DAG 视图后 rework 边为 warning 虚线并带 reason title,存在下钻热区', () => {
    const nav = makeNav();
    render(<TaskOverviewV2 {...props({ nav })} />);
    fireEvent.click(screen.getByTitle('切换到 DAG 视图'));

    const dashed = document.querySelectorAll('path[stroke-dasharray="4 3"]');
    expect(dashed.length).toBeGreaterThanOrEqual(1);

    // 节点可点(selectNode 语义) — SVG 热区以 aria-label 查询
    fireEvent.click(screen.getByLabelText('下钻节点副屏'));
    expect(nav.calls.drill).toEqual(['n-group']);
  });

  it('DAG 视图点击根节点同样只恢复宿主会话', () => {
    const nav = makeNav();
    render(<TaskOverviewV2 {...props({ nav })} />);
    fireEvent.click(screen.getByTitle('切换到 DAG 视图'));

    fireEvent.keyDown(screen.getByLabelText('查看节点 需求澄清与规划'), { key: 'Enter' });

    expect(nav.calls.root).toEqual(['n-solo']);
    expect(nav.calls.select).toHaveLength(0);
  });

  it('产物区:任务级聚合(根 legacy + 节点产物)去重计数,kind 徽章与链接', () => {
    render(<TaskOverviewV2 {...props()} />);
    // 1 root legacy + 3 node artifacts
    expect(screen.getByTitle('产物计数').textContent).toBe('4 项');
    expect(screen.getByText('需求说明书')).toBeTruthy();
    expect(screen.getByText('任务根产物')).toBeTruthy();
    // kind 徽章
    expect(screen.getByText('链接')).toBeTruthy();
    expect(screen.getByText('代码')).toBeTruthy();
    expect(screen.queryByText('文件')).toBeNull(); // 无 file kind 产物
    // url_link 卡可打开
    expect(screen.getByTitle('打开产物:需求说明书').getAttribute('href')).toBe('https://yuque.example/doc1');
  });

  it('纯文本产物可预览，并在下载时提示不支持', async () => {
    render(<TaskOverviewV2 {...props()} />);

    expect(screen.getByRole('button', { name: '预览产物 阶段结论' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '下载产物 阶段结论' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '预览产物 阶段结论' }));
    expect((await screen.findByTestId('task-artifact-plain-text')).textContent).toContain('一段纯文本结论');

    fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
    expect((await screen.findByRole('alert')).textContent).toContain('纯文本类型的产物不支持下载。');

    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    fireEvent.click(screen.getByRole('button', { name: '下载产物 阶段结论' }));
    expect((await screen.findByRole('alert')).textContent).toContain('纯文本类型的产物不支持下载。');
  });

  it('session_file 产物可在 TaskPanel 内预览，并可关闭抽屉', async () => {
    const sessionArtifact = {
      id: 'session-pdf',
      name: '附件.pdf',
      type: 'document' as const,
      kind: 'file' as const,
      owner: 'bot-1',
      updatedAt: '2026-10-20T11:00:00.000Z',
      fileRef: {
        storageType: 'session_file' as const,
        storageId: 'sess-9',
        fileId: 'f-1',
        fileType: 'application/pdf',
      },
    };
    render(<TaskOverviewV2 {...props({ task: { ...task, artifacts: [...task.artifacts, sessionArtifact] } })} />);

    fireEvent.click(screen.getByRole('button', { name: '预览产物 附件.pdf' }));

    expect(await screen.findByTestId('task-artifact-preview-drawer')).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTitle('附件.pdf 预览').getAttribute('src')).toBe(
        '/api/v1/collaboration/sessions/sess-9/files/f-1/content?show=true',
      ),
    );
    expect(screen.getByRole('button', { name: '下载产物 附件.pdf' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '关闭产物预览' }));
    expect(screen.queryByTestId('task-artifact-preview-drawer')).toBeNull();
  });

  it('PRD 两区结构:只有步骤与产物,无「任务产出」「任务信息」区', () => {
    render(<TaskOverviewV2 {...props()} />);
    expect(screen.getByText('步骤')).toBeTruthy();
    expect(screen.getByText('产物')).toBeTruthy();
    expect(screen.queryByText('任务产出')).toBeNull();
    expect(screen.queryByText('任务信息')).toBeNull();
    expect(screen.queryByText('任务背景描述')).toBeNull();
  });
});

describe('useOverviewView 本地视图偏好(套件自洽回归)', () => {
  it('默认节点视图;DAG 视图切换后再次切回节点视图', () => {
    render(<TaskOverviewV2 {...props()} />);
    expect(screen.queryByTitle('切换到节点视图')).toBeNull(); // 已在节点视图
    fireEvent.click(screen.getByTitle('切换到 DAG 视图'));
    expect(screen.getByTitle('切换到节点视图')).toBeTruthy();
    fireEvent.click(screen.getByTitle('切换到节点视图'));
    expect(screen.getByTitle('切换到 DAG 视图')).toBeTruthy();
  });
});
