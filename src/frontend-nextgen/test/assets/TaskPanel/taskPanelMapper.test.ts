import type { TaskDashboardResponse } from '@/assets/TaskPanel/contract';
import { mapDashboard, mapTaskStatus } from '@/assets/TaskPanel/taskPanelMapper';
import { describe, expect, it } from '@jest/globals';

describe('taskPanelMapper status mapping', () => {
  it('兼容旧运行时状态和新的产品层状态', () => {
    expect(mapTaskStatus('HUNG')).toBe('REVIEWING');
    expect(mapTaskStatus('REVIEWING')).toBe('REVIEWING');
    expect(mapTaskStatus('DONE')).toBe('DONE');
    expect(mapTaskStatus('SUCCESS')).toBe('DONE');
    expect(mapTaskStatus('FAILED')).toBe('FAILED');
    expect(mapTaskStatus('CANCELLED')).toBe('CANCELLED');
    expect(mapTaskStatus('PENDING')).toBe('DEFINED');
    expect(mapTaskStatus('PLANNING')).toBe('EXECUTING');
    expect(mapTaskStatus('RUNNING')).toBe('EXECUTING');
    expect(mapTaskStatus('UNKNOWN')).toBe('EXECUTING');
  });
});

describe('taskPanelMapper node SUCCESS mapping', () => {
  it('节点 SUCCESS 映射为 done，而不是 pending', () => {
    const dashboard = {
      task_id: 'task-success-node',
      run_id: 1,
      status: 'SUCCESS',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: { metadata: { title: '任务' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: '2026-08-22T10:01:00+08:00',
      loop_round: 1,
      progress: {
        total: 1,
        pending: 0,
        planning: 0,
        running: 0,
        done: 1,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 100,
      },
      tasks: [
        {
          node_id: 'node-success',
          task_id: 'task-success-node',
          sequence: 1,
          status: 'SUCCESS',
          task_spec: { metadata: { title: '成功节点' }, goal: { objective: '目标', acceptances: [] } },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    expect(mapDashboard(dashboard).nodes[0].status).toBe('done');
  });
});

describe('taskPanelMapper task description', () => {
  it('任务描述使用 task_spec.context.background，不使用 metadata.instruction', () => {
    const dashboard = {
      task_id: 'task-description',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: {
        metadata: { title: '任务标题', instruction: '不应展示的 instruction' },
        context: { background: '任务背景描述' },
        goal: { objective: '任务目标', acceptances: [] },
      },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      tasks: [
        {
          node_id: 'node-description',
          task_id: 'task-description',
          sequence: 1,
          status: 'RUNNING',
          task_spec: {
            metadata: { title: '任务标题', instruction: '不应展示的 instruction' },
            context: { background: '任务背景描述' },
            goal: { objective: '任务目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    expect(mapDashboard(dashboard).description).toBe('任务背景描述');
  });
});

describe('taskPanelMapper Task Spec', () => {
  it('协作群节点优先展示自身 group_name，缺失时回退根节点 group_name', () => {
    const dashboard = {
      task_id: 'task-group',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'coop_group',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: { metadata: { title: '任务', instruction: '执行' }, goal: { objective: '目标', acceptances: [] } },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 2, pending: 2, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      tasks: [
        {
          node_id: 'node-group-named',
          task_id: 'task-group',
          sequence: 1,
          status: 'PENDING',
          run_info: {
            run_mode: 'coop_group',
            assignee: 'bcs_grp_123',
            assignee_name: '自动研发Bot',
            extend_props: { group_id: 'bcs_grp_123', group_name: '业务架构分析群' },
          },
          task_spec: {
            metadata: { title: '无人任务研发承接', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
        {
          node_id: 'node-group-fallback',
          task_id: 'task-group',
          sequence: 2,
          status: 'PENDING',
          run_info: { run_mode: 'coop_group', assignee: 'bcs_grp_456', extend_props: { group_id: 'bcs_grp_456' } },
          task_spec: {
            metadata: { title: '营销策略进一步讨论', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
        {
          node_id: 'node-risk-review',
          task_id: 'task-group',
          sequence: 3,
          status: 'PENDING',
          run_info: { run_mode: 'coop_group', assignee: 'bcs_grp_789', extend_props: { group_id: 'bcs_grp_789' } },
          task_spec: {
            metadata: { title: '风险评审', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    expect(task.nodes[0].groupName).toBe('业务架构分析群');
    expect(task.nodes[0].name).toBe('舆情监控方案分析');
    expect(task.nodes[0].executor).toBe('安全架构师');
    expect(task.nodes[1].groupName).toBe('业务架构分析群');
    expect(task.nodes[1].name).toBe('圈人和选品策略讨论');
    expect(task.nodes[2].name).toBe('业务/技术风险评审');
    expect(task.nodes[1].executor).toBeNull();
  });

  it('兼容验收项 acceptance 字段并映射全部验收标准', () => {
    const dashboard = {
      task_id: 'task-1',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-1',
      task_spec: {
        metadata: { title: '任务标题', instruction: '执行指令' },
        goal: {
          objective: '任务目标',
          acceptances: [
            { id: 'ac-1', acceptance: '验收项一' },
            { id: 'ac-2', acceptance: '验收项二' },
          ],
        },
      },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: {
        total: 1,
        pending: 0,
        planning: 0,
        running: 1,
        done: 0,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 0,
      },
      tasks: [
        {
          node_id: 'node-1',
          task_id: 'task-1',
          sequence: 1,
          status: 'RUNNING',
          run_info: {
            run_mode: 'single_bot',
            assignee: '20260823_bot_id',
            extend_props: {
              assignee_name: '自动研发Bot',
            },
          },
          task_spec: {
            metadata: { title: '节点标题', instruction: '节点指令' },
            goal: {
              objective: '节点目标',
              acceptances: [
                { id: 'node-ac-1', acceptance: '节点验收项一' },
                { id: 'node-ac-2', acceptance: '节点验收项二' },
              ],
            },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const task = mapDashboard(dashboard);

    expect(task.nodes[0].executor).toBe('安全架构师');
    expect(task.nodes[0].taskSpec).toEqual({
      title: '节点标题',
      instruction: '节点指令',
      target: '节点目标',
      acceptances: ['节点验收项一', '节点验收项二'],
    });
  });
});

describe('taskPanelMapper root graph-level fallbacks', () => {
  it('根节点缺少 assignee/run_mode/session 时使用 graph extend_props 兜底', () => {
    const dashboard = {
      task_id: 'task-root-fallback',
      run_id: 1,
      status: 'RUNNING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'user-1',
      owner_bot_id: 'bot-top-level',
      extend_props: {
        owner_bot_id: 'bot-from-graph',
        source_type: 'coop_group',
        main_session_id: 'bcs_grp_session:round-1',
      },
      task_spec: {
        metadata: { title: '根节点兜底任务', instruction: '执行' },
        context: { background: '', extend_props: {} },
        goal: { objective: '目标', acceptances: [] },
      },
      create_time: '2026-08-22T10:00:00+08:00',
      finish_time: null,
      loop_round: 1,
      progress: { total: 1, pending: 1, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
      tasks: [
        {
          node_id: 'task-root-fallback',
          task_id: 'task-root-fallback',
          sequence: 1,
          status: 'PENDING',
          run_info: { extend_props: {} },
          task_spec: {
            metadata: { title: '根节点兜底任务', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const root = mapDashboard(dashboard).nodes[0];
    expect(root.assignee).toBe('bot-from-graph');
    expect(root.runMode).toBe('coop_group');
    expect(root.sessionId).toBe('bcs_grp_session:round-1');
  });
});

describe('taskPanelMapper implementation node artifacts output', () => {
  const makeDashboard = (
    implementationStatus: 'RUNNING' | 'DONE',
    rootStatus: 'RUNNING' | 'DONE' = 'DONE',
    taskId = 'task-okr',
  ): TaskDashboardResponse => ({
    task_id: taskId,
    run_id: 1,
    status: implementationStatus,
    needs_attention: false,
    task_type: 'dynamic',
    source_type: 'bot',
    owner_user_id: 'user-1',
    owner_bot_id: 'bot-1',
    task_spec: {
      metadata: { title: '大促OKR任务' },
      context: { background: '制定 GMV 增长目标的大促营销方案' },
      goal: { objective: '提升大促 GMV', acceptances: [] },
    },
    create_time: '2026-08-22T10:00:00+08:00',
    finish_time: null,
    loop_round: 1,
    progress: { total: 2, pending: 0, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 50 },
    output: { result: '根节点中间输出' },
    tasks: [
      {
        node_id: 'node-root',
        task_id: 'task-okr',
        sequence: 1,
        status: rootStatus,
        run_info: { output: { result: '根节点中间输出' } },
        task_spec: { metadata: { title: '任务接收' } },
      },
      {
        node_id: 'node-implementation',
        task_id: 'task-okr',
        sequence: 2,
        status: implementationStatus,
        run_info: {
          output: {
            result: { summary: '结果摘要', random: '479710' },
            implementation_result: { summary: '实施结果摘要', random: '479710' },
            handoff: { summary: '交接摘要', random: '479710' },
          },
        },
        task_spec: { metadata: { title: '投放实施' } },
      },
    ],
    relations: [],
  });

  it('投放实施节点完成后，产物输出取该节点的响应', () => {
    const task = mapDashboard(makeDashboard('DONE'));

    expect(task.rootOutputRender).toContain('implementation_result');
    expect(task.rootOutputRender).not.toContain('根节点中间输出');
    expect(task.rootOutputDimensions).toHaveLength(2);
    expect(task.rootOutputDimensions?.map((dimension) => dimension.key)).toEqual(['实施计划', '阶段性实施结果']);
    expect(task.rootOutputDimensions?.[0].content).not.toContain('Bot：投放实施Bot');
    expect(task.rootOutputDimensions?.[0].content).not.toMatch(/^# 2026 年秋冬年度大促投放实施配置单/m);
    expect(task.rootOutputDimensions?.[1].content).not.toContain('Bot：投放实施Bot');
    expect(task.rootOutputDimensions?.[1].content).toContain('日期：2026-11-10');
    expect(task.rootOutputDimensions?.[1].content).not.toMatch(/^# 大促投放实施结果日报/m);
    expect(task.rootOutputDimensions?.[1].content).toContain('当前累计 GMV：4.8 亿元');
    expect(task.rootOutputDimensions?.[0].content).not.toContain('结果摘要');
  });

  it('投放实施节点未完成时，保持根节点产出逻辑', () => {
    expect(mapDashboard(makeDashboard('RUNNING')).rootOutputRender).toContain('根节点中间输出');
  });

  it('OKR 任务根节点出现后续节点后，即使后续节点未完成也置为 DONE', () => {
    const task = mapDashboard(makeDashboard('RUNNING', 'RUNNING'));

    expect(task.nodes[0].status).toBe('done');
    expect(task.nodes[1].status).toBe('running');
  });

  it('非 OKR 任务不套用根节点 DONE 兜底', () => {
    const task = mapDashboard(makeDashboard('RUNNING', 'RUNNING', 'task-normal'));

    expect(task.nodes[0].status).toBe('running');
  });
});
