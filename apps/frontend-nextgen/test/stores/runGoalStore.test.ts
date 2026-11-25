import { useRunGoalStore } from '@/stores/runGoalStore';

beforeEach(() => useRunGoalStore.getState().reset());

test('a submitted goal is remembered against its run', () => {
  useRunGoalStore.getState().remember('run-1', 'Say hello world');
  expect(useRunGoalStore.getState().goals['run-1']).toBe('Say hello world');
});

test('a blank goal is not recorded — there is nothing to show', () => {
  useRunGoalStore.getState().remember('run-1', '   ');
  expect(useRunGoalStore.getState().goals).toEqual({});
});

test('re-remembering the same run updates its label', () => {
  useRunGoalStore.getState().remember('run-1', 'first');
  useRunGoalStore.getState().remember('run-1', 'second');
  expect(useRunGoalStore.getState().goals['run-1']).toBe('second');
});

test('the map is bounded rather than growing without limit', () => {
  for (let i = 0; i < 130; i++) useRunGoalStore.getState().remember(`run-${i}`, `goal ${i}`);
  const keys = Object.keys(useRunGoalStore.getState().goals);
  expect(keys.length).toBeLessThanOrEqual(100);
  // The newest survive; the oldest were dropped.
  expect(keys).toContain('run-129');
  expect(keys).not.toContain('run-0');
});

test('forgetting a run removes only that label', () => {
  useRunGoalStore.getState().remember('a', 'A');
  useRunGoalStore.getState().remember('b', 'B');
  useRunGoalStore.getState().forget('a');
  expect(useRunGoalStore.getState().goals).toEqual({ b: 'B' });
});