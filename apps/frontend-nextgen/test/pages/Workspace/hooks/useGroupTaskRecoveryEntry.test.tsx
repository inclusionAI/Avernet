/** @jest-environment jsdom */
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';
import { useGroupChatHeaderPanels } from '@/pages/Workspace/hooks/useGroupChatHeaderPanels';
const mockContinue = jest.fn(async () => {});
let mockStatus = 'idle';
jest.mock('@/hooks/useSessionTaskPanelBootstrap', () => ({ useSessionTaskPanelBootstrap: () => ({ status: mockStatus, continueRecovery: mockContinue }) }));
jest.mock('@/hooks/useSessionTaskPanelTab', () => ({ useSessionTaskPanelTab: () => null }));
jest.mock('@/services/tasks/panel/taskPanelRuntime', () => ({ getTaskPanelController: async () => ({ continueRecovery: mockContinue }) }));
jest.mock('@/services/tasks/panel/taskPanelHistoryAdapter', () => ({ createGroupTaskHistoryReader: () => ({}) }));
beforeEach(() => { mockContinue.mockClear(); });
it.each(['incomplete', 'error'])('keeps group recovery %s actionable without a visible declaration', async status => {
  mockStatus = status;
  const { result } = renderHook(() => useGroupChatHeaderPanels({ session: { sessionId: 's1' } as any, userId: 'viewer', messages: [], panelRef: { current: null }, toggleFavorite: async () => {} }));
  expect(result.current.sidePaneDisabled).toBe(false);
  await act(async () => { result.current.toggleSidePane(); });
  expect(mockContinue).toHaveBeenCalledTimes(1);
});
it('keeps an exhausted empty group disabled', () => {
  mockStatus = 'not-found';
  const { result } = renderHook(() => useGroupChatHeaderPanels({ session: { sessionId: 's1' } as any, userId: 'viewer', messages: [], panelRef: { current: null }, toggleFavorite: async () => {} }));
  expect(result.current.sidePaneDisabled).toBe(true);
});
