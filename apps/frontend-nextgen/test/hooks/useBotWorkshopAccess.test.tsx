/** @jest-environment jsdom */
import { useBotWorkshopAccess } from '@/hooks/useBotWorkshopAccess';
import type { BotDomain } from '@/services/botWorkshop';
import { botManagementService } from '@/services/botWorkshop/botManagementService';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock('@/services/botWorkshop/botManagementService', () => ({
  botManagementService: {
    listCollaborators: jest.fn(),
    listSpaceMembers: jest.fn(),
    getEditorRequestPolicy: jest.fn(),
    updateEditorRequestPolicy: jest.fn(),
  },
}));

const bot = {
  id: 'bot-1',
  ownerId: 'owner-1',
  spaceId: 'space-1',
  spaceKind: 'team',
  actions: [],
} as unknown as BotDomain;

beforeEach(() => {
  jest.mocked(botManagementService.listCollaborators).mockResolvedValue([]);
  jest.mocked(botManagementService.listSpaceMembers).mockResolvedValue([]);
  jest.mocked(botManagementService.getEditorRequestPolicy).mockResolvedValue(false);
  jest.mocked(botManagementService.updateEditorRequestPolicy).mockResolvedValue(true);
});

afterEach(() => jest.clearAllMocks());

it('打开 Owner 授权管理时读取自动审批策略并支持更新', async () => {
  const { result } = renderHook(() => useBotWorkshopAccess('owner-1', jest.fn()));

  act(() => result.current.openAuthorize(bot));

  await waitFor(() => expect(result.current.access.loading).toBe(false));
  expect(botManagementService.getEditorRequestPolicy).toHaveBeenCalledWith('bot-1');
  expect(result.current.access.autoApproveEditorRequests).toBe(false);

  await act(async () => result.current.updateEditorRequestPolicy(true));

  expect(botManagementService.updateEditorRequestPolicy).toHaveBeenCalledWith('bot-1', true);
  expect(result.current.access.autoApproveEditorRequests).toBe(true);
});

it('策略读取失败时只降级策略区，不阻断协作者和空间成员', async () => {
  jest
    .mocked(botManagementService.listCollaborators)
    .mockResolvedValue([{ id: 1, userId: 'member-1', name: '成员一', role: 'member' }]);
  jest.mocked(botManagementService.listSpaceMembers).mockResolvedValue([{ userId: 'member-1', name: '成员一' }]);
  jest.mocked(botManagementService.getEditorRequestPolicy).mockRejectedValue(new Error('策略接口不可用'));
  const { result } = renderHook(() => useBotWorkshopAccess('owner-1', jest.fn()));

  act(() => result.current.openAuthorize(bot));

  await waitFor(() => expect(result.current.access.loading).toBe(false));
  expect(result.current.collaborators).toHaveLength(1);
  expect(result.current.access.members).toHaveLength(1);
  expect(result.current.access.policyError).toBe('策略接口不可用');
});
