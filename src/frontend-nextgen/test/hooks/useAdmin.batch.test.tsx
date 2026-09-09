/** @jest-environment jsdom */
// useAdmin.addMembers 编排测试：聚合 toast（经 sonner toast.error/.success spy）/ 单次 refresh /
// 加载态 / 同一 tick 重入阻断 / 失败子集重试。沿用既有 useAdmin.test.tsx 的 mock 口径
// （auto-mock @/services/admin barrel；notify 不 mock，spy 真实 sonner，断言 toast 入参）。
import type { SearchedUser } from '@/capabilities';
import { useAdmin } from '@/hooks/useAdmin';
import { adminService } from '@/services/admin';
import type { SpaceMember } from '@/domain/admin/models';
import { useAdminStore } from '@/stores/adminStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/admin');
// notify 不 mock：notifyError(msg,{title}) → toast.error(title,{description:msg,duration})，
// notifySuccess(msg) → toast.success(msg,{duration})。spy 真实 sonner，断言 title/description。
const { toast } = jest.requireActual('sonner') as typeof import('sonner');
const toastError = jest.spyOn(toast, 'error').mockImplementation(() => 'err' as any);
const toastSuccess = jest.spyOn(toast, 'success').mockImplementation(() => 'ok' as any);

const as = adminService as unknown as Record<string, jest.Mock<any>>;

const space = { spaceId: 100, spaceCode: 's', spaceName: 'x', spaceType: 'TEAM', memberCount: 0, ownerCount: 0, botCount: 0, gmtModified: '', currentUserRole: 'ADMIN' } as const;

function member(userId: string): SpaceMember {
  return {
    userId,
    userName: userId,
    role: 'MEMBER',
    botPermissionCount: 0,
    isCreator: false,
    gmtModified: '',
  } as SpaceMember;
}

function searchedUser(userId: string, nickName?: string): SearchedUser {
  return { userId, displayName: nickName ?? userId, nickName } as SearchedUser;
}

const LIST_OK = { data: { items: [] as SpaceMember[], total: 0, page: 1, pageSize: 20, warnings: [] } };

beforeEach(() => {
  jest.clearAllMocks();
  useAdminStore.getState().reset();
  useExternalAuthStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway'); // 非静默口径
  useAdminStore.getState().setCurrentSpace(space as any);
  as.listSpaces.mockResolvedValue(LIST_OK);
  as.listMembers.mockResolvedValue(LIST_OK);
});

/** 取 toastError 描述体中的 description 文案（notifyError(msg,{title}) 走 title→description 双行）。 */
function errorDescription(): string {
  const opts = toastError.mock.calls[0]?.[1] as { description?: string } | undefined;
  return opts?.description ?? '';
}

