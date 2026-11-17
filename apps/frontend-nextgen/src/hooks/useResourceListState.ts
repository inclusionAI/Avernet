import type { BotEditorResource } from '@/domain/botEditor';
import { useEffect, useRef, useState } from 'react';

export function useResourceListState(botId: string | null, ownerId?: string) {
  const [resources, setResources] = useState<BotEditorResource[]>([]);
  const [resourceLoadingPaths, setResourceLoadingPaths] = useState<string[]>([]);
  const [resourceErrors, setResourceErrors] = useState<Record<string, string>>({});
  const [resourceLoadedPaths, setResourceLoadedPaths] = useState<string[]>([]);
  const resourceIdentity = `${botId ?? ''}:${ownerId ?? ''}`;
  const resourceRequests = useRef({ identity: resourceIdentity, sequence: 0, versions: {} as Record<string, number> });
  if (resourceRequests.current.identity !== resourceIdentity) {
    resourceRequests.current = { identity: resourceIdentity, sequence: resourceRequests.current.sequence + 1, versions: {} };
  }
  const startResourceRequest = (path: string) => {
    const current = resourceRequests.current;
    if (current.identity !== resourceIdentity) return { identity: resourceIdentity, path, version: -1 };
    const version = ++current.sequence;
    current.versions[path] = version;
    setResourceLoadingPaths((paths) => [...new Set([...paths, path])]);
    return { identity: current.identity, path, version };
  };
  const isCurrentResourceRequest = (ticket: { identity: string; path: string; version: number }) =>
    resourceRequests.current.identity === ticket.identity && resourceRequests.current.versions[ticket.path] === ticket.version;
  const finishResourceRequest = (
    ticket: { identity: string; path: string; version: number },
    items?: BotEditorResource[],
    error?: unknown,
  ) => {
    if (!isCurrentResourceRequest(ticket)) return false;
    const { path } = ticket;
    if (items) {
      // A refreshed parent invalidates all in-flight descendants and cached children.
      const requests = resourceRequests.current;
      Object.keys(requests.versions).forEach((child) => {
        if (child !== path && (!path || child.startsWith(`${path}/`))) delete requests.versions[child];
      });
      setResources((current) => path ? [...current.filter((item) => item.parentPath !== path && !item.parentPath.startsWith(`${path}/`)), ...items] : items);
      setResourceLoadedPaths((current) => [...current.filter((child) => child !== path && (path ? !child.startsWith(`${path}/`) : false)), path]);
      setResourceErrors((current) => Object.fromEntries(Object.entries(current).filter(([child]) => child !== path && (path ? !child.startsWith(`${path}/`) : false))));
      setResourceLoadingPaths((current) => current.filter((child) => child !== path && (path ? !child.startsWith(`${path}/`) : false)));
    } else {
      Object.keys(resourceRequests.current.versions).forEach((child) => {
        if (child !== path && (!path || child.startsWith(`${path}/`))) delete resourceRequests.current.versions[child];
      });
      setResourceLoadedPaths((current) => current.filter((child) => child !== path && (path ? !child.startsWith(`${path}/`) : false)));
      const data = error && typeof error === 'object' && 'data' in error ? error.data : undefined;
      const offline = error && typeof error === 'object' && 'status' in error && error.status === 503 && data && typeof data === 'object' && 'message' in data && data.message === 'Bot device is offline';
      setResourceErrors((current) => ({
        ...Object.fromEntries(Object.entries(current).filter(([child]) => child !== path && (path ? !child.startsWith(`${path}/`) : false))),
        [path]: offline ? 'Bot 暂时离线，请稍后重试' : '资源加载失败，请重试',
      }));
      setResourceLoadingPaths((current) => current.filter((child) => child !== path && (path ? !child.startsWith(`${path}/`) : false)));
    }
    return true;
  };
  useEffect(() => {
    setResources([]);
    setResourceErrors({});
    setResourceLoadedPaths([]);
    setResourceLoadingPaths([]);
  }, [resourceIdentity]);
  return { resources, resourceLoadingPaths, resourceErrors, resourceLoadedPaths, startResourceRequest, finishResourceRequest };
}
