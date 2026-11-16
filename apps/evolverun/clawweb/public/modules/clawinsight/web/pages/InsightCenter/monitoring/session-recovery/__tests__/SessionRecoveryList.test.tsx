import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { SessionRecoveryList } from '../SessionRecoveryList';
const api = vi.hoisted(() => ({ tasks: vi.fn() }));
vi.mock('../../../../../api/session-recovery', () => ({ sessionRecoveryApi: api }));
vi.mock('../../MonitoringBotSelector', () => ({ MonitoringBotSelector: () => <div>Bot 选择器</div> }));
vi.mock('../../../../../api/monitoring', () => ({ monitoringApi: { targetStatus: vi.fn() }, monitoringErrorText: () => '请求失败' }));
function Location() { return <output>{useLocation().search}</output>; }
it('keeps task state separate from effect and preserves filters/page when opening the existing detail', async () => {
  api.tasks.mockResolvedValue({ page: 2, pageSize: 20, total: 21, items: [{ taskId: 'SR-one', botId: 'default', entityId: 'owner', env: 'prod',
    sessionKey: 'session', status: 'completed', phase: 'verify', effect: 'unknown', createdAt: 1700000000 }] });
  render(<MemoryRouter initialEntries={['/insight?module=monitoring&monitoringTab=recovery&recoveryPage=2&recoveryStatus=completed']}><SessionRecoveryList /><Location /></MemoryRouter>);
  expect(await screen.findByText('尚未确认')).toBeInTheDocument();
  expect(screen.getAllByText('已完成').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('recoveryTaskId=SR-one'));
  expect(screen.getByRole('status')).toHaveTextContent('recoveryPage=2');
  expect(screen.getByRole('status')).toHaveTextContent('recoveryStatus=completed');
});
