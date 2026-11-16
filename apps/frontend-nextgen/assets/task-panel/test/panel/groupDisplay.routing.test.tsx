import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { collectTaskGroupIds, enrichTaskGroupDisplay } from '../../src/panel/groupDisplay';
import { useGroupMemberNames } from '../../src/panel/hooks/useGroupMemberNames';
import { TaskPanelHostContext } from '../../src/panel/hostContext';
import { resolveNodeSessionTarget } from '../../src/panel/sessionResolution';
import { mapDashboard } from '../../src/panel/taskPanelMapper';
import type { TaskArtifactView } from '../../src/panel/types';
import { dashboard, makeHost } from './taskSwitcherFixtures';

const botId = '20260101_fixture:123';
const groupId = 'bcs_grp_execution';
function groupTask(extendProps: Record<string, unknown> = {}, assignee = botId) {
  return mapDashboard(dashboard('t1', {
    run_info: { run_mode: 'coop_group', assignee, extend_props: extendProps },
  }));
}

describe('group metadata uses group identity, not executor identity', () => {
  it.each([
    { name: 'explicit group wins over session and assignee', props: { group_id: ' legacy-group ', session_id: `${groupId}:round` }, assignee: 'bcs_grp_other', ids: ['legacy-group'] },
    { name: 'BBS Bot assignee uses its group session', props: { actual_run_mode: 'bbs', assignee_bot_id: botId, session_id: `${groupId}:round` }, assignee: botId, ids: [groupId] },
    { name: 'blank group falls back to its session', props: { group_id: ' ', session_id: `${groupId}:round` }, assignee: botId, ids: [groupId] },
    { name: 'session wins over a different group assignee', props: { session_id: `${groupId}:round` }, assignee: 'bcs_grp_other', ids: [groupId] },
    { name: 'recognized legacy group assignee remains supported', props: {}, assignee: ' bcs_grp_legacy ', ids: ['bcs_grp_legacy'] },
    { name: 'Bot assignee alone is not a group', props: {}, assignee: botId, ids: [] },
    { name: 'unknown role binding is not a group', props: { actual_run_mode: 'bbs' }, assignee: 'worker-role', ids: [] },
    { name: 'a session ID in assignee is not a group ID', props: {}, assignee: `${groupId}:round`, ids: [] },
  ])('$name', ({ props, assignee, ids }) => {
    expect(collectTaskGroupIds(groupTask(props, assignee))).toEqual(ids);
  });

  it('does not infer group membership for an ordinary single-Bot node', () => {
    const task = mapDashboard(dashboard('t1', { run_info: { run_mode: 'single_bot', assignee: botId,
      extend_props: { group_id: 'bcs_grp_context', session_id: 'agent:main:session:one:user:123' } } }));
    expect(collectTaskGroupIds(task)).toEqual([]);
  });

  it('queries each actual execution group once in a multi-group task', () => {
    const task = groupTask({ session_id: `${groupId}:round` });
    task.nodes.push(
      { ...task.nodes[0], id: 'n2', sessionId: 'bcs_grp_second:round' },
      { ...task.nodes[0], id: 'n3', sessionId: `${groupId}:another` },
    );
    task.sourceGroupId = 'bcs_grp_page';
    expect(collectTaskGroupIds(task)).toEqual([groupId, 'bcs_grp_second']);
  });

  it('uses the same group for node/artifact labels without changing navigation or raw IDs', () => {
    const task = groupTask({ actual_run_mode: 'bbs', session_id: `${groupId}:round` });
    task.nodes[0].artifacts = [{ id: 'a1', name: 'Report', type: 'document', kind: 'text',
      owner: 'unknown-actor', updatedAt: '' } as TaskArtifactView];
    const original = JSON.stringify(task);
    const target = resolveNodeSessionTarget(task.nodes[0], task);
    const enriched = enrichTaskGroupDisplay(task, {
      [groupId]: { names: ['Alice', 'Bob'], nameByActorId: {} },
    });
    expect(enriched.nodes[0].executorLabel).toBe('Alice、Bob');
    expect(enriched.nodes[0].artifacts[0].ownerLabel).toBe('Alice、Bob');
    expect(enriched.nodes[0].assignee).toBe(botId);
    expect(enriched.nodes[0].groupId).toBeNull();
    expect(resolveNodeSessionTarget(enriched.nodes[0], enriched)).toEqual(target);
    expect(JSON.stringify(task)).toBe(original);
  });

  it('routes the real metadata hook to the BBS session group rather than the Bot', async () => {
    const task = groupTask({ actual_run_mode: 'bbs', assignee_bot_id: botId, session_id: `${groupId}:round` });
    const ids = collectTaskGroupIds(task);
    const host = makeHost();
    vi.mocked(host.transport.requestJson).mockResolvedValue({ data: { group_id: groupId,
      participants: [{ actor_id: botId, actor_kind: 'bot', name: 'Executor', role: 'worker' }] } });
    const { result } = renderHook(() => useGroupMemberNames(ids), {
      wrapper: ({ children }) => <TaskPanelHostContext.Provider value={host}>{children}</TaskPanelHostContext.Provider>,
    });
    await waitFor(() => expect(result.current[groupId]?.names).toEqual(['Executor']));
    expect(vi.mocked(host.transport.requestJson).mock.calls.map(([path]) => path)).toEqual([
      `/openapi/v1/collaboration/groups/${groupId}`,
    ]);
  });
});
