import { describe, expect, it } from 'vitest';
import { filterTasksForCurrentConversation as filter, firstTaskNodeTarget, toSwitcherItem } from '../../src/data/taskSwitcher';
import { mainSessionId } from '../../src/data/taskClient';
import { dashboard, row } from '../panel/taskSwitcherFixtures';

describe('shared switcher conversation filtering (former useTaskSwitcherTasks)', () => {
  it('keeps modern main_session siblings and deduplicates by task_id, first record wins', () => {
    const current = row('t1');
    expect(filter([current, row('t1', 'shared', 'DONE'), row('t2'), row('foreign', 'other')], 't1'))
      .toEqual([current, row('t2')]);
  });
  it('includes by_session relay tasks with another main session, exactly once', () => {
    expect(filter([row('t1'), row('relay', 'second'), row('relay', 'second'), row('foreign', 'other')], 't1', ['relay'])
      .map(r => r.task_id)).toEqual(['t1', 'relay']);
  });
  it('falls back to historical teamclaw_context main_session_id, with modern taking precedence', () => {
    const legacy = { ...row('legacy', ''), task_spec: { context: { extend_props: { teamclaw_context: { main_session_id: 'shared' } } } } };
    expect(mainSessionId(legacy)).toBe('shared');
    expect(mainSessionId({ ...legacy, execution_config: { main_session_id: ' modern ' } })).toBe('modern');
    expect(filter([row('t1'), legacy, row('foreign', 'other')], 'legacy').map(r => r.task_id)).toEqual(['t1', 'legacy']);
  });
  it('never aggregates anonymous sessions, but retains explicit associations and deduplicates them', () => {
    expect(filter([row('t1', ''), row('other', '')], 't1')).toEqual([row('t1', '')]);
    expect(filter([row('t1', ''), row('relay', ''), row('relay', ''), row('other', '')], 't1', ['relay']).map(r => r.task_id))
      .toEqual(['t1', 'relay']);
  });
  it('has no switcher without the current task in the list', () => {
    expect(filter([row('t2')], 'missing', ['t2'])).toEqual([]);
  });
  it.each([
    ['EXECUTING', 'running', '执行中'], ['RUNNING', 'running', '执行中'], ['REVIEWING', 'running', '待验收'],
    ['DRAFTING', 'pending', '待开始'], ['DEFINED', 'pending', '待开始'], ['PENDING', 'pending', '待开始'],
    ['DONE', 'done', '已完成'], ['FAILED', 'done', '失败'], ['CANCELLED', 'done', '已取消'],
  ])('maps %s to %s / %s', (status, bucket, label) => {
    expect(toSwitcherItem(row('t1', 'shared', status))).toMatchObject({ taskId: 't1', title: 'Title t1', status: bucket, statusLabel: label });
  });
  it('uses the shared title precedence: trimmed context, goal, then task id label', () => {
    expect(toSwitcherItem({ ...row('t1'), task_spec: { context: { title: '  ' }, goal: { objective: ' Goal ' } } }).title).toBe('Goal');
    expect(toSwitcherItem({ task_id: 't1', status: 'DONE' }).title).toBe('任务 t1');
  });
});

describe('shared first-node location (former buildTaskConversationNavigationTarget)', () => {
  it('uses the first step bot session and its exact message time window', () => {
    expect(firstTaskNodeTarget(dashboard('t1'), row('t1'))).toEqual({ nodeId: 'n-first', target: {
      sessionId: 'session-first-step', kind: 'bot', botId: 'bot-first', groupId: null,
      startTime: 1760000000000, endTime: 1760000060000,
    } });
  });
  it('preserves a cooperation group session and group id', () => {
    expect(firstTaskNodeTarget(dashboard('t1', { run_info: { run_mode: 'coop_group', start_time: 1760000000000,
      extend_props: { session_id: 'bcs_grp_group-1:round:1', group_id: 'group-1' } } }), row('t1'))?.target)
      .toMatchObject({ sessionId: 'bcs_grp_group-1:round:1', kind: 'group', groupId: 'group-1', botId: null });
  });
  it('uses list metadata only for missing node/dashboard session hints', () => {
    expect(firstTaskNodeTarget(dashboard('t1', { run_info: { run_mode: 'single_bot' } }), row('t1', 'fallback'))?.target.sessionId).toBe('fallback');
  });
  it('never invents a whole-task location when no first step exists', () => {
    expect(firstTaskNodeTarget({ ...dashboard('t1'), tasks: [] }, row('t1'))).toBeNull();
  });
});


it('falls through a blank modern source_group_id to historical group metadata', () => {
  const record = { ...row('t1', 'bcs_grp_legacy:round'), execution_config: { main_session_id: 'bcs_grp_legacy:round', source_group_id: '  ' },
    task_spec: { context: { extend_props: { teamclaw_context: { source_group_id: 'legacy-group' } } } } };
  expect(firstTaskNodeTarget(dashboard('t1', { run_info: { run_mode: 'coop_group' } }), record)?.target)
    .toMatchObject({ sessionId: 'bcs_grp_legacy:round', kind: 'group', groupId: 'legacy-group' });
});
