/**
 * The Agent Compute side panel's open state.
 *
 * A panel belongs to one conversation: opening it in session A must not surface
 * it in session B. The panel therefore records the session it was opened for and
 * the shell renders nothing when the active session differs.
 *
 * Only presentation lives here. Run state belongs to the gateway and is polled by
 * the panel's hooks; nothing here caches or advances a run.
 */
import { create } from 'zustand';

export type AgentComputeView = 'draft' | 'detail';

/** Panel width bounds, in pixels. 440 is the design width; 720 leaves the chat usable. */
export const PANEL_MIN_WIDTH = 380;
export const PANEL_MAX_WIDTH = 720;
export const PANEL_DEFAULT_WIDTH = 440;

interface AgentComputePanelState {
  /** The conversation this panel was opened for; undefined when closed. */
  sessionId?: string;
  view: AgentComputeView;
  /** Which run the detail view addresses; unset means "no run selected yet". */
  runId?: string;
  open: (sessionId: string) => void;
  /** Open straight onto one run's detail, as 查看计算任务详情 does. */
  openDetail: (sessionId: string, runId: string) => void;
  /** Open the run list without preselecting one; `runId` selects if given. */
  showDetail: (sessionId: string, runId?: string) => void;
  showDraft: (sessionId: string) => void;
  selectRun: (runId: string) => void;
  /** Collapse the expanded run while staying on the list. */
  clearRun: () => void;
  /** User-adjusted panel width; kept across open/close so a chosen size sticks. */
  width: number;
  setWidth: (width: number) => void;
  close: () => void;
  reset: () => void;
}

/** Everything a close clears. Width is deliberately absent: a chosen size sticks. */
const closed = {
  sessionId: undefined,
  view: 'draft' as AgentComputeView,
  runId: undefined,
};

export const useAgentComputePanelStore = create<AgentComputePanelState>((set) => ({
  ...closed,
  width: PANEL_DEFAULT_WIDTH,
  open: (sessionId) => set({ sessionId, view: 'draft', runId: undefined }),
  openDetail: (sessionId, runId) => set({ sessionId, view: 'detail', runId }),
  showDetail: (sessionId, runId) => set({ sessionId, view: 'detail', runId }),
  showDraft: (sessionId) => set({ sessionId, view: 'draft', runId: undefined }),
  selectRun: (runId) => set({ view: 'detail', runId }),
  clearRun: () => set({ runId: undefined }),
  setWidth: (width) =>
    set({ width: Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, Math.round(width))) }),
  close: () => set({ ...closed }),
  // reset is for tests and teardown, so it also restores the size.
  reset: () => set({ ...closed, width: PANEL_DEFAULT_WIDTH }),
}));

/** Whether the panel should render for this conversation. */
export function isPanelOpenFor(sessionId: string | undefined, activeSessionId: string | undefined): boolean {
  return Boolean(sessionId) && sessionId === activeSessionId;
}