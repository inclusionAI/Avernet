/** @jest-environment node */
import type { TaskDashboardResponse } from '@/assets/TaskPanel/contract';
import { mapDashboard } from '@/assets/TaskPanel/taskPanelMapper';
import { describe, expect, it } from '@jest/globals';

// 兜底分支补充用例：只覆盖既有测试未触达的边界分支(bbs 执行模态、分钟/小时级时长、
// 顶层标量 output、空白历史 output、FAILED/CANCELLED 节点态、DAG 环兜底、yaml execution_graph 根节点回退)。

const baseDashboard = (overrides?: Partial<TaskDashboardResponse>): TaskDashboardResponse =>
  ({
    task_id: 'task-coverage',
    run_id: 1,
    status: 'RUNNING',
    needs_attention: false,
    task_type: 'dynamic',
    source_type: 'bot',
    owner_user_id: 'user-1',
    owner_bot_id: 'bot-owner',
    task_spec: { metadata: { title: '边界任务' }, goal: { objective: '目标', acceptances: [] } },
    create_time: '2026-11-01T10:00:00+08:00',
    finish_time: null,
    loop_round: 1,
    progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    tasks: [],
    relations: [],
    ...overrides,
  }) as TaskDashboardResponse;

const nodeSpec = () => ({
  metadata: { title: '节点' },
  goal: { objective: '', acceptances: [] },
});

describe('taskPanelMapper 边界分支补充', () => {
  it('bbs 执行模态节点使用红色头像底色', () => {
    const task = mapDashboard(
      baseDashboard({
        tasks: [
          {
            node_id: 'n-bbs',
            task_id: 'task-coverage',
            sequence: 1,
            status: 'RUNNING',
            run_info: { run_mode: 'bbs', assignee: 'bbs-actor' },
            task_spec: nodeSpec(),
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.nodes[0]!.runMode).toBe('bbs');
    expect(task.nodes[0]!.executorColor).toBe('#F53F3F');
  });

  it('时长展示覆盖分钟与小时级(>= 1 分钟不再按秒展示)', () => {
    const task = mapDashboard(
      baseDashboard({
        tasks: [
          {
            node_id: 'n-minutes',
            task_id: 'task-coverage',
            sequence: 1,
            status: 'DONE',
            run_info: { start_time: 0, end_time: 5 * 60_000 },
            task_spec: nodeSpec(),
          },
          {
            node_id: 'n-hours',
            task_id: 'task-coverage',
            sequence: 2,
            status: 'DONE',
            run_info: { start_time: 0, end_time: 90 * 60_000 },
            task_spec: nodeSpec(),
          },
          {
            // 结束时间早于开始时间(脏数据)→ 不展示耗时
            node_id: 'n-negative',
            task_id: 'task-coverage',
            sequence: 3,
            status: 'DONE',
            run_info: { start_time: 2_000, end_time: 1_000 },
            task_spec: nodeSpec(),
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    const byId = new Map(task.nodes.map((n) => [n.id, n.timeConsuming]));
    expect(byId.get('n-minutes')).toBe('5分钟');
    expect(byId.get('n-hours')).toBe('1小时30分钟');
    expect(byId.get('n-negative')).toBeNull();
  });

  it('顶层 output 为标量(数字)视为非空产物;根节点空白 output 文本回退 output_summary', () => {
    const task = mapDashboard(
      baseDashboard({
        output: 123,
        tasks: [
          {
            node_id: 'n-root',
            task_id: 'task-coverage',
            sequence: 1,
            status: 'DONE',
            run_info: { output: '  ', output_summary: '根节点小结' },
            task_spec: nodeSpec(),
          },
        ],
      } as unknown as TaskDashboardResponse),
    );
    // 数值型顶层 output 不算「无产物」,非结构化也不产生维度卡片
    expect(task.rootOutputDimensions).toBeUndefined();
    // 根节点 output 为空白字符串 → 回退 output_summary 作为整块渲染源
    expect(task.rootOutputRender).toBe('根节点小结');
  });

  it('节点 FAILED/CANCELLED 状态直通为 failed/cancelled', () => {
    const task = mapDashboard(
      baseDashboard({
        tasks: [
          {
            node_id: 'n-failed',
            task_id: 'task-coverage',
            sequence: 1,
            status: 'FAILED',
            task_spec: nodeSpec(),
          },
          {
            node_id: 'n-cancelled',
            task_id: 'task-coverage',
            sequence: 2,
            status: 'CANCELLED',
            task_spec: nodeSpec(),
          },
        ] as TaskDashboardResponse['tasks'],
      }),
    );
    expect(task.nodes[0]!.status).toBe('failed');
    expect(task.nodes[1]!.status).toBe('cancelled');
  });

  it('relations 成环(全节点入度>0)时以首节点兜底分层,不产生死循环', () => {
    const task = mapDashboard(
      baseDashboard({
        tasks: [
          { node_id: 'c-a', task_id: 'task-coverage', sequence: 1, status: 'RUNNING', task_spec: nodeSpec() },
          { node_id: 'c-b', task_id: 'task-coverage', sequence: 2, status: 'RUNNING', task_spec: nodeSpec() },
        ] as TaskDashboardResponse['tasks'],
        relations: [
          { src_id: 'c-a', dst_id: 'c-b', type: 'DEPENDENCY' },
          { src_id: 'c-b', dst_id: 'c-a', type: 'DEPENDENCY' },
        ] as TaskDashboardResponse['relations'],
      }),
    );
    // 两个节点都完成分层并被渲染,两条环形依赖边也如实透出
    expect(task.dagNodes.map((n) => n.id).sort()).toEqual(['c-a', 'c-b']);
    expect(task.dagEdges).toHaveLength(2);
  });

  it('yaml 任务下钻 execution_graph:内部图根节点缺失 task_id 匹配时回退首节点', () => {
    const task = mapDashboard(
      baseDashboard({
        task_type: 'yaml',
        extend_props: { owner_bot_id: 'bot-graph' },
        tasks: [
          {
            node_id: 'task-coverage',
            task_id: 'task-coverage',
            sequence: 1,
            status: 'RUNNING',
            task_spec: nodeSpec(),
          },
        ] as TaskDashboardResponse['tasks'],
        execution_graph: {
          tasks: [
            {
              // execution_graph 未复用顶层 node_id → 展示根节点回退取内部图首节点
              node_id: 'state-start',
              task_id: 'task-coverage',
              sequence: 0,
              status: 'RUNNING',
              task_spec: nodeSpec(),
            },
          ],
          relations: [],
        },
      }),
    );
    // 展示节点来自 execution_graph,根节点回退生效(继承 graph 级 owner_bot_id)
    expect(task.nodes.map((n) => n.id)).toEqual(['state-start']);
    expect(task.nodes[0]!.assignee).toBe('bot-graph');
    // 无子节点时内部图任务也按根节点口径给 fallback 兜底
    expect(task.nodes[0]!.sessionId).toBeNull();
  });
});
