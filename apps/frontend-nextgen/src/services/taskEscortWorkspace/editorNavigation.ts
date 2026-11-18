import { readTaskEscortRoute } from './navigation';

interface EditorLocation {
  pathname: string;
  search: string;
  hash: string;
}
/** Only the period may change; path/hash and all non-period editor query context must remain identical. */
export function isEditorPeriodOnlyChange(current: EditorLocation, next: EditorLocation) {
  if (current.pathname !== next.pathname || current.hash !== next.hash) return false;
  const currentRoute = readTaskEscortRoute(current.search),
    nextRoute = readTaskEscortRoute(next.search);
  if (currentRoute.page !== 'editor' || nextRoute.page !== 'editor') return false;
  const currentParams = new URLSearchParams(current.search),
    nextParams = new URLSearchParams(next.search);
  if (JSON.stringify(currentParams.getAll('period')) === JSON.stringify(nextParams.getAll('period'))) return false;
  currentParams.delete('period');
  nextParams.delete('period');
  currentParams.sort();
  nextParams.sort();
  return currentParams.toString() === nextParams.toString();
}
