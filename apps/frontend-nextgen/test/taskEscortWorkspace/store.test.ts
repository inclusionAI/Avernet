import type { TaskEscortWorkspaceSnapshot } from '@/domain/taskEscortWorkspace';
import { createTaskEscortWorkspaceStore } from '@/stores/taskEscortWorkspaceStore';

const settings = {
  enabled: false,
  autoAnalyze: false,
  humanReview: true,
  notifications: false,
  recurrence: false,
  sensitivity: 'balanced',
  webhook: '',
};
const empty = (): TaskEscortWorkspaceSnapshot => ({
  workflows: [],
  runs: [],
  issues: [],
  experiences: [],
  settings: { ...settings },
  settingsDirty: false,
  loading: false,
  error: null,
});

describe('Task Escort Workspace vanilla store', () => {
  it('exposes sync setters and immutable snapshots without changing caller-owned inputs', () => {
    const input = empty();
    const store = createTaskEscortWorkspaceStore(input);
    const listener = jest.fn();
    const unsubscribe = store.subscribe(listener);
    store.getState().setLoading(true);
    store.getState().setError('read failed');
    store.getState().setSettings({ ...settings, enabled: true }, true);
    expect(store.getState().snapshot).toMatchObject({
      loading: true,
      error: 'read failed',
      settingsDirty: true,
      settings: { enabled: true },
    });
    expect(input.loading).toBe(false);
    expect(Object.isFrozen(input.settings)).toBe(false);
    expect(Object.isFrozen(store.getState().snapshot.settings)).toBe(true);
    expect(listener).toHaveBeenCalledTimes(3);
    unsubscribe();
    store.getState().reset();
    expect(store.getState().snapshot).toEqual(empty());
    expect(listener).toHaveBeenCalledTimes(3);
  });

  it('replaces domain collections synchronously and isolates factories', () => {
    const first = createTaskEscortWorkspaceStore(empty());
    const second = createTaskEscortWorkspaceStore(empty());
    first.getState().setSnapshot({ ...empty(), loading: true });
    first.getState().setCollections({ workflows: [], runs: [], issues: [], experiences: [] });
    expect(first.getState().snapshot.loading).toBe(true);
    expect(second.getState().snapshot.loading).toBe(false);
    expect(createTaskEscortWorkspaceStore().getState().snapshot.runs).toEqual([]);
  });
});
