const STORAGE_KEY = 'avernet:task-evaluation:auto-executed';
const MAX_RECORDED_IDS = 100;

function readClaimedIds(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const value = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '[]');
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
  } catch {
    return [];
  }
}

/**
 * Claims one evaluation card before dispatching execute.
 *
 * Evaluation cards remain in chat history, so mount effects may run again after navigation or refresh.
 * The Skill supplies a unique id per card; persisting the bounded claim prevents duplicate task creation.
 */
export function claimEvaluationAutoExecute(id: string): boolean {
  const normalized = id.trim();
  if (!normalized || typeof window === 'undefined') return false;

  const claimed = readClaimedIds();
  if (claimed.includes(normalized)) return false;

  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([...claimed.slice(-(MAX_RECORDED_IDS - 1)), normalized]),
    );
    return true;
  } catch {
    // If storage is unavailable, the mounted card still gets one in-memory attempt via its ref guard.
    return true;
  }
}

export function clearEvaluationAutoExecuteClaimsForTest(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(STORAGE_KEY);
}
