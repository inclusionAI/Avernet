import type { SessionView } from '@/domain/collaboration';
import type { SessionParticipantMode } from '@/services/backendApi/collaboration/sessionController';
import {
  addSessionParticipant,
  collectSession as collectSessionApi,
  createSession,
  deleteSession,
  deleteSessionParticipant,
  getSession,
  listGroupSessions,
  uncollectSession as uncollectSessionApi,
  updateSession,
  updateSessionMemberMode,
} from '@/services/backendApi/collaboration/sessionController';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { DomainError, DomainResult } from './identityService';
import { mapBcsSessionItem, mapSessionListItem, type BcsSessionRaw } from './mappers';

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

const FAVORITES_STORAGE_PREFIX = 'teamclaw:collab:favorites:';

function getFavoriteStorageKey(identityId: string): string {
  return `${FAVORITES_STORAGE_PREFIX}${identityId}`;
}

function readFavorites(identityId: string): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(getFavoriteStorageKey(identityId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : [];
  } catch {
    return [];
  }
}

function writeFavorites(identityId: string, favorites: string[]): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(getFavoriteStorageKey(identityId), JSON.stringify(favorites));
  } catch {
    // ignore storage failures in non-browser or quota constrained environments
  }
}

export interface VisibleSessionsOpts {
  tab: 'all' | 'favorite';
  search: string;
  favorites: string[];
}

