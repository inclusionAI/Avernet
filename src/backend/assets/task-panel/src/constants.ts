/**
 * Task workflow panel — geometry, polling, status-set constants.
 * Geometry mirrors bcsPanel.StateMachineRunView (NODE_WIDTH/HEIGHT/LEVEL_GAP/COLUMN_GAP).
 */

// --- layout geometry (SVG units) ---
export const NODE_WIDTH = 188;
export const NODE_HEIGHT = 58;
export const LEVEL_GAP = 56;
export const COLUMN_GAP = 18;
export const PADDING = 24;

// --- polling (mirrors StateMachineRunView DEFAULT_POLLING_INTERVAL) ---
export const DEFAULT_POLLING_INTERVAL = 3000;
export const MAX_TRANSIENT_RETRIES = 3;
export const MAX_BACKOFF = DEFAULT_POLLING_INTERVAL * 10; // 30s cap

// --- task root_phase sets ---
export const ROOT_PHASE_TERMINAL: ReadonlySet<string> = new Set([
  'done',
  'cancelled',
  'failed',
]);
export const ROOT_PHASE_ACTIVE: ReadonlySet<string> = new Set([
  'drafting',
  'defined',
  'executing',
  'reviewing',
]);

// --- node status sets ---
export const NODE_STATUS_TERMINAL: ReadonlySet<string> = new Set([
  'done',
  'failed',
  'skipped',
]);
export const NODE_STATUS_ACTIVE: ReadonlySet<string> = new Set([
  'running',
  'human_required',
  'pending',
]);

// --- edge render order (lower renders first, under higher) ---
export const EDGE_RENDER_ORDER: Record<string, number> = {
  executed: 0,
  pending: 1,
  blocked: 2,
  skipped: 3,
};
