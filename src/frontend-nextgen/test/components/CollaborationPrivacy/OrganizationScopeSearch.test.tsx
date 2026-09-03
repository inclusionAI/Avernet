/** @jest-environment jsdom */
import { OrganizationScopeSearch } from '@/components/CollaborationPrivacy/OrganizationScopeSearch';
import type { OrganizationSearchEntry } from '@/domain/collaborationPrivacy/types';
import { afterEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const entry: OrganizationSearchEntry = {
  deptNo: 'TECH-001',
  path: ['示例集团-技术事业部-平台团队'],
};

describe('OrganizationScopeSearch', () => {
  afterEach(() => {
    jest.useRealTimers();
  });

  it('waits for input to settle and searches only the final department keyword', async () => {
    jest.useFakeTimers();
    const onSearch = jest.fn(async (keyword: string, signal?: AbortSignal) => {
      void keyword;
      void signal;
      return [] as OrganizationSearchEntry[];
    });
    render(<OrganizationScopeSearch value={[]} onChange={jest.fn()} onSearch={onSearch} />);

    const input = screen.getByRole('textbox', { name: '搜索组织团队范围' });
    fireEvent.change(input, { target: { value: '技' } });
    await act(async () => {
      jest.advanceTimersByTime(350);
    });
    fireEvent.change(input, { target: { value: '技术' } });
    await act(async () => {
      jest.advanceTimersByTime(350);
    });
    fireEvent.change(input, { target: { value: '技术部' } });
    await act(async () => {
      jest.advanceTimersByTime(999);
    });

    expect(onSearch).not.toHaveBeenCalled();

    await act(async () => {
      jest.advanceTimersByTime(1);
      await Promise.resolve();
    });
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith('技术部', expect.any(AbortSignal));
  });

  it('cancels the active department search as soon as the keyword changes', async () => {
    jest.useFakeTimers();
    let firstSignal: AbortSignal | undefined;
    const onSearch = jest.fn((keyword: string, signal?: AbortSignal) => {
      if (keyword !== '技术') return Promise.resolve([]);
      firstSignal = signal;
      return new Promise<OrganizationSearchEntry[]>((_, reject) => {
        signal?.addEventListener('abort', () => reject(new Error('aborted')));
      });
    });
    render(<OrganizationScopeSearch value={[]} onChange={jest.fn()} onSearch={onSearch} />);

    const input = screen.getByRole('textbox', { name: '搜索组织团队范围' });
    fireEvent.change(input, { target: { value: '技术' } });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(onSearch).toHaveBeenCalledTimes(1);

    fireEvent.change(input, { target: { value: '技术部' } });
    expect(firstSignal?.aborted).toBe(true);
    expect(onSearch).toHaveBeenCalledTimes(1);

    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(onSearch).toHaveBeenCalledTimes(2);
    expect(onSearch).toHaveBeenLastCalledWith('技术部', expect.any(AbortSignal));
    expect(screen.queryByText('部门搜索正在进行，请稍后再试')).not.toBeInTheDocument();
  });

  it('shows the complete department name without rewriting delimiters or adding level suffixes', async () => {
    jest.useFakeTimers();
    const slashEntry: OrganizationSearchEntry = {
      deptNo: 'TECH-002',
      path: ['示例集团 / 技术事业部 / 算法团队'],
    };
    render(
      <OrganizationScopeSearch value={[]} onChange={jest.fn()} onSearch={jest.fn(async () => [entry, slashEntry])} />,
    );

    fireEvent.change(screen.getByRole('textbox', { name: '搜索组织团队范围' }), { target: { value: '技术' } });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });

    expect(await screen.findByText('示例集团-技术事业部-平台团队')).toBeInTheDocument();
    expect(screen.getByText('示例集团 / 技术事业部 / 算法团队')).toBeInTheDocument();
    expect(screen.queryByText(/· (集团|事业部|部门|团队)/)).not.toBeInTheDocument();
  });

  it('adds and removes the complete search entry, including deptNo', async () => {
    jest.useFakeTimers();
    const onChange = jest.fn();
    const onEntriesChange = jest.fn();
    const onSearch = jest.fn(async () => [entry]);
    render(
      <OrganizationScopeSearch value={[]} onChange={onChange} onEntriesChange={onEntriesChange} onSearch={onSearch} />,
    );

    fireEvent.change(screen.getByRole('textbox', { name: '搜索组织团队范围' }), { target: { value: '技术部' } });
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByRole('button', { name: /平台团队/ })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /平台团队/ }));
    expect(onChange).toHaveBeenLastCalledWith([entry.path]);
    expect(onEntriesChange).toHaveBeenLastCalledWith([entry]);

    render(
      <OrganizationScopeSearch
        value={[entry.path]}
        selectedEntries={[entry]}
        onChange={onChange}
        onEntriesChange={onEntriesChange}
        onSearch={onSearch}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: `移除 ${entry.path.join(' / ')}` }));
    expect(onChange).toHaveBeenLastCalledWith([]);
    expect(onEntriesChange).toHaveBeenLastCalledWith([]);
  });
});
