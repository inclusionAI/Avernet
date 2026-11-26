import {
  isRunStatus,
  isTerminalStatus,
  runStatusLabel,
  runStatusTone,
  RUN_STATUS_TONES,
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

test('every gateway state has a tone, and the tones actually distinguish states', () => {
  for (const status of RUN_STATUSES) expect(RUN_STATUS_TONES[status]).toBeTruthy();

  // A guard kill is neither a clean success nor an internal failure, and a
  // user cancel is not a defect — collapsing them into error/success was the
  // bug this map exists to prevent.
  expect(runStatusTone('COMPLETED')).toBe('success');
  expect(runStatusTone('RUNNING')).toBe('primary');
  expect(runStatusTone('GUARD_KILLED')).toBe('warning');
  expect(runStatusTone('CANCELLED')).toBe('neutral');
  for (const status of ['FAILED', 'TIMED_OUT', 'STUCK'] as const) {
    expect(runStatusTone(status)).toBe('error');
  }
});

test('an unrecognized status reads as no-judgement, never as success', () => {
  expect(runStatusTone('SOMETHING_NEW')).toBe('neutral');
  expect(runStatusTone(null)).toBe('neutral');
});