export const sessionService = {
  async createNewSession(groupId: string, title?: string, contextQuery?: string): Promise<DomainResult<SessionView>> {
    try {
      const resp = await createSession(groupId, {
        title: title || undefined,
        input: contextQuery ? { query: contextQuery } : undefined,
      });
      return { ok: true, data: mapSessionListItem(resp.data!) };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) {
        return { ok: false, error: toDomainError('SESSION_CONFLICT', '会话状态已变更，请刷新后重试。') };
      }
      return { ok: false, error: toDomainError('SESSION_CREATE_FAILED', '创建会话失败，请稍后重试。') };
    }
  },

  async renameSession(sessionId: string, title: string): Promise<DomainResult<null>> {
    try {
      await updateSession(sessionId, { title });
      return { ok: true, data: null };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) {
        return { ok: false, error: toDomainError('SESSION_CONFLICT', '会话状态已变更，请刷新后重试。') };
      }
      return { ok: false, error: toDomainError('SESSION_RENAME_FAILED', '重命名会话失败，请稍后重试。') };
    }
  },

  async deleteSession(sessionId: string): Promise<DomainResult<null>> {
    try {
      await deleteSession(sessionId);
      return { ok: true, data: null };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) {
        return { ok: false, error: toDomainError('SESSION_CONFLICT', '会话状态已变更，请刷新后重试。') };
      }
      return { ok: false, error: toDomainError('SESSION_DELETE_FAILED', '删除会话失败，请稍后重试。') };
    }
  },

  async loadSessionsByIds(groupId: string, offset: number): Promise<SessionView[]> {
    const resp = await listGroupSessions(groupId, { offset, limit: 50 });
    return (resp.data?.items ?? []).map((s) => mapSessionListItem(s));
  },

  /** execute 建群返回的群会话列表：走预发 OpenAPI，映射复用 mappers.mapBcsSessionItem。 */
  async loadBcsSessions(groupId: string, offset = 0): Promise<SessionView[]> {
    const resp = await fetch(
      `/openapi/v1/collaboration/groups/${encodeURIComponent(groupId)}/sessions?offset=${offset}&limit=50`,
      {
        credentials: 'include',
      },
    );
    if (!resp.ok) return [];
    const json = await resp.json().catch(() => null);
    const data = json?.data ?? json;
    const items = Array.isArray(data) ? data : data?.items ?? [];
    return (items as BcsSessionRaw[]).map((s) => mapBcsSessionItem(s, groupId));
  },

  /** 选中群会话统一入口：本地 BCS 群走 loadBcsSessions，预发群走 loadSessionsByIds。判断收敛于此。 */
  async loadSessionsByIdsOrBcs(groupId: string, offset: number): Promise<SessionView[]> {
    return useWorkspaceStore.getState().bcsGroupIds[groupId]
      ? this.loadBcsSessions(groupId, offset)
      : this.loadSessionsByIds(groupId, offset);
  },

  /**
   * 拉取会话详情（含 participants/mode）；
   * 列表接口只返回 participant_count，需经此补齐协作面板所需状态。
   */
  async getSessionDetail(sessionId: string): Promise<DomainResult<SessionView>> {
    try {
      const resp = await getSession(sessionId);
      return { ok: true, data: mapSessionListItem(resp.data!) };
    } catch {
      return {
        ok: false,
        error: toDomainError('SESSION_DETAIL_FAILED', '加载会话详情失败，请稍后重试。'),
      };
    }
  },

  /** 更新会话成员姿态/发言模式（Bot auto↔muted；Human present↔absent），返回刷新后的会话。 */
  async updateMemberMode(
    sessionId: string,
    actorId: string,
    mode: SessionParticipantMode,
  ): Promise<DomainResult<SessionView>> {
    try {
      await updateSessionMemberMode(sessionId, actorId, { mode });
      return this.getSessionDetail(sessionId);
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) {
        return { ok: false, error: toDomainError('SESSION_CONFLICT', '会话状态已变更，请刷新后重试。') };
      }
      return { ok: false, error: toDomainError('SESSION_MEMBER_MODE_FAILED', '更新会话成员状态失败，请稍后重试。') };
    }
  },

  /** 新增会话成员；返回刷新后的会话详情。 */
  async addMember(sessionId: string, actorId: string): Promise<DomainResult<SessionView>> {
    try {
      await addSessionParticipant(sessionId, actorId);
      return this.getSessionDetail(sessionId);
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) {
        return { ok: false, error: toDomainError('SESSION_CONFLICT', '会话状态已变更，请刷新后重试。') };
      }
      return { ok: false, error: toDomainError('SESSION_MEMBER_ADD_FAILED', '添加会话成员失败，请稍后重试。') };
    }
  },

  /** 移除会话成员；返回刷新后的会话详情。 */
  async removeMember(sessionId: string, actorId: string): Promise<DomainResult<SessionView>> {
    try {
      await deleteSessionParticipant(sessionId, actorId);
      return this.getSessionDetail(sessionId);
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) {
        return { ok: false, error: toDomainError('SESSION_CONFLICT', '会话状态已变更，请刷新后重试。') };
      }
      return { ok: false, error: toDomainError('SESSION_MEMBER_REMOVE_FAILED', '移除会话成员失败，请稍后重试。') };
    }
  },

  /** 退出会话：从当前身份视角移除自己。 */
  async leaveSession(sessionId: string, actorId: string): Promise<DomainResult<SessionView>> {
    return this.removeMember(sessionId, actorId);
  },

  /** 收藏/取消收藏会话：调用后端接口，返回最终收藏状态（collected 字段）。 */
  async setFavorite(identityId: string, sessionId: string, favorite: boolean): Promise<DomainResult<boolean>> {
    try {
      const resp = favorite
        ? await collectSessionApi(sessionId, { participant: identityId })
        : await uncollectSessionApi(sessionId, { participant: identityId });
      const collected = resp.data?.collected ?? favorite;
      const nextFavorites = readFavorites(identityId);
      const updated = collected
        ? Array.from(new Set([...nextFavorites, sessionId]))
        : nextFavorites.filter((id) => id !== sessionId);
      writeFavorites(identityId, updated);
      return { ok: true, data: collected };
    } catch {
      return {
        ok: false,
        error: toDomainError(
          favorite ? 'SESSION_COLLECT_FAILED' : 'SESSION_UNCOLLECT_FAILED',
          favorite ? '收藏会话失败，请稍后重试。' : '取消收藏失败，请稍后重试。',
        ),
      };
    }
  },

  listFavorites(identityId: string): string[] {
    return readFavorites(identityId);
  },

  getVisibleSessions(sessions: SessionView[], opts: VisibleSessionsOpts): SessionView[] {
    const normalized = opts.search.trim().toLowerCase();
    return sessions
      .filter((s) => (opts.tab === 'all' ? true : opts.favorites.includes(s.sessionId)))
      .filter(
        (s) =>
          !normalized || s.title.toLowerCase().includes(normalized) || s.sessionId.toLowerCase().includes(normalized),
      )
      .sort((a, b) => b.lastMessageAt - a.lastMessageAt);
  },

  /** 在 groupId → sessions 的多群映射中重命名某条会话；会话可能属于任一已加载群。 */
  renameInMap(map: Record<string, SessionView[]>, sessionId: string, title: string): Record<string, SessionView[]> {
    const next = { ...map };
    for (const gid of Object.keys(next)) {
      if (next[gid].some((s) => s.sessionId === sessionId)) {
        next[gid] = next[gid].map((s) => (s.sessionId === sessionId ? { ...s, title } : s));
      }
    }
    return next;
  },

  /**
   * 在 groupId → sessions 的多群映射中移除某条会话。
   * 返回新映射 + 命中的群与剩余列表（供删除后的选中校正）。
   */
  removeFromMap(
    map: Record<string, SessionView[]>,
    sessionId: string,
  ): { next: Record<string, SessionView[]>; hitGroupId: string | null; remaining: SessionView[] } {
    const next = { ...map };
    let hitGroupId: string | null = null;
    let remaining: SessionView[] = [];
    for (const gid of Object.keys(next)) {
      if (next[gid].some((s) => s.sessionId === sessionId)) {
        remaining = next[gid].filter((s) => s.sessionId !== sessionId);
        next[gid] = remaining;
        hitGroupId = gid;
      }
    }
    return { next, hitGroupId, remaining };
  },
};
