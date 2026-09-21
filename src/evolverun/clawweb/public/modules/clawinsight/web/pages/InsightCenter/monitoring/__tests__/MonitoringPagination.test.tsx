import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DiagnosisPage } from '../../../../types/monitoring';
import { MonitoringPagination, paginationTokens } from '../MonitoringPagination';

afterEach(cleanup);
const page = (current = 1, totalPages = 20): DiagnosisPage => ({
  botId: 'bot', page: current, pageSize: 20, total: totalPages * 20, totalPages,
  problemTypes: [], counts: { all: totalPages * 20, alert: 0, pass: 0, unresolved: 0 }, items: [],
});
function props(current = 1, pages = 20) {
  return { data: page(current, pages), pageSize: 20, loading: false, resetKey: 'same', onPageChange: vi.fn(), onPageSizeChange: vi.fn() };
}

describe('MonitoringPagination', () => {
  it.each([
    [1, 0, []], [1, 1, [1]], [1, 2, [1, 2]], [2, 2, [1, 2]],
    [4, 7, [3, 4, 5]], [1, 20, [1, 2, 3]], [2, 20, [1, 2, 3]],
    [4, 20, [3, 4, 5]], [5, 20, [4, 5, 6]], [16, 20, [15, 16, 17]],
    [19, 20, [18, 19, 20]], [20, 20, [18, 19, 20]],
    [1000000, 2147483647, [999999, 1000000, 1000001]],
  ])('bounds tokens at page %s of %s', (current, total, expected) => {
    expect(paginationTokens(current as number, total as number)).toEqual(expected);
  });
  it('shows at most three numeric controls and keeps every page reachable', () => {
    const p = props(5); render(<MonitoringPagination {...p} />);
    expect(screen.getAllByRole('button', { name: /^第 \d+ 页$/ })).toHaveLength(3);
    expect(screen.queryByRole('button', { name: /跳 5 页/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '首页' })); expect(p.onPageChange).toHaveBeenLastCalledWith(1);
    fireEvent.click(screen.getByRole('button', { name: '末页' })); expect(p.onPageChange).toHaveBeenLastCalledWith(20);
    fireEvent.click(screen.getByRole('button', { name: '上一页' })); expect(p.onPageChange).toHaveBeenLastCalledWith(4);
    fireEvent.click(screen.getByRole('button', { name: '下一页' })); expect(p.onPageChange).toHaveBeenLastCalledWith(6);
    fireEvent.click(screen.getByRole('button', { name: '第 4 页' })); expect(p.onPageChange).toHaveBeenLastCalledWith(4);
    p.onPageChange.mockClear(); fireEvent.click(screen.getByRole('button', { name: '第 5 页' })); expect(p.onPageChange).not.toHaveBeenCalled();
  });
  it.each(['', '0', '-1', '1.5', '1e1', '+2', ' 2', '2 ', 'x', '21', '9007199254740992'])('rejects invalid jump %j without requests', value => {
    const p = props(); render(<MonitoringPagination {...p} />);
    fireEvent.change(screen.getByLabelText('跳转页码'), { target: { value } });
    fireEvent.click(screen.getByRole('button', { name: '跳转' }));
    expect(screen.getByRole('alert')).toHaveTextContent('1–20'); expect(p.onPageChange).not.toHaveBeenCalled();
    expect(screen.getByLabelText('跳转页码')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('跳转页码')).toHaveFocus();
  });
  it('submits on form/Enter, changes size, and Escape clears drafts without a request', () => {
    const p = props(); render(<MonitoringPagination {...p} />);
    const input = screen.getByLabelText('跳转页码');
    fireEvent.change(input, { target: { value: '12' } }); expect(p.onPageChange).not.toHaveBeenCalled();
    fireEvent.submit(input.closest('form')!); expect(p.onPageChange).toHaveBeenCalledWith(12); expect(input).toHaveValue(''); expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: '7' } }); fireEvent.keyDown(input, { key: 'Escape' }); expect(input).toHaveValue('');
    expect(p.onPageChange).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText('每页条数'), { target: { value: '50' } }); expect(p.onPageSizeChange).toHaveBeenCalledWith(50);
  });
  it('keeps draft and focus during polling, disables actions, and clears only for a new query', () => {
    const p = props(); const view = render(<MonitoringPagination {...p} />);
    const input = screen.getByLabelText('跳转页码'); input.focus(); fireEvent.change(input, { target: { value: '17' } });
    view.rerender(<MonitoringPagination {...p} loading />);
    expect(input).toHaveFocus(); expect(input).toHaveValue('17'); expect(input).not.toBeDisabled();
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled();
    expect(screen.getByLabelText('每页条数')).toBeDisabled();
    view.rerender(<MonitoringPagination {...p} data={page(1, 25)} />); expect(input).toHaveValue('17'); expect(input).toHaveFocus();
    view.rerender(<MonitoringPagination {...p} resetKey="changed-filter" />); expect(input).toHaveValue('');
  });
  it('keeps the jump input focus through a submitted page load, even before new data arrives', () => {
    const p = props(); const view = render(<MonitoringPagination {...p} />);
    const input = screen.getByLabelText('跳转页码');
    fireEvent.change(input, { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: '跳转' }));
    view.rerender(<MonitoringPagination {...p} data={null} loading resetKey="page12" />);
    expect(input).not.toBeDisabled(); expect(input).toHaveFocus();
    view.rerender(<MonitoringPagination {...p} data={page(12)} resetKey="page12" />);
    expect(input).toHaveFocus();
  });
  it('restores focus to the new current page when numeric controls are replaced during loading', () => {
    const p = props(); const view = render(<MonitoringPagination {...p} />);
    const button = screen.getByRole('button', { name: '第 3 页' }); button.focus(); fireEvent.click(button);
    view.rerender(<MonitoringPagination {...p} data={null} loading resetKey="page3" />);
    view.rerender(<MonitoringPagination {...p} data={page(3)} resetKey="page3" />);
    expect(screen.getByRole('button', { name: '第 3 页' })).toHaveFocus();
  });
  it.each([0, 1])('disables both edges at %s pages and represents empty data as zero of zero', pages => {
    render(<MonitoringPagination {...props(1, pages)} />);
    for (const name of ['首页', '上一页', '下一页', '末页']) expect(screen.getByRole('button', { name })).toBeDisabled();
    expect(screen.getByText(pages ? '1 / 1 页' : '0 / 0 页')).toBeInTheDocument();
    if (!pages) expect(screen.getByLabelText('跳转页码')).toBeDisabled();
  });
  it('distinguishes missing/loading data from an empty successful response', () => {
    render(<MonitoringPagination {...props()} data={null} loading />);
    expect(screen.getByText('— / — 页')).toBeInTheDocument(); expect(screen.queryByText('共 0 条')).not.toBeInTheDocument();
  });
});
