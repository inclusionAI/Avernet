/** @jest-environment jsdom */
import { useBotWorkshopAccess } from '@/hooks/useBotWorkshopAccess';
import type { BotDomain } from '@/services/botWorkshop';
import { botManagementService } from '@/services/botWorkshop/botManagementService';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock('@/services/botWorkshop/botManagementService', () => ({
  botManagementService: {
    listCollaborators: jest.fn(),
    listSpaceMembers: jest.fn(),
    getEditorRequestPolicy: jest.fn(),
    updateEditorRequestPolicy: jest.fn(),
    requestAccess: jest.fn(),
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

it('编辑权限自动通过后明确提示已获得权限并刷新列表', async () => {
  jest.mocked(botManagementService.requestAccess).mockResolvedValue({
    status: 'approved',
    workOrderId: 11,
    workOrderNo: 'WO-11',
  });
  const reload = jest.fn().mockResolvedValue(undefined);
  const applicantBot = { ...bot, ownerId: 'other-owner' };
  const { result } = renderHook(() => useBotWorkshopAccess('applicant-1', reload));

  act(() => result.current.openAuthorize(applicantBot));
  await act(async () => result.current.requestAccess('需要共同编辑'));

  expect(toast.success).toHaveBeenCalledWith('编辑权限已自动通过，你现在可以编辑该 Bot');
  expect(reload).toHaveBeenCalled();
  expect(result.current.access.bot).toBeUndefined();
});

it('编辑权限进入审批时提示等待 Owner 审批，失败时也有明确反馈', async () => {
  jest
    .mocked(botManagementService.requestAccess)
    .mockResolvedValueOnce({ status: 'pending', workOrderId: 12, workOrderNo: 'WO-12' })
    .mockRejectedValueOnce(new Error('当前用户不在 Bot 所属空间内'));
  const applicantBot = { ...bot, ownerId: 'other-owner' };
  const { result } = renderHook(() => useBotWorkshopAccess('applicant-1', jest.fn()));

  act(() => result.current.openAuthorize(applicantBot));
  await act(async () => result.current.requestAccess('请审批'));
  expect(toast.success).toHaveBeenCalledWith('操作权限申请已提交，等待 Owner 审批');

  act(() => result.current.openAuthorize(applicantBot));
  await act(async () => result.current.requestAccess('再试一次'));
  expect(toast.error).toHaveBeenCalledWith('当前用户不在 Bot 所属空间内');
  expect(result.current.access.bot).toEqual(applicantBot);
});
