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

    expect(screen.getByRole('heading', { name: 'Bot 可见性：对用户' })).toBeInTheDocument();
    const squareLink = screen.getByRole('link', { name: '[协作广场/公开Bot]' });
    expect(squareLink).toHaveAttribute('href', '/collaboration-square/bots');
    expect(squareLink).not.toHaveClass('underline');
    expect(squareLink.parentElement).toHaveTextContent(
      '选择当前 Bot 在[协作广场/公开Bot]中的可见性，以及其他用户或 Bot 能否申请当前 Bot 为好友。',
    );
    fireEvent.click(squareLink);
    expect(history.push).toHaveBeenCalledWith('/collaboration-square/bots');
    expect(screen.getByRole('radio', { name: /限定组织可申请/ })).toHaveTextContent(
      '其他用户可见，仅选中组织范围内的用户可申请好友',
    );
    expect(screen.queryByText('可搜索组织范围，并连续添加多个范围。')).not.toBeInTheDocument();

    rerender(<PublicationEditor {...props} audience="bot" />);

    expect(screen.getByRole('heading', { name: 'Bot 可见性：对 Bot' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '[协作广场/公开Bot]' }).parentElement).toHaveTextContent(
      '选择当前 Bot 在[协作广场/公开Bot]中的可见性，以及其他用户或 Bot 能否申请当前 Bot 为好友。',
    );
    expect(screen.getByRole('radio', { name: /限定组织可申请/ })).toHaveTextContent(
      '其他 Bot 可见，仅选中组织范围内的用户的 Bot 可申请好友',
    );
  });

  it('Open Core 对用户和 Bot 可见性窗口均隐藏限定组织可申请', () => {
    mockRestrictedPublicationScopeEnabled = false;
    const props = {
      open: true,
      initialConfig: { scope: 'restricted' as const, organizationPaths: [existingEntry.path] },
      onSearch: jest.fn(async () => []),
      onClose: jest.fn(),
      onSubmit: jest.fn(),
    };
    const { rerender } = render(<PublicationEditor {...props} audience="user" />);

    expect(screen.queryByRole('radio', { name: /限定组织可申请/ })).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /不可见/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.queryByText('选择组织范围')).not.toBeInTheDocument();

    rerender(<PublicationEditor {...props} audience="bot" />);

    expect(screen.queryByRole('radio', { name: /限定组织可申请/ })).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /不可见/ })).toHaveAttribute('aria-checked', 'true');
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

    expect(screen.getByRole('radiogroup', { name: 'Bot 可见性' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /全部可见/ })).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('radio', { name: /限定组织可申请/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('示例集团-技术事业部-平台团队')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '搜索组织范围' }), {
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
    fireEvent.click(screen.getByRole('button', { name: '发起变更' }));

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

  it('外部 Bot 始终提供限定组织可申请，按保存语义提交组织完整名称', async () => {
    // TC Open Core 会隐藏受限选项，但外部 Bot 合同必须提供限定组织可申请（PATCH 直改）。
    mockRestrictedPublicationScopeEnabled = false;
    jest.useFakeTimers();
    const onSubmit = jest.fn();
    const externalEntry: OrganizationSearchEntry = {
      deptNo: 'A4195',
      path: ['蚂蚁集团-CRO线-大安全技术部'],
    };
    render(
      <PublicationEditor
        open
        audience="bot"
        isExternalBot
        initialConfig={{ scope: 'all', organizationPaths: [] }}
        onSearch={jest.fn(async () => [externalEntry])}
        onClose={jest.fn()}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole('radio', { name: /限定组织可申请/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发起变更' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: /限定组织可申请/ }));
    fireEvent.change(screen.getByRole('textbox', { name: '搜索组织范围' }), { target: { value: '大安全' } });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /蚂蚁集团-CRO线-大安全技术部/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /蚂蚁集团-CRO线-大安全技术部/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(onSubmit).toHaveBeenCalledWith(
      {
        scope: 'restricted',
        organizationPaths: [externalEntry.path],
        organizationEntries: [externalEntry],
      },
      [{ deptNo: 'A4195', deptName: '蚂蚁集团-CRO线-大安全技术部' }],
    );
  });

  it('外部 Bot 已配置的名称范围回显不因能力开关降级为不可见', () => {
    mockRestrictedPublicationScopeEnabled = false;
    render(
      <PublicationEditor
        open
        audience="bot"
        isExternalBot
        initialConfig={{ scope: 'restricted', organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']] }}
        onSearch={jest.fn(async () => [])}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );

    expect(screen.getByRole('radio', { name: /限定组织可申请/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('蚂蚁集团-CRO线-大安全技术部')).toBeInTheDocument();
    expect(screen.queryByText('当前可见性“限定组织可申请”已下线，请重新选择“不可见”或“全部可见”。')).not.toBeInTheDocument();
  });

  it('外部 Bot 配置未变化时提示无需保存，而非审批话术', () => {
    render(
      <PublicationEditor
        open
        audience="bot"
        isExternalBot
        initialConfig={{ scope: 'all', organizationPaths: [] }}
        onSearch={jest.fn(async () => [])}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );

    expect(screen.getByText('可见性未发生变化，无需保存')).toBeInTheDocument();
    expect(screen.queryByText('可见性未发生变化，无需提交')).not.toBeInTheDocument();
  });
});
