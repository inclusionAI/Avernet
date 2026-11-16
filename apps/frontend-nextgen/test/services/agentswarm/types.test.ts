import {
  isRunStatus,
  isTerminalStatus,
  runStatusLabel,
  RUN_STATUSES,
  TERMINAL_STATUSES,
  type RunStatus,
} from '@/services/agentswarm/types';

test('the status union is exactly the gateway vocabulary and carries no authorization state', () => {
  expect([...RUN_STATUSES]).toEqual([
    'RUNNING',
    'COMPLETED',
    'FAILED',
    'CANCELLED',
    'TIMED_OUT',
    'GUARD_KILLED',
    'STUCK',
  ]);
  const removed = ['WAITING_AUTHORIZATION', 'AUTHORIZATION_CANCELLED', 'STARTING'];
  for (const state of removed) expect(isRunStatus(state)).toBe(false);
});

test('terminal statuses exclude RUNNING and cover every finished state', () => {
  expect(isTerminalStatus('RUNNING')).toBe(false);
  for (const status of TERMINAL_STATUSES) expect(isTerminalStatus(status)).toBe(true);
  expect(TERMINAL_STATUSES).toHaveLength(RUN_STATUSES.length - 1);
});

test('every gateway state has a label', () => {
  for (const status of RUN_STATUSES) {
    expect(runStatusLabel(status)).not.toBe(status);
    expect(runStatusLabel(status).length).toBeGreaterThan(0);
  }
});

test('an unrecognized status falls back to the gateway string rather than a guessed label', () => {
  expect(runStatusLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW');
  expect(runStatusLabel(null)).toBe('未知');
});

test('the status union rejects a literal the gateway cannot report', () => {
  // Compile-time guard: this line fails typecheck if the union widens.
  // @ts-expect-error WAITING_AUTHORIZATION is not a gateway status.
  const removed: RunStatus = 'WAITING_AUTHORIZATION';
  expect(removed).toBe('WAITING_AUTHORIZATION');
});