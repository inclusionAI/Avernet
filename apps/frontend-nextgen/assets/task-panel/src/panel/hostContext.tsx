import { createContext, useContext } from 'react';
import type { TaskPanelHost } from '../contracts';
export const TaskPanelHostContext = createContext<TaskPanelHost | null>(null);
export const useTaskPanelHost = () => useContext(TaskPanelHostContext);
/** Standalone use keeps existing cookie transport; authenticated products inject their own transport. */
export async function requestPanelJson(path: string, signal?: AbortSignal, host?: TaskPanelHost | null): Promise<unknown> {
  if (host) return host.transport.requestJson(path, signal ?? new AbortController().signal);
  const response = await fetch(path, { credentials: 'include', signal });
  if (!response.ok) throw new Error(`请求失败（${response.status}）`);
  return response.json();
}
