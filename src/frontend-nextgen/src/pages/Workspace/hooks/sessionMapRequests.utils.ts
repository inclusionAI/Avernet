import type { GroupSessionPage, SessionView } from '@/domain/collaboration';
import type { DomainError } from '@/services/workspace/identityService';
import { shouldMuteNonAuthedToast } from '@/utils/loginToastGate';
import { type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { toast } from 'sonner';

export const SESSION_PAGE_SIZE = 10;

export interface SessionPageMeta {
  total: number;
  hasMore: boolean;
  /** 后端分页游标，避免本地新建/删除导致 offset 与服务端列表错位。 */
  nextOffset: number;
  isLoadingMore: boolean;
}

export type SessionMap = Record<string, SessionView[]>;
export type SessionMapRef = MutableRefObject<SessionMap>;
export type SessionMetaMapRef = MutableRefObject<Record<string, SessionPageMeta>>;

export interface UseSessionMapRequestsOptions {
  groupId: string | null;
  expandedGroupIds: string[];
  activeIdentityId: string | null;
  rawByGroupId: SessionMap;
  rawByGroupIdRef: SessionMapRef;
  pageMetaByGroupIdRef: SessionMetaMapRef;
  inFlightRef: MutableRefObject<Map<string, number>>;
  loadingMoreRef: MutableRefObject<Set<string>>;
  requestVersionRef: MutableRefObject<Map<string, number>>;
  identityEpochRef: MutableRefObject<number>;
  setIsLoading: Dispatch<SetStateAction<boolean>>;
  setRawByGroupId: Dispatch<SetStateAction<SessionMap>>;
  setPageMetaByGroupId: Dispatch<SetStateAction<Record<string, SessionPageMeta>>>;
  setErrorByGroupId: Dispatch<SetStateAction<Record<string, string>>>;
  setLoadMoreErrorByGroupId: Dispatch<SetStateAction<Record<string, string>>>;
  beginGroupRequest: (groupId: string) => number;
  isCurrentRequest: (groupId: string, version: number, epoch: number) => boolean;
  replaceGroupPage: (groupId: string, data: GroupSessionPage | SessionView[]) => void;
}

export function notifyError(err: DomainError): void {
  // 未登录（oauth-provider + 非 authenticated）静默：会话失效后的 sessions 加载失败 toast
  // 统一由 ExternalLoginPromptModal 承担（见 loginToastGate）；已登录 / ace-gateway 照常提示。
  if (shouldMuteNonAuthedToast()) return;
  toast.error(err.friendlyMessage);
}

export function normalizePage(data: GroupSessionPage | SessionView[], fallbackOffset = 0): GroupSessionPage {
  if (Array.isArray(data)) {
    return {
      items: data,
      offset: fallbackOffset,
      limit: SESSION_PAGE_SIZE,
      total: fallbackOffset + data.length,
      hasMore: data.length >= SESSION_PAGE_SIZE,
    };
  }
  return data;
}
