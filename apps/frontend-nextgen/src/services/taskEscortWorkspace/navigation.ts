import type { PageId, Period, TaskEscortWorkspaceMode } from '@/domain/taskEscortWorkspace';
import { readRoute, readWorkspaceMode, routeSearch } from '@/domain/taskEscortWorkspace';

export interface TaskEscortRoute {
  page: PageId;
  period: Period;
  mode: TaskEscortWorkspaceMode;
}
export function readTaskEscortRoute(search: string): TaskEscortRoute {
  return { ...readRoute(search), mode: readWorkspaceMode(search) };
}
export function taskEscortSearch(search: string, route: TaskEscortRoute): string {
  // The legacy mode field is accepted for compatibility, never serialized.
  return routeSearch(search, route);
}
