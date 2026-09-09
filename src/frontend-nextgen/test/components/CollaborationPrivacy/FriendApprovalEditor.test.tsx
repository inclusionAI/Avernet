/** @jest-environment jsdom */
import { FriendApprovalEditor } from '@/components/CollaborationPrivacy/FriendApprovalEditor';
import type { OrganizationSearchEntry } from '@/domain/collaborationPrivacy/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

let mockPartialFriendApprovalEnabled = false;
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getPartialFriendApprovalEnabled: () => ({ status: 'available', value: mockPartialFriendApprovalEnabled }),
  }),
}));

const onSearch = jest.fn(async () => [] as OrganizationSearchEntry[]);

describe('FriendApprovalEditor', () => {
  beforeEach(() => {
    mockPartialFriendApprovalEnabled = false;
    onSearch.mockClear();
  });

  it('only shows open-core approval strategies', () => {
    render(
      <FriendApprovalEditor
        open
        initialConfig={{ mode: 'all', exemptOrganizationPaths: [] }}
        onSearch={onSearch}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );

    expect(screen.getByRole('radiogroup', { name: '好友审批策略' })).toBeInTheDocument();
    expect(
      screen.getByRole('radio', { name: '无需审批 符合 Bot 可见性限制的新申请直接建立好友关系' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '全部审批 所有新好友申请都需要确认' })).toBeInTheDocument();
    expect(screen.queryByText('部分组织免审批')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '搜索组织范围' })).not.toBeInTheDocument();
  });

  it('requires selecting an open-core strategy for legacy partial configuration', () => {
    const onSubmit = jest.fn();
    render(
      <FriendApprovalEditor
        open
        initialConfig={{
          mode: 'partial_exempt',
          exemptOrganizationPaths: [['示例集团', '技术部']],
          exemptDepartmentNos: ['TECH-001'],
        }}
        onSearch={onSearch}
        onClose={jest.fn()}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByText(/当前策略“部分组织免审批”已下线/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存策略' })).toBeDisabled();

    fireEvent.click(screen.getByRole('radio', { name: '全部审批 所有新好友申请都需要确认' }));
    fireEvent.click(screen.getByRole('button', { name: '保存策略' }));

    expect(onSubmit).toHaveBeenCalledWith({
      mode: 'all',
      exemptOrganizationPaths: [],
      exemptDepartmentNos: [],
      exemptOrganizationEntries: [],
    });
  });

  it('keeps partial strategy editor in internal overlay', () => {
    mockPartialFriendApprovalEnabled = true;
    render(
      <FriendApprovalEditor
        open
        initialConfig={{
          mode: 'partial_exempt',
          exemptOrganizationPaths: [['示例集团', '技术部']],
          exemptDepartmentNos: ['TECH-001'],
        }}
        onSearch={onSearch}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );

    expect(screen.getByRole('radio', { name: /部分组织免审批/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('textbox', { name: '搜索组织范围' })).toBeInTheDocument();
  });
});