describe('useAdmin.addMembers：聚合 toast / 单次 refresh / 重入阻断 / 重试', () => {
  it('全部成功 → toast.success 一次含「已添加 N 名成员」+ refresh 一次 + addMembersBatch 入参正确', async () => {
    as.addMembersBatch.mockResolvedValue({ succeeded: [member('u1'), member('u2')], failed: [] });
    const { result } = renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());

    let r: { succeeded: unknown[] } | undefined;
    await act(async () => {
      r = await result.current.addMembers([searchedUser('u1', '花一'), searchedUser('u2')], 'MEMBER');
    });
    expect(r?.succeeded).toHaveLength(2);

    expect(toastSuccess).toHaveBeenCalledTimes(1);
    expect(toastSuccess.mock.calls[0][0]).toBe('已添加 2 名成员');
    expect(toastError).not.toHaveBeenCalled();
    // refresh 一次
    expect(as.listMembers).toHaveBeenCalledTimes(1);
    // 入参：spaceId + items（逐项 userName 透传）+ role
    expect(as.addMembersBatch).toHaveBeenCalledWith(
      100,
      [
        { userId: 'u1', userName: '花一' },
        { userId: 'u2', userName: undefined },
      ],
      'MEMBER',
    );
  });

  it('部分成功 2/5 → toast.error 一次，title=「添加成员部分失败」且 description 含「2/5」与每名失败原因 + refresh 一次', async () => {
    as.addMembersBatch.mockResolvedValue({
      succeeded: [member('u1'), member('u2')],
      failed: [
        { userId: 'p', userName: '花名P', reason: '已是成员' },
        { userId: 'q', userName: '花名Q', reason: '无权限' },
        { userId: 'r', reason: '请求异常' },
      ],
    });
    const { result } = renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());

    await act(async () => {
      await result.current.addMembers(
        [searchedUser('u1'), searchedUser('u2'), searchedUser('p', '花名P'), searchedUser('q', '花名Q'), searchedUser('r')],
        'MEMBER',
      );
    });

    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError.mock.calls[0][0]).toBe('添加成员部分失败');
    const desc = errorDescription();
    expect(desc).toContain('2/5');
    expect(desc).toContain('花名P(已是成员)');
    expect(desc).toContain('花名Q(无权限)');
    expect(desc).toContain('r(请求异常)');
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(as.listMembers).toHaveBeenCalledTimes(1);
  });

  it('全部失败 → toast.error 一次，description 含「0/N」与所有失败项 + 不静默', async () => {
    as.addMembersBatch.mockResolvedValue({
      succeeded: [],
      failed: [
        { userId: 'p', userName: '花名P', reason: '已是成员' },
        { userId: 'q', userName: '花名Q', reason: '无权限' },
      ],
    });
    const { result } = renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());

    await act(async () => {
      await result.current.addMembers([searchedUser('p', '花名P'), searchedUser('q', '花名Q')], 'ADMIN');
    });

    expect(toastError).toHaveBeenCalledTimes(1);
    const desc = errorDescription();
    expect(desc).toContain('0/2');
    expect(desc).toContain('花名P(已是成员)');
    expect(desc).toContain('花名Q(无权限)');
  });

  it('空批次 → 不调 addMembersBatch、不 toast、不 refresh', async () => {
    const { result } = renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());

    await act(async () => {
      await result.current.addMembers([], 'MEMBER');
    });

    expect(as.addMembersBatch).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
    expect(as.listMembers).not.toHaveBeenCalled();
  });

  it('in-flight 阻断重入：pending 期间再次调用不新增 addMembersBatch，并暴露 addMembersLoading/disabledReason', async () => {
    let resolveBatch!: (v: unknown) => void;
    as.addMembersBatch.mockImplementation(
      () => new Promise((r) => { resolveBatch = r; }),
    );

    const { result } = renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());

    await act(async () => {
      // 不 await：使其 pending；触发 addingMembersRef + addMembersLoading state。
      void result.current.addMembers([searchedUser('u1'), searchedUser('u2')], 'MEMBER');
      await Promise.resolve();
    });
    expect(as.addMembersBatch).toHaveBeenCalledTimes(1);
    expect(result.current.addMembersLoading).toBe(true);
    expect(result.current.addMembersDisabledReason).toBeTruthy();

    // 同一 tick 重入 → 被同步阻断
    await act(async () => {
      await result.current.addMembers([searchedUser('u3')], 'MEMBER');
    });
    expect(as.addMembersBatch).toHaveBeenCalledTimes(1); // 仍 1 次

    // 解开 pending → toast + 一次 refresh
    await act(async () => {
      resolveBatch({ succeeded: [member('u1'), member('u2')], failed: [] });
      await Promise.resolve();
    });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledTimes(1));
    expect(toastSuccess.mock.calls[0][0]).toBe('已添加 2 名成员');
    expect(as.listMembers).toHaveBeenCalledTimes(1);
    expect(result.current.addMembersLoading).toBe(false);
  });

  it('失败子集重试：以 failed 子集回填再提交，走同一路径并再次 toast+refresh', async () => {
    as.addMembersBatch.mockResolvedValueOnce({
      succeeded: [member('u1')],
      failed: [{ userId: 'p', userName: '花名P', reason: '已是成员' }],
    });
    const { result } = renderHook(() => useAdmin());
    await waitFor(() => expect(as.listSpaces).toHaveBeenCalled());

    // 首次：1 成功 1 失败
    await act(async () => {
      await result.current.addMembers([searchedUser('u1'), searchedUser('p', '花名P')], 'MEMBER');
    });
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(as.addMembersBatch).toHaveBeenCalledTimes(1);
    expect(as.listMembers).toHaveBeenCalledTimes(1);

    // 重试失败子集（仅 p）
    as.addMembersBatch.mockResolvedValueOnce({ succeeded: [member('p')], failed: [] });

    await act(async () => {
      await result.current.addMembers([searchedUser('p', '花名P')], 'MEMBER');
    });
    expect(as.addMembersBatch).toHaveBeenCalledWith(100, [{ userId: 'p', userName: '花名P' }], 'MEMBER');
    expect(toastSuccess.mock.calls[0][0]).toBe('已添加 1 名成员');
    // 第二次 batch 后又一次 refresh
    expect(as.listMembers).toHaveBeenCalledTimes(2);
  });
});
