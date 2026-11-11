/**
 * A local run_id → goal map.
 *
 * `GET /runs` returns `RunSummaryDTO`, which has no `goal`: only the per-run
 * detail does. Asking detail for every row would multiply the list into N
 * requests, so the goal is remembered from the submit that created the run and
 * shown where it is known. A run this client did not submit simply has no label
 * here — it is never guessed from anything else.
 */
import { create } from 'zustand';

interface RunGoalState {
  goals: Record<string, string>;
  remember: (runId: string, goal: string) => void;
  forget: (runId: string) => void;
  reset: () => void;
}

/** Only a bounded window is kept; the map is a label cache, not a record. */
const MAX_ENTRIES = 100;

export const useRunGoalStore = create<RunGoalState>((set) => ({
  goals: {},
  remember: (runId, goal) =>
    set((state) => {
      const trimmed = (goal || '').trim();
      if (!runId || !trimmed) return state;
      const next = { ...state.goals, [runId]: trimmed };
      const keys = Object.keys(next);
      // Drop the oldest insertions rather than growing without bound.
      for (const key of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) delete next[key];
      return { goals: next };
    }),
  forget: (runId) =>
    set((state) => {
      if (!(runId in state.goals)) return state;
      const next = { ...state.goals };
      delete next[runId];
      return { goals: next };
    }),
  reset: () => set({ goals: {} }),
}));