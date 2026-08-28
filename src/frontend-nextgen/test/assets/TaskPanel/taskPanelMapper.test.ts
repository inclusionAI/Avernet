import type { TaskDashboardResponse } from '@/assets/TaskPanel/contract';
import { mapDashboard, mapTaskStatus } from '@/assets/TaskPanel/taskPanelMapper';
import { describe, expect, it } from '@jest/globals';

describe('taskPanelMapper status mapping', () => {
  it('兼容旧运行时状态和新的产品层状态', () => {
    expect(mapTaskStatus('HUNG')).toBe('REVIEWING');
    expect(mapTaskStatus('REVIEWING')).toBe('REVIEWING');
    expect(mapTaskStatus('DONE')).toBe('DONE');
    expect(mapTaskStatus('FAILED')).toBe('FAILED');
    expect(mapTaskStatus('CANCELLED')).toBe('CANCELLED');
    expect(mapTaskStatus('PENDING')).toBe('DEFINED');
    expect(mapTaskStatus('PLANNING')).toBe('EXECUTING');
    expect(mapTaskStatus('RUNNING')).toBe('EXECUTING');
    expect(mapTaskStatus('UNKNOWN')).toBe('EXECUTING');
  });
});

describe('taskPanelMapper Task Spec', () => {
  it('协作群节点优先展示 group_name，缺失时使用 BCS协作群且不展示 group_id', () => {
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
            extend_props: { group_id: 'bcs_grp_123', group_name: '业务架构分析群' },
          },
          task_spec: {
            metadata: { title: '群任务一', instruction: '执行' },
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
            metadata: { title: '群任务二', instruction: '执行' },
            goal: { objective: '目标', acceptances: [] },
          },
        },
      ],
      relations: [],
    } as TaskDashboardResponse;

    const task = mapDashboard(dashboard);
    expect(task.nodes[0].groupName).toBe('业务架构分析群');
    expect(task.nodes[1].groupName).toBe('BCS协作群');
    expect(task.nodes[0].executor).toBeNull();
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
              assignee_name: '技术栈概览Bot',
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

    expect(task.nodes[0].executor).toBe('技术栈概览Bot');
    expect(task.nodes[0].taskSpec).toEqual({
      title: '节点标题',
      instruction: '节点指令',
      target: '节点目标',
      acceptances: ['节点验收项一', '节点验收项二'],
    });
  });
});
