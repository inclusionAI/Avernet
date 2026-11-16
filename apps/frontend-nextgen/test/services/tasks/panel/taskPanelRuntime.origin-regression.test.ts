/** @jest-environment jsdom */
// Real runtime + shared controller + real host navigation/capability; no old store commands.
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act } from '@testing-library/react';
import { history } from '@umijs/max';
import { getTaskPanelController, resetTaskPanelRuntime } from '@/services/tasks/panel/taskPanelRuntime';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn((url: string) => globalThis.window.history.pushState({}, '', url)) } }));
jest.mock('@/capabilities', () => ({ getCapabilities: () => ({
  ...require('@/capabilities/defaultCapabilities').defaultCapabilities,
  getHumanIdentity: () => ({ status: 'available', value: { userId: 'viewer' } }),
}) }));
jest.mock('@/services/tasks/panel/taskPanelAssetLoader', () => ({ loadTaskPanelAsset: async () => require('../../../../assets/task-panel/src/controller/createTaskPanelController') }));
jest.mock('@/services/tasks/panel/taskPanelArtifactAdapter', () => ({ taskPanelArtifacts: {} }));
jest.mock('@/services/workspace/chatBridge', () => ({ chatBridge: { openPanelTab: jest.fn() } }));

const origin = '/workspace/chat?bot=bot-host&session=s-host';
beforeEach(() => { resetTaskPanelRuntime(); jest.clearAllMocks(); jest.useFakeTimers(); window.history.pushState({}, '', origin); });
afterEach(() => { resetTaskPanelRuntime(); jest.useRealTimers(); });
async function rootFrom(url: string) {
  const c = await getTaskPanelController();
  c.adoptPresentedTask({ id: 'task-t1', type: 'taskPanel.TaskLoopView', title: 'T', closable: true, params: { taskId: 't1' } });
  window.history.pushState({}, '', url);
  c.nav('t1').selectRootNode('root');
  await act(async () => { await jest.advanceTimersByTimeAsync(200); });
  expect(c.nav('t1').selectedNodeId).toBe('root');
  return c;
}
describe('migrated root-origin assertions against actual host runtime', () => {
  it('restores the exact original host route after a linked group conversation', async () => {
    await rootFrom('/workspace/collaboration?group=g2&session=s-linked');
    expect(history.push).toHaveBeenCalledWith(origin);
    expect(window.location.pathname + window.location.search).toBe(origin);
  });
  it('keeps an already-current normalized host URL unchanged (former nav binding regression)', async () => {
    const normalized = '/workspace/chat?section=managed&bot=bot-host&origin=mine&session=s-host#latest';
    await rootFrom(normalized);
    expect(history.push).not.toHaveBeenCalled();
    expect(window.location.pathname + window.location.search + window.location.hash).toBe(normalized);
  });
});
