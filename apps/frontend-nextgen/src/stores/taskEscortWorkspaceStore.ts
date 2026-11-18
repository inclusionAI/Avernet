import type { TaskEscortSettings, TaskEscortWorkspaceSnapshot } from '@/domain/taskEscortWorkspace';
import { emptyWorkspaceSnapshot } from '@/domain/taskEscortWorkspace';
import { immutableCopy } from '@/domain/taskEscortWorkspace/immutable';
import { createStore } from 'zustand/vanilla';

type Collections = Pick<TaskEscortWorkspaceSnapshot, 'workflows' | 'runs' | 'issues' | 'experiences'>;
export interface TaskEscortWorkspaceStoreState {
  snapshot: TaskEscortWorkspaceSnapshot;
  setSnapshot: (snapshot: TaskEscortWorkspaceSnapshot) => void;
  setCollections: (collections: Collections) => void;
  setSettings: (settings: TaskEscortSettings, settingsDirty: boolean) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}
/** Session-local vanilla Zustand store. All setters are synchronous and policy-free. */
export function createTaskEscortWorkspaceStore(initial = emptyWorkspaceSnapshot()) {
  const baseline = immutableCopy(initial);
  return createStore<TaskEscortWorkspaceStoreState>((set) => ({
    snapshot: baseline,
    setSnapshot: (snapshot) => set({ snapshot: immutableCopy(snapshot) }),
    setCollections: (collections) =>
      set((state) => ({ snapshot: immutableCopy({ ...state.snapshot, ...collections }) })),
    setSettings: (settings, settingsDirty) =>
      set((state) => ({ snapshot: immutableCopy({ ...state.snapshot, settings, settingsDirty }) })),
    setLoading: (loading) => set((state) => ({ snapshot: immutableCopy({ ...state.snapshot, loading }) })),
    setError: (error) => set((state) => ({ snapshot: immutableCopy({ ...state.snapshot, error }) })),
    reset: () => set({ snapshot: immutableCopy(baseline) }),
  }));
}
export type TaskEscortWorkspaceStore = ReturnType<typeof createTaskEscortWorkspaceStore>;
