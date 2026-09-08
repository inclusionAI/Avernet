/** @jest-environment jsdom */
// SpaceMemberList 批量多选模态：chip 列表、计数按钮、去重（已是成员/已选入本批次/自己）、
// in-flight 禁用、部分失败重试。经 extendCapabilities 注入可信搜索目录驱动真实 UserSearchDropdown
// （该下拉内部行为已由 UserSearchDropdown.test.tsx 覆盖，这里聚焦父级 chip 管理）。
// 关键：useUserSearch 300ms 防抖 → 必须用 findByRole（自轮询等渲染）而非同步 getByRole。
import { extendCapabilities } from '@/capabilities';
import { SpaceMemberList } from '@/components/Admin/SpaceMemberList';
import type { SearchedUser } from '@/capabilities';
import type { Space, SpaceMember } from '@/domain/admin/models';
import { readUserId } from '@/services/admin/userIdentity';
import '@testing-library/jest-dom';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';
import { act, fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/services/admin/userIdentity'); // readUserId 自动 mock（固定自己=工号 'self'）
const readUserIdMock = readUserId as unknown as jest.Mock;

const mockSearch = jest.fn();

const CANDIDATES: SearchedUser[] = [
  { userId: 'a', displayName: 'A', nickName: '花A' },
  { userId: 'b', displayName: 'B', nickName: '花B' },
  { userId: 'c', displayName: 'C', nickName: '花C' },
  { userId: 'm1', displayName: 'M', nickName: '成员一' },
  { userId: 'self', displayName: 'S', nickName: '我自己' },
];

const space = {
  spaceId: 100,
  spaceCode: 's',
  spaceName: 'x',
  spaceType: 'TEAM',
  memberCount: 1,
  ownerCount: 1,
  botCount: 0,
  gmtModified: '',
  currentUserRole: 'ADMIN',
} as unknown as Space;

function mem(userId: string): SpaceMember {
  return { userId, userName: userId, role: 'ADMIN', botPermissionCount: 0, isCreator: false, gmtModified: '' } as SpaceMember;
}

interface BatchResult {
  succeeded: SpaceMember[];
  failed: { userId: string; userName?: string; reason: string }[];
}

beforeEach(() => {
  jest.clearAllMocks();
  readUserIdMock.mockReturnValue('self');
  mockSearch.mockReset();
  mockSearch.mockResolvedValue(CANDIDATES);
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'available', value: { search: mockSearch } }),
  });
});

afterEach(() => {
  jest.clearAllMocks();
});

/** 打开模态 + 触发搜索（5 名候选随后渲染）。 */
async function openAndSearch(): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: '添加成员' }));
  const input = (await screen.findByLabelText('搜索员工')) as HTMLInputElement;
  fireEvent.change(input, { target: { value: 'aa' } });
}

/**
 * 选某个候选。首次（retype=false）下拉已开；第 2/3 次（retype=true）前先重新键入触发搜索，
 * 并用 findByRole 自轮询等 300ms 防抖后的候选渲染（同步 getByRole 会在防抖完成前抛错）。
 */
async function pickCandidate(re: RegExp, retype = false): Promise<void> {
  if (retype) {
    const input = screen.getByLabelText('搜索员工') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'aa' } });
  }
  const opt = await screen.findByRole('button', { name: re }, { timeout: 3000 });
  fireEvent.click(opt);
  await act(async () => {
    await Promise.resolve();
  });
}

function submitButton(): HTMLButtonElement {
  // 排除「添加成员」开关按钮（其 name 固定为「添加成员」无数字）；只匹配「添加 N 人」或加载态「添加中」。
  return screen.getByRole('button', { name: /添加\s+\d+\s+人|添加中/ }) as HTMLButtonElement;
}

function chipRemoveButtons(): HTMLElement[] {
  return screen.getAllByLabelText('取消选择');
}

