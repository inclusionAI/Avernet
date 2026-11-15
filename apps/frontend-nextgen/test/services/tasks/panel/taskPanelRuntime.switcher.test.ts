/** @jest-environment jsdom */
// SDK-only assertions migrated from the retired TaskPanelSwitcherSlot. No old nav store policy.
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { persistPanelWidthPercent } from '@/services/workspace/panelWidthMemory';
import { chatBridge } from '@/services/workspace/chatBridge';
import type { TaskPanelDeclaration } from '@avernet-assets/task-panel';

jest.mock('@/capabilities', () => ({ getCapabilities: () => ({ getHumanIdentity: () => ({ status: 'available', value: { userId: 'viewer' } }) }) }));
jest.mock('@/services/tasks/panel/taskPanelAssetLoader', () => ({ loadTaskPanelAsset: async () => require('../../../../assets/task-panel/src/controller/createTaskPanelController') }));
jest.mock('@/services/tasks/panel/taskPanelArtifactAdapter', () => ({ taskPanelArtifacts: {} }));
jest.mock('@/services/workspace/chatBridge', () => ({ chatBridge: { openPanelTab: jest.fn() } }));
jest.mock('@/services/tasks/panel/taskPanelConversationAdapter', () => ({ captureTaskPanelOrigin: () => null, navigateTaskPanelConversation: async () => ({ status: 'ready' }) }));
import { getTaskPanelController, resetTaskPanelRuntime } from '@/services/tasks/panel/taskPanelRuntime';

beforeEach(() => { resetTaskPanelRuntime(); localStorage.clear(); jest.clearAllMocks(); });
afterEach(() => { resetTaskPanelRuntime(); });

describe('real runtime adapts shared switcher presentation to SDK openPanelTab', () => {
  it.each([[null, 420], ['35%', 420], ['56%', 560]] as const)('width memory %s opens at %s', async (memory, expected) => {
    if (memory) persistPanelWidthPercent(memory, 1000);
    const c = await getTaskPanelController();
    const tab: TaskPanelDeclaration = { id: 'task-other', type: 'taskPanel.TaskLoopView', title: 'Other task', closable: true,
      defaultWidth: 123, params: { taskId: 'other', userId: 'stale', taskApiBase: '/fixture/tasks' } };
    c.openTask(tab, 'explicit');
    expect(chatBridge.openPanelTab).toHaveBeenCalledTimes(1);
    expect(chatBridge.openPanelTab).toHaveBeenCalledWith({ ...tab, defaultWidth: expected, params: { ...tab.params, userId: 'viewer' } });
  });
});
