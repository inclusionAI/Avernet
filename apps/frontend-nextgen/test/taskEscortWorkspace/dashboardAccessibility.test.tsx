/** @jest-environment jsdom */
import { DashboardTrend } from '@/pages/TaskEscort/dashboard/DashboardCharts';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

it('lets a keyboard user reveal and read actual per-day date/rate/failure values, including unknowns', async () => {
  const user = userEvent.setup();
  render(
    <DashboardTrend
      resource={{
        loading: false,
        error: null,
        retry: jest.fn(),
        data: {
          from: 1,
          to: 2,
          dates: [
            {
              date: '2026-11-17',
              runCount: 8,
              succeededCount: 7,
              failedCount: 3,
              successRate: 0.875,
              avgDurationMs: null,
              tokenUsage: 0,
            },
            {
              date: '2026-11-18',
              runCount: null,
              succeededCount: null,
              failedCount: null,
              successRate: null,
              avgDurationMs: null,
              tokenUsage: null,
            },
          ],
        },
      }}
      range="global"
      globalLabel="近7天"
      onRangeChange={jest.fn()}
    />,
  );
  expect(screen.queryByRole('table', { name: '运行质量趋势明细' })).toBeNull();
  const toggle = screen.getByRole('button', { name: '查看趋势明细' });
  for (let i = 0; i < 3 && document.activeElement !== toggle; i++) await user.tab();
  expect(document.activeElement).toBe(toggle);
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  await user.keyboard('{Enter}');
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  const table = screen.getByRole('table', { name: '运行质量趋势明细' });
  expect(table.id).toBe(toggle.getAttribute('aria-controls'));
  const rows = within(table).getAllByRole('row');
  expect(rows[1].textContent).toContain('2026-11-17');
  expect(within(rows[1]).getByText('87.5%')).toBeTruthy();
  expect(within(rows[1]).getByText('3')).toBeTruthy();
  expect(within(rows[2]).getAllByText('—')).toHaveLength(2);
  await user.keyboard('{Enter}');
  expect(screen.queryByRole('table', { name: '运行质量趋势明细' })).toBeNull();
});