describe('SpaceMemberList 批量多选模态', () => {
  it('已是成员/自己 初始即在下拉中禁用；选中 A 后 A 变为禁用且按钮计数为「添加 1 人」', async () => {
    render(
      <SpaceMemberList
        space={space}
        members={[mem('m1')]}
        loading={false}
        onAddMembers={jest.fn()}
        onRemoveMember={jest.fn()}
        onUpdateRole={jest.fn()}
      />,
    );
    await openAndSearch();

    // m1、self 初始禁用（已是成员 / 自己）
    const m1Opt = await screen.findByRole('button', { name: /成员一\(m1\)/ });
    expect(m1Opt).toBeDisabled();
    const selfOpt = await screen.findByRole('button', { name: /我自己\(self\)/ });
    expect(selfOpt).toBeDisabled();

    // 选 A → chip + 计数
    fireEvent.click(await screen.findByRole('button', { name: /花A\(a\)/ }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(submitButton()).toHaveTextContent('添加 1 人');
    expect(submitButton()).toBeEnabled();

    // 重键：A 已在下拉中禁用（已选入本批次）；B 仍可选
    const input = screen.getByLabelText('搜索员工') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'aa' } });
    await screen.findByRole('button', { name: /花A\(a\)/ });
    expect(screen.getByRole('button', { name: /花A\(a\)/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /花B\(b\)/ })).toBeEnabled();
  });

  it('连续选 A/B/C → 按钮「添加 3 人」；逐个移除至空 → 按钮禁用（计数 0）', async () => {
    render(
      <SpaceMemberList
        space={space}
        members={[]}
        loading={false}
        onAddMembers={jest.fn()}
        onRemoveMember={jest.fn()}
        onUpdateRole={jest.fn()}
      />,
    );
    await openAndSearch();
    await pickCandidate(/花A\(a\)/);
    await pickCandidate(/花B\(b\)/, true);
    await pickCandidate(/花C\(c\)/, true);

    expect(submitButton()).toHaveTextContent('添加 3 人');
    expect(chipRemoveButtons()).toHaveLength(3);

    // 移除 A、B、C → 计数归 0、按钮禁用
    for (let i = 0; i < 3; i++) {
      fireEvent.click(screen.getAllByLabelText('取消选择')[0]);
      await act(async () => {
        await Promise.resolve();
      });
    }
    expect(submitButton()).toHaveTextContent('添加 0 人');
    expect(submitButton()).toBeDisabled();
  });

  it('in-flight（addMembersLoading=true）→ 提交按钮禁用且呈现加载态', async () => {
    render(
      <SpaceMemberList
        space={space}
        members={[]}
        loading={false}
        onAddMembers={jest.fn()}
        addMembersLoading={true}
        addMembersDisabledReason="正在添加成员…"
        onRemoveMember={jest.fn()}
        onUpdateRole={jest.fn()}
      />,
    );
    await openAndSearch();
    fireEvent.click(await screen.findByRole('button', { name: /花A\(a\)/ }));
    await act(async () => {
      await Promise.resolve();
    });

    const btn = submitButton();
    expect(btn).toBeDisabled();
    expect(btn.textContent ?? '').toMatch(/添加中/);
  });

  it('部分失败重试：提交返回 failed[B] → chips 回填为 [B] → 再次提交仅以 B 调 onAddMembers', async () => {
    const onAddMembers = jest.fn(async (users: SearchedUser[]): Promise<BatchResult | undefined> => {
      const hasB = users.some((u) => u.userId === 'b');
      if (!hasB) return { succeeded: [mem('a')], failed: [] };
      return { succeeded: [mem('a')], failed: [{ userId: 'b', userName: '花B', reason: '已是成员' }] };
    });
    render(
      <SpaceMemberList
        space={space}
        members={[]}
        loading={false}
        onAddMembers={onAddMembers}
        onRemoveMember={jest.fn()}
        onUpdateRole={jest.fn()}
      />,
    );
    await openAndSearch();
    await pickCandidate(/花A\(a\)/);
    await pickCandidate(/花B\(b\)/, true);

    expect(submitButton()).toHaveTextContent('添加 2 人');
    await act(async () => {
      fireEvent.click(submitButton());
    });
    await screen.findByText(/花B\(b\)/); // 等回填 chip 出现
    expect(onAddMembers).toHaveBeenCalledTimes(1);
    expect(onAddMembers).toHaveBeenNthCalledWith(
      1,
      expect.arrayContaining([expect.objectContaining({ userId: 'a' }), expect.objectContaining({ userId: 'b' })]),
      'MEMBER',
    );

    // 回填后：成功者 A 移除；失败者 B 为唯一 chip，按钮「添加 1 人」
    expect(chipRemoveButtons()).toHaveLength(1);
    expect(submitButton()).toHaveTextContent('添加 1 人');

    // 再次提交 → 仅以 B 调用
    await act(async () => {
      fireEvent.click(submitButton());
    });
    await screen.findByText(/花B\(b\)/); // B chip 仍在
    expect(onAddMembers).toHaveBeenCalledTimes(2);
    expect(onAddMembers).toHaveBeenNthCalledWith(2, [expect.objectContaining({ userId: 'b' })], 'MEMBER');
  });
});
