/** @jest-environment jsdom */
import { PublicationEditor } from '@/components/CollaborationPrivacy/PublicationEditor';
import type { OrganizationSearchEntry } from '@/domain/collaborationPrivacy/types';
import { afterEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const existingEntry: OrganizationSearchEntry = {
  deptNo: 'TECH-001',
  path: ['示例集团-技术事业部-平台团队'],
};
const addedEntry: OrganizationSearchEntry = {
  deptNo: 'PRODUCT-001',
  path: ['示例集团 / 产品事业部 / 体验团队'],
};

describe('PublicationEditor', () => {
  afterEach(() => {
    jest.useRealTimers();
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
    expect(screen.getByRole('radio', { name: /指定组织/ })).toHaveAttribute('aria-checked', 'true');
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
