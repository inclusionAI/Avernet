/** @jest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, cleanup, render, renderHook, waitFor } from '@testing-library/react';
import { useTaskExecution } from '@/hooks/useTaskExecution';
import { TaskPanelAdapter } from '@/components/Workspace/TaskPanelAdapter';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { getTaskPanelController, resetTaskPanelRuntime } from '@/services/tasks/panel/taskPanelRuntime';
import { parseTaskPanelDeclarations } from '../../../assets/task-panel/src/history/parseTaskPanelDeclarations';
import type { TaskComposerForm } from '@/services/tasks/taskMapper';

const mockExecute = jest.fn<(...args: unknown[]) => Promise<unknown>>();
jest.mock('@/services/tasks/taskService', () => ({ executeTaskService: (...args: unknown[]) => mockExecute(...args), resolveWorkflowCommand: jest.fn() }));
jest.mock('@/hooks/useWorkflowList', () => ({ useWorkflowList: () => ({ workflows: [], workflowsLoading: false, loadWorkflows: jest.fn() }) }));
jest.mock('@/services/workspace/botSkillService', () => ({ botSkillService: { hasTaskIntentSkillForBot: async () => ({ ok: true, data: true }) } }));
jest.mock('@/capabilities', () => ({ getCapabilities: () => ({ getHumanIdentity: () => ({ status: 'available', value: { userId: 'viewer' } }) }) }));
jest.mock('@/services/tasks/panel/taskPanelAssetLoader', () => ({ loadTaskPanelAsset: async () => require('../../../assets/task-panel/src') }));
jest.mock('@/services/tasks/panel/taskPanelArtifactAdapter', () => ({ taskPanelArtifacts: {} }));
jest.mock('@/services/workspace/chatBridge', () => ({ chatBridge: { openPanelTab: jest.fn() } }));

const ctx = { sourceType: 'bot' as const, ownerUserId: 'viewer', ownerBotId: 'bot-main', mainSessionId: 's1' };
const form: TaskComposerForm = { title: 'Displayed launch', instruction: 'Work', objective: 'Deliver', acceptances: ['Tests'], taskType: 'dynamic' };
const dashboard = { task_id: 'new-task', run_id: 1, status: 'DONE', needs_attention: false, task_type: 'dynamic', source_type: 'bot',
  owner_user_id: 'viewer', owner_bot_id: 'bot-main', task_spec: { context: { title: 'Displayed launch', background: '', extend_props: {} }, goal: { objective: 'Deliver', acceptances: [] } },
  create_time: '2026-10-20 10:00:00', finish_time: null, loop_round: 1,
  progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 }, tasks: [], relations: [] };
let originalFetch: typeof fetch;
beforeEach(() => {
  resetTaskPanelRuntime(); window.history.pushState({}, '', '/workspace/chat?bot=bot-main&session=s1');
  originalFetch = global.fetch;
  global.fetch = jest.fn(async (path: unknown) => ({ ok: true, json: async () => ({ data: String(path).includes('/dashboard?') ? dashboard : { items: [] } }) })) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); resetTaskPanelRuntime(); global.fetch = originalFetch; });

describe('submission intent is not SDK presentation', () => {
  it.each(['centralized', 'relay'])('%s only requests an open; mounting the real SDK adapter projects the displayed task', async mode => {
    mockExecute.mockResolvedValue({ task_id: 'new-task', create_time: dashboard.create_time, finish_time: null,
      task_info: { execution_config: mode === 'relay' ? { orchestration_mode: 'relay', root_node_id: 'root', root_execution_instruction: '[task-execute] new-task' } : {} } });
    const controller = await getTaskPanelController();
    await controller.enterConversation({ sessionId: 's1', kind: 'bot' }, null, { recover: false });
    const send = jest.fn<(content: string) => void>(() => {
      expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBe('new-task');
      expect(controller.getSnapshot().activeTaskId).toBeNull();
    });
    const { result } = renderHook(() => useTaskExecution({ panelRef: { current: null }, context: ctx, submitPanelMessage: send, submitTaskExecutionMessage: send }));
    await act(async () => { await result.current.submit(form); });
    expect(send).toHaveBeenCalledTimes(1);
    expect(useTaskSidePanelStore.getState()).toMatchObject({ activeTaskId: null, taskPanelOpenIntentTaskId: 'new-task' });
    const declaration = parseTaskPanelDeclarations(send.mock.calls[0][0])[0]; expect(declaration.params.taskId).toBe('new-task');
    // SDK boundary: mount its actual content adapter, host and shared TaskLoopView, not a fake active setter.
    const adapterProps = { params: declaration.params, tab: declaration, payload: declaration.params, onAction: jest.fn(), onInteraction: jest.fn() } as Parameters<typeof TaskPanelAdapter>[0];
    const mounted = render(<TaskPanelAdapter {...adapterProps} />);
    await waitFor(() => expect(controller.getSnapshot().activeTaskId).toBe('new-task'));
    await waitFor(() => expect(mounted.container.querySelector('[data-task-panel-root="true"]')?.textContent).toContain('Displayed launch'));
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('new-task');
    expect(useTaskSidePanelStore.getState()).not.toHaveProperty('navByTask');
  });
  it('failed send clears its own intent without claiming any displayed task', async () => {
    mockExecute.mockResolvedValue({ task_id: 'new-task', task_info: { execution_config: {} } });
    const { result } = renderHook(() => useTaskExecution({ panelRef: { current: null }, context: ctx,
      submitPanelMessage: () => { throw new Error('SDK send rejected'); } }));
    await act(async () => { await expect(result.current.submit(form)).resolves.toEqual({ ok: false, reason: 'SDK send rejected' }); });
    expect(useTaskSidePanelStore.getState()).toMatchObject({ activeTaskId: null, taskPanelOpenIntentTaskId: null });
  });
});
