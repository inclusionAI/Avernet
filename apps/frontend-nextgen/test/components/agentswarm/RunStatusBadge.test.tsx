/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { RunStatusBadge } from '@/components/agentswarm/RunStatusBadge';

test.each([
  ['RUNNING', '执行中'],
  ['COMPLETED', '已完成'],
  ['FAILED', '已失败'],
  ['CANCELLED', '已取消'],
  ['TIMED_OUT', '已超时'],
  ['GUARD_KILLED', '已终止'],
  ['STUCK', '已卡住'],
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

const ERROR_LABELS: Record<string, string> = { FAILED: '已失败', TIMED_OUT: '已超时', STUCK: '已卡住' };

test('the badge and the shared tone map agree — one colour per state, product-wide', () => {
  // This component used to carry its own tone table that disagreed with the
  // rest of the product (TIMED_OUT warning / GUARD_KILLED error, inverted).
  // Asserting against the shared map stops that drift from coming back.
  const ERRORS = ['FAILED', 'TIMED_OUT', 'STUCK'];
  for (const status of ERRORS) {
    const { container, unmount } = render(<RunStatusBadge status={status} />);
    expect(container.textContent).toContain(ERROR_LABELS[status]);
    unmount();
  }

  const { container } = render(<RunStatusBadge status="GUARD_KILLED" />);
  // Amber, not red: a guard cut the run short from outside its own logic.
  expect(container.querySelector('.text-warning')).not.toBeNull();
  expect(container.querySelector('.text-destructive')).toBeNull();
});