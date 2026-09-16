import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { fetchJson } from '@avernet/clawweb-shared/web/api/client';
import GroupRepairSelection from '../GroupRepairSelection';
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });
const fixture = { capability: 'issue-group-repair/v1', inputDigest: 'digest', candidates: [
  { id: 'a', summary: '修复 A', sources: [] }, { id: 'b', summary: '修复 B', sources: [] },
], bots: [{ botId: 'bot', env: 'prod' }], tasks: [], uncovered: 1 };
it('defaults to all, preserves exclusions across rerenders, and submits IDs only after confirmation', async () => {
  vi.mocked(fetchJson).mockResolvedValue(fixture);
  const view = render(<GroupRepairSelection workflowId="wf" signature="sig" />);
  await screen.findByLabelText('修复 A');
  fireEvent.click(screen.getByLabelText('修复 B'));
  view.rerender(<GroupRepairSelection workflowId="wf" signature="sig" />);
  expect((screen.getByLabelText('修复 B') as HTMLInputElement).checked).toBe(false);
  fireEvent.change(screen.getByLabelText('执行 Bot'), { target: { value: JSON.stringify(['bot', 'prod']) } });
  fireEvent.click(screen.getByRole('button', { name: '修复所选问题（1 条建议）' }));
  expect(vi.mocked(fetchJson).mock.calls).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '确认修复并部署' }));
  await waitFor(() => expect(vi.mocked(fetchJson).mock.calls.some(call => call[1]?.method === 'POST')).toBe(true));
  const post = vi.mocked(fetchJson).mock.calls.find(call => call[1]?.method === 'POST')!;
  const body = JSON.parse(String(post[1]?.body));
  expect(body.candidateIds).toEqual(['a']);
  expect(body.inputDigest).toBe('digest');
  expect(body.proposal).toBeUndefined();
});
it('does not offer application when candidate loading fails', async () => {
  vi.mocked(fetchJson).mockRejectedValue(new Error('服务不可用'));
  render(<GroupRepairSelection workflowId="wf" signature="sig" />);
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: /修复所选/ })).toBeNull();
});
it('requires review when sources update without silently selecting newly arrived suggestions', async () => {
  vi.useFakeTimers();
  vi.mocked(fetchJson).mockResolvedValueOnce(fixture).mockResolvedValue({ ...fixture, inputDigest: 'new', candidates: [...fixture.candidates, { id: 'c', summary: '修复 C', sources: [] }] });
  await act(async () => { render(<GroupRepairSelection workflowId="wf" signature="sig" />); });
  fireEvent.change(screen.getByLabelText('执行 Bot'), { target: { value: JSON.stringify(['bot', 'prod']) } });
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect((screen.getByLabelText('修复 C') as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('button', { name: '修复所选问题（2 条建议）' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '已核对更新' }));
  expect((screen.getByRole('button', { name: '修复所选问题（2 条建议）' }) as HTMLButtonElement).disabled).toBe(false);
});
