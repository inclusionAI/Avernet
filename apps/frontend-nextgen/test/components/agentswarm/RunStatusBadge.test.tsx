/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { RunStatusBadge } from '@/components/agentswarm/RunStatusBadge';

test.each([
  ['RUNNING', '执行中'],
  ['COMPLETED', '已完成'],
  ['FAILED', '执行失败'],
  ['CANCELLED', '已取消'],
  ['TIMED_OUT', '执行超时'],
  ['GUARD_KILLED', '已被运行保护机制终止'],
  ['STUCK', '执行无法继续'],
])('%s renders its own label', (status, label) => {
  render(<RunStatusBadge status={status} />);
  expect(screen.getByText(label)).toBeInTheDocument();
});

test('an unknown status renders the gateway string rather than a guessed label', () => {
  render(<RunStatusBadge status="SOMETHING_NEW" />);
  expect(screen.getByText('SOMETHING_NEW')).toBeInTheDocument();
});

test('a missing status does not fabricate a state', () => {
  render(<RunStatusBadge status={null} />);
  expect(screen.getByText('未知')).toBeInTheDocument();
});

test('no authorization state can be rendered', () => {
  for (const removed of ['WAITING_AUTHORIZATION', 'AUTHORIZATION_CANCELLED', 'STARTING']) {
    const { unmount } = render(<RunStatusBadge status={removed} />);
    expect(screen.getByText(removed)).toBeInTheDocument();
    unmount();
  }
  expect(screen.queryByText('待授权 · 未启动')).toBeNull();
});