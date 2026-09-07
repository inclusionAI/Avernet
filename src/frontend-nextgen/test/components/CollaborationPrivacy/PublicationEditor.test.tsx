/** @jest-environment jsdom */
import { PublicationEditor } from '@/components/CollaborationPrivacy/PublicationEditor';
import type { OrganizationSearchEntry } from '@/domain/collaborationPrivacy/types';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

let mockRestrictedPublicationScopeEnabled = true;
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getRestrictedPublicationScopeEnabled: () => ({ status: 'available', value: mockRestrictedPublicationScopeEnabled }),
  }),
}));

const existingEntry: OrganizationSearchEntry = {
  deptNo: 'TECH-001',
  path: ['示例集团-技术事业部-平台团队'],
};
const addedEntry: OrganizationSearchEntry = {
  deptNo: 'PRODUCT-001',
  path: ['示例集团 / 产品事业部 / 体验团队'],
};

describe('PublicationEditor', () => {
  beforeEach(() => {
    mockRestrictedPublicationScopeEnabled = true;
  });

  afterEach(() => {
    jest.useRealTimers();
    (history.push as jest.Mock).mockClear();
  });

  it('describes collaboration square discovery for each audience', () => {
    const props = {
      open: true,
      initialConfig: { scope: 'all' as const, organizationPaths: [] },
      onSearch: jest.fn(async () => []),
      onClose: jest.fn(),
      onSubmit: jest.fn(),
    };
    const { rerender } = render(<PublicationEditor {...props} audience="user" />);

    expect(screen.getByRole('heading', { name: '对其他用户公开' })).toBeInTheDocument();
    const squareLink = screen.getByRole('link', { name: '[协作广场/公开Bot]' });
    expect(squareLink).toHaveAttribute('href', '/collaboration-square/bots');
    expect(squareLink).not.toHaveClass('underline');
    expect(squareLink.parentElement).toHaveTextContent(
      '公开后，其他用户可在 [协作广场/公开Bot] 中发现当前 Bot，并申请添加为好友。',
    );
    fireEvent.click(squareLink);
    expect(history.push).toHaveBeenCalledWith('/collaboration-square/bots');
    expect(screen.getByRole('radio', { name: /限制组织范围/ })).toHaveTextContent(
      '仅所选组织范围可申请添加当前 Bot 为好友',
    );
    expect(
      screen.queryByText('可分别搜索集团、事业部、部门或团队，并连续添加多个范围。提交后将进入审批流程。'),
    ).not.toBeInTheDocument();

    rerender(<PublicationEditor {...props} audience="bot" />);

    expect(screen.getByRole('heading', { name: '对其他 Bot 公开' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '[协作广场/公开Bot]' }).parentElement).toHaveTextContent(
      '公开后，其他 Bot 可在 [协作广场/公开Bot] 中发现当前 Bot，并申请添加为好友。',
    );
    expect(screen.getByRole('radio', { name: /限制组织范围/ })).toHaveTextContent(
      '仅所选组织范围可申请添加当前 Bot 为好友',
    );
  });

  it('Open Core 对用户和 Bot 公开窗口均隐藏限制组织范围', () => {
    mockRestrictedPublicationScopeEnabled = false;
    const props = {
      open: true,
      initialConfig: { scope: 'restricted' as const, organizationPaths: [existingEntry.path] },
      onSearch: jest.fn(async () => []),
      onClose: jest.fn(),
      onSubmit: jest.fn(),
    };
    const { rerender } = render(<PublicationEditor {...props} audience="user" />);

    expect(screen.queryByRole('radio', { name: /限制组织范围/ })).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /不公开/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.queryByText('选择组织范围')).not.toBeInTheDocument();

    rerender(<PublicationEditor {...props} audience="bot" />);

    expect(screen.queryByRole('radio', { name: /限制组织范围/ })).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /不公开/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.queryByText('选择组织范围')).not.toBeInTheDocument();
  });

  it('restores configured departments and submits their codes with original department names', async () => {
    jest.useFakeTimers();
    const onSubmit = jest.fn();
    render(
      <PublicationEditor
        open
        audience="user"
        initialConfig={{
          scope: 'restricted',
          organizationPaths: [existingEntry.path],
          organizationEntries: [existingEntry],
        }}
        onSearch={jest.fn(async () => [addedEntry])}
        onClose={jest.fn()}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole('radiogroup', { name: '公开范围' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /全部公开/ })).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('radio', { name: /限制组织范围/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('示例集团-技术事业部-平台团队')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '搜索组织团队范围' }), {
      target: { value: '产品' },
    });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /示例集团 \/ 产品事业部 \/ 体验团队/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /示例集团 \/ 产品事业部 \/ 体验团队/ }));
    fireEvent.click(screen.getByRole('button', { name: '提交审批' }));

    expect(onSubmit).toHaveBeenCalledWith(
      {
        scope: 'restricted',
        organizationPaths: [existingEntry.path, addedEntry.path],
        organizationEntries: [existingEntry, addedEntry],
      },
      [
        { deptNo: 'TECH-001', deptName: '示例集团-技术事业部-平台团队' },
        { deptNo: 'PRODUCT-001', deptName: '示例集团 / 产品事业部 / 体验团队' },
      ],
    );
  });
});
