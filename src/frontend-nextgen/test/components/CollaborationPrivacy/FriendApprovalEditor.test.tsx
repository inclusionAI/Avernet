/** @jest-environment jsdom */
import { FriendApprovalEditor } from '@/components/CollaborationPrivacy/FriendApprovalEditor';
import type { OrganizationSearchEntry } from '@/domain/collaborationPrivacy/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const entry: OrganizationSearchEntry = {
  deptNo: 'TECH-001',
  path: ['示例集团 / 技术部'],
};

describe('FriendApprovalEditor', () => {
  it('shows the existing exempt organization paths when the editor opens', () => {
    render(
      <FriendApprovalEditor
        open
        initialConfig={{
          mode: 'partial_exempt',
          exemptOrganizationPaths: [['示例集团 / 技术部'], ['示例集团-产品部']],
          exemptDepartmentNos: ['TECH-001', 'PRODUCT-001'],
          exemptOrganizationEntries: [
            { deptNo: 'TECH-001', path: ['示例集团 / 技术部'] },
            { deptNo: 'PRODUCT-001', path: ['示例集团-产品部'] },
          ],
        }}
        onSearch={jest.fn(async () => [] as OrganizationSearchEntry[])}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );

    expect(screen.getByRole('radiogroup', { name: '好友审批策略' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /部分组织免审批/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('已选组织范围（2）')).toBeInTheDocument();
    expect(screen.getByText('示例集团 / 技术部')).toBeInTheDocument();
    expect(screen.getByText('示例集团-产品部')).toBeInTheDocument();
  });

  it('submits the selected department code together with the display path', async () => {
    jest.useFakeTimers();
    const onSearch = jest.fn(async () => [entry]);
    const onSubmit = jest.fn();
    render(
      <FriendApprovalEditor
        open
        initialConfig={{ mode: 'all', exemptOrganizationPaths: [] }}
        onSearch={onSearch}
        onClose={jest.fn()}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /部分组织免审批/ }));
    fireEvent.change(screen.getByRole('textbox', { name: '搜索组织团队范围' }), { target: { value: '技术部' } });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByRole('button', { name: /技术部/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /技术部/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存策略' }));

    expect(onSubmit).toHaveBeenCalledWith({
      mode: 'partial_exempt',
      exemptOrganizationPaths: [entry.path],
      exemptDepartmentNos: ['TECH-001'],
      exemptOrganizationEntries: [entry],
    });
    jest.useRealTimers();
  });
});
