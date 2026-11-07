/** @jest-environment jsdom */
import { ExpertTemplatePicker } from '@/components/BotWorkshop/CreateBotModal/ExpertTemplatePicker';
import { backendRequest } from '@/services/backendApi/httpClient';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
jest.mock('@/services/backendApi/httpClient', () => ({ ...jest.requireActual('@/services/backendApi/httpClient'), backendRequest: jest.fn() }));

test('模板搜索等待300ms及输入法完成，并忽略先前查询的迟到结果', async () => {
  jest.useFakeTimers();
  try {
    let resolveOld: (value: unknown) => void = () => {};
    const request = jest.mocked(backendRequest);
    const expert = { expert_id: 'writer', name: '新结果', description: '说明', tags: [], category: '内容', creation_count: 0, first_added_at: '2026-01-01' };
    request.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
    request.mockResolvedValue({ code: 200000, data: { total: 1, items: [expert] } });
    render(<ExpertTemplatePicker onSelect={jest.fn()} />);
    expect(request).toHaveBeenCalledTimes(1);
    const input = screen.getByRole('textbox', { name: '搜索专家模板' });
    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: '产' } });
    await act(async () => { jest.advanceTimersByTime(500); });
    expect(request).toHaveBeenCalledTimes(1);
    fireEvent.change(input, { target: { value: '产品' } });
    fireEvent.compositionEnd(input, { data: '产品' });
    await act(async () => { jest.advanceTimersByTime(299); });
    expect(request).toHaveBeenCalledTimes(1);
    await act(async () => { jest.advanceTimersByTime(1); });
    expect(request).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenLastCalledWith('/openapi/v1/experts', expect.objectContaining({ params: expect.objectContaining({ keyword: '产品', page_num: 1 }) }));
    expect(screen.getByRole('button', { name: '新结果' })).toBeInTheDocument();
    await act(async () => { resolveOld({ code: 200000, data: { total: 1, items: [{ ...expert, name: '旧结果' }] } }); });
    expect(screen.queryByRole('button', { name: '旧结果' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新结果' })).toBeInTheDocument();
  } finally { jest.useRealTimers(); }
});
