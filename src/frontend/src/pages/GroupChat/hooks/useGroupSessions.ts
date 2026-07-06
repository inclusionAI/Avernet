/**
 * useGroupSessions - 群内会话管理业务逻辑 Hook
 *
 * 遵循三层架构：Hook层封装业务逻辑、API调用、错误处理和Toast
 * 管理协作群内的多会话（Session）CRUD和成员操作
 */

import type {
  CreateSessionParams,
  GroupSession,
} from '@/pages/GroupChat/types';
import { transformMessageData } from '@/pages/GroupChat/utils/transformMessageData';
import * as BcnController from '@/services/backend-api/BcnController';
import { useGroupSessionStore } from '@/stores/groupSessionStore';
import { extractErrorMessage } from '@/utils/requestErrorHandler';
import { useCallback } from 'react';
import { toast } from 'sonner';

const SESSION_PAGE_SIZE = 20;

/** 转换 API 响应为前端 GroupSession 格式 */
function transformSessionData(
  session: BcnController.SessionInfoResponse,
): GroupSession {
  return {
    sessionId: session.session_id,
    sessionKind:
      (session.session_kind as GroupSession['sessionKind']) || 'chat',
    sessionTitle: session.session_title || '新会话',
    groupId: session.group_id,
    status: (session.status as GroupSession['status']) || 'running',
    members: (session.participants || []).map((p) => ({
      actorId: p.actor_id || p.bot_uuid || '',
      actorKind: p.actor_kind || (p.type === 'bot' ? 'bot' : 'human'),
      name: p.bot_name,
      mode: p.mode,
      role: p.role,
      type: p.type,
    })),
    createdAt: session.created_at,
    updatedAt: session.updated_at,
    createdBy: session.created_by,
  };
}

export function useGroupSessions() {
  const {
    // State
    sessions,
    activeSessionId,
    isLoadingSessions,
    hasMoreSessions,
    sessionsTotal,
    currentSession,
    sessionMessages,
    isLoadingSessionMessages,
    isCreatingSession,
    isUpdatingSessionTitle,
    isJoiningSession,
    isLeavingSession,
    // Actions
    setSessions,
    appendSessions,
    addSession,
    updateSession,
    removeSession,
    setActiveSessionId,
    setHasMoreSessions,
    setSessionsTotal,
    setCurrentSession,
    setSessionMessages,
    prependSessionMessages,
    // Loading setters
    setLoadingSessions,
    setLoadingSessionMessages,
    setCreatingSession,
    setUpdatingSessionTitle,
    setJoiningSession,
    setLeavingSession,
    // Reset
    reset,
    resetSessionMessages,
  } = useGroupSessionStore();

  /**
   * 加载群组的会话列表
   */
  const loadSessions = useCallback(
    async (groupId: string, offset = 0, participant?: string) => {
      try {
        setLoadingSessions(true);
        const response = await BcnController.getGroupSessions({
          group_id: groupId,
          limit: SESSION_PAGE_SIZE,
          offset,
          participant,
        });

        const rawSessions = response.items || response.sessions || [];
        const transformed = rawSessions.map(transformSessionData);
        if (offset === 0) {
          setSessions(transformed);
        } else {
          appendSessions(transformed);
        }
        setHasMoreSessions(transformed.length >= SESSION_PAGE_SIZE);
        // 优先使用接口返回的 total，否则取 store 中已累加的 sessions 长度
        setSessionsTotal(
          response.total ?? useGroupSessionStore.getState().sessions.length,
        );
      } catch (error: any) {
        console.error('[useGroupSessions] Failed to load sessions:', error);
        const msg = extractErrorMessage(error, '加载会话列表失败');
        toast.error(msg);
      } finally {
        setLoadingSessions(false);
      }
    },
    [
      setSessions,
      appendSessions,
      setHasMoreSessions,
      setSessionsTotal,
      setLoadingSessions,
    ],
  );

  /**
   * 加载更多会话（分页）
   */
  const loadMoreSessions = useCallback(
    async (groupId: string, participant?: string) => {
      if (isLoadingSessions || !hasMoreSessions) return;
      await loadSessions(groupId, sessions.length, participant);
    },
    [isLoadingSessions, hasMoreSessions, sessions.length, loadSessions],
  );

  /**
   * 搜索会话（按名称）
   */
  const searchSessions = useCallback(
    async (groupId: string, query: string, participant?: string) => {
      if (!query.trim()) {
        return loadSessions(groupId, 0, participant);
      }
      try {
        setLoadingSessions(true);
        const response = await BcnController.getGroupSessions({
          group_id: groupId,
          q: query.trim(), // 按名称搜索
          limit: SESSION_PAGE_SIZE,
          offset: 0,
          participant,
        });
        const rawSessions = response.items || response.sessions || [];
        const transformed = rawSessions.map(transformSessionData);
        setSessions(transformed);
        setHasMoreSessions(false);
        setSessionsTotal(response.total ?? transformed.length);
      } catch (error: any) {
        console.error('[useGroupSessions] Failed to search sessions:', error);
        const msg = extractErrorMessage(error, '搜索会话失败');
        toast.error(msg);
      } finally {
        setLoadingSessions(false);
      }
    },
    [setSessions, setHasMoreSessions, setSessionsTotal, setLoadingSessions],
  );

  /**
   * 选中会话，加载详情和消息
   * @param sessionId 会话 ID
   * @param options.addToSessionsList 如果会话不在列表中，是否自动添加到列表头部（用于视角切换后确保会话可见）
   */
  const selectSession = useCallback(
    async (
      sessionId: string,
      options?: { addToSessionsList?: boolean; viewBotId?: string },
    ) => {
      setActiveSessionId(sessionId);
      resetSessionMessages();
      setLoadingSessionMessages(true);
      try {
        const [detailResponse] = await Promise.all([
          BcnController.getSessionDetail({ session_id: sessionId }),
          BcnController.getSessionMessages({
            session_id: sessionId,
            view_bot_id: options?.viewBotId,
          })
            .then((msgResponse) => {
              // 防止竞态：响应回来时若已切换到其它会话，丢弃本次结果
              if (
                useGroupSessionStore.getState().activeSessionId !== sessionId
              ) {
                return;
              }
              const messages = transformMessageData(msgResponse || []);
              setSessionMessages(messages);
            })
            .catch((error: any) => {
              if (
                useGroupSessionStore.getState().activeSessionId !== sessionId
              ) {
                return;
              }
              console.error(
                '[useGroupSessions] Failed to load session messages:',
                error,
              );
              const msg = extractErrorMessage(error, '加载会话消息失败');
              toast.error(msg);
            }),
        ]);
        // 防止竞态：详情返回时若已切换会话，丢弃
        if (useGroupSessionStore.getState().activeSessionId !== sessionId) {
          return;
        }
        const session = transformSessionData(detailResponse);
        setCurrentSession(session);

        // 如果会话不在列表中，自动添加到列表头部
        if (options?.addToSessionsList) {
          const existing = useGroupSessionStore
            .getState()
            .sessions.find((s) => s.sessionId === sessionId);
          if (!existing) {
            addSession(session);
          }
        }
      } catch (error: any) {
        if (useGroupSessionStore.getState().activeSessionId !== sessionId) {
          return;
        }
        console.error(
          '[useGroupSessions] Failed to load session detail:',
          error,
        );
      } finally {
        if (useGroupSessionStore.getState().activeSessionId === sessionId) {
          setLoadingSessionMessages(false);
        }
      }
    },
    [
      setActiveSessionId,
      setCurrentSession,
      setSessionMessages,
      resetSessionMessages,
      addSession,
      setLoadingSessionMessages,
    ],
  );

  /**
   * 创建新会话
   */
  const createSession = useCallback(
    async (
      groupId: string,
      params: CreateSessionParams,
      options?: { viewBotId?: string },
    ) => {
      try {
        setCreatingSession(true);
        const response = await BcnController.createGroupSession({
          group_id: groupId,
          session_kind: params.session_kind,
          session_title: params.session_title,
          created_by: params.created_by,
          input: params.input,
        });

        const newSession: GroupSession = {
          sessionId: response.session_id,
          sessionKind:
            (response.session_kind as GroupSession['sessionKind']) || 'chat',
          sessionTitle: response.session_title || '新会话',
          groupId: response.group_id || groupId,
          status: (response.status as GroupSession['status']) || 'running',
          members: [],
          createdAt: response.created_at || Date.now(),
          updatedAt: response.created_at || Date.now(),
          createdBy: params.created_by,
        };

        addSession(newSession);
        // 通过 selectSession 完整切换：重置旧消息 + 加载新会话详情和消息
        await selectSession(newSession.sessionId, {
          viewBotId: options?.viewBotId,
        });
        toast.success('会话创建成功');
        return newSession;
      } catch (error: any) {
        console.error('[useGroupSessions] Failed to create session:', error);
        const msg = extractErrorMessage(error, '创建会话失败');
        toast.error(msg);
        return null;
      } finally {
        setCreatingSession(false);
      }
    },
    [addSession, selectSession, setCreatingSession],
  );

  /**
   * 加入当前会话
   * @param actorKind 可选，默认 'human'（向后兼容）；新调用方应显式传入以支持 Bot 加入场景
   */
  const joinSession = useCallback(
    async (
      sessionId: string,
      actorId: string,
      actorKind: 'bot' | 'human' = 'human',
    ) => {
      try {
        setJoiningSession(true);
        await BcnController.updateSessionMember({
          session_id: sessionId,
          actor_id: actorId,
          mode: 'present',
        });
        // 更新当前会话的成员列表
        if (currentSession?.sessionId === sessionId) {
          const existingMember = currentSession.members.find(
            (m) => m.actorId === actorId,
          );
          if (existingMember) {
            updateSession(sessionId, {
              members: currentSession.members.map((m) =>
                m.actorId === actorId
                  ? { ...m, actorKind, mode: 'present' as const }
                  : m,
              ),
            });
          } else {
            updateSession(sessionId, {
              members: [
                ...currentSession.members,
                { actorId, actorKind, mode: 'present' as const },
              ],
            });
          }
        }
        toast.success('已加入当前会话');
        return true;
      } catch (error: any) {
        console.error('[useGroupSessions] Failed to join session:', error);
        const msg = extractErrorMessage(error, '加入会话失败');
        toast.error(msg);
        return false;
      } finally {
        setJoiningSession(false);
      }
    },
    [currentSession, updateSession, setJoiningSession],
  );

  /**
   * 加载会话消息
   */
  const loadSessionMessages = useCallback(
    async (sessionId: string, botId?: string, cursor?: string) => {
      try {
        setLoadingSessionMessages(true);
        const response = await BcnController.getSessionMessages({
          session_id: sessionId,
          cursor: cursor || undefined,
          view_bot_id: botId || undefined,
        });

        // 防止竞态：响应回来时若已切换到其它会话，丢弃本次结果
        if (useGroupSessionStore.getState().activeSessionId !== sessionId) {
          return;
        }

        const messages = transformMessageData(response || []);
        if (cursor) {
          prependSessionMessages(messages);
        } else {
          setSessionMessages(messages);
        }
      } catch (error: any) {
        if (useGroupSessionStore.getState().activeSessionId !== sessionId) {
          return;
        }
        console.error(
          '[useGroupSessions] Failed to load session messages:',
          error,
        );
        const msg = extractErrorMessage(error, '加载会话消息失败');
        toast.error(msg);
      } finally {
        if (useGroupSessionStore.getState().activeSessionId === sessionId) {
          setLoadingSessionMessages(false);
        }
      }
    },
    [setSessionMessages, prependSessionMessages, setLoadingSessionMessages],
  );

  /**
   * 退出当前会话
   */
  const leaveSession = useCallback(
    async (sessionId: string, actorId: string) => {
      try {
        setLeavingSession(true);
        await BcnController.updateSessionMember({
          session_id: sessionId,
          actor_id: actorId,
          mode: 'absent',
        });
        // 更新当前会话的成员列表
        if (currentSession?.sessionId === sessionId) {
          updateSession(sessionId, {
            members: currentSession.members.map((m) =>
              m.actorId === actorId ? { ...m, mode: 'absent' as const } : m,
            ),
          });
        }
        toast.success('已退出当前会话');
        return true;
      } catch (error: any) {
        console.error('[useGroupSessions] Failed to leave session:', error);
        const msg = extractErrorMessage(error, '退出会话失败');
        toast.error(msg);
        return false;
      } finally {
        setLeavingSession(false);
      }
    },
    [currentSession, updateSession, setLeavingSession],
  );

  /**
   * 删除会话（仅 creator 可操作）
   */
  const deleteSession = useCallback(
    async (sessionId: string, botId: string): Promise<boolean> => {
      try {
        await BcnController.deleteSession({
          session_id: sessionId,
          bot_id: botId,
        });
        removeSession(sessionId);
        toast.success('会话已删除');
        return true;
      } catch (error: any) {
        console.error('[useGroupSessions] Failed to delete session:', error);
        toast.error(extractErrorMessage(error, '删除会话失败'));
        return false;
      }
    },
    [removeSession],
  );

  /**
   * 更新会话成员模式（Bot 禁言/自动）
   */
  const updateSessionMemberMode = useCallback(
    async (
      sessionId: string,
      actorId: string,
      mode: 'auto' | 'muted',
    ): Promise<boolean> => {
      try {
        await BcnController.updateSessionMember({
          session_id: sessionId,
          actor_id: actorId,
          mode,
        });
        // 乐观更新当前会话的成员模式
        if (currentSession?.sessionId === sessionId) {
          updateSession(sessionId, {
            members: currentSession.members.map((m) =>
              m.actorId === actorId ? { ...m, mode } : m,
            ),
          });
        }
        toast.success(mode === 'muted' ? '已禁言' : '已切换为自动模式');
        return true;
      } catch (error: any) {
        console.error(
          '[useGroupSessions] Failed to update session member mode:',
          error,
        );
        const msg = extractErrorMessage(error, '更新模式失败');
        toast.error(msg);
        return false;
      }
    },
    [currentSession, updateSession],
  );

  /**
   * 刷新当前会话详情（仅更新会话数据，不重置消息）
   */
  const refreshCurrentSession = useCallback(
    async (sessionId: string) => {
      try {
        const detailResponse = await BcnController.getSessionDetail({
          session_id: sessionId,
        });
        const session = transformSessionData(detailResponse);
        setCurrentSession(session);
      } catch (error: any) {
        console.error(
          '[useGroupSessions] Failed to refresh session detail:',
          error,
        );
      }
    },
    [setCurrentSession],
  );

  /**
   * 更新会话标题
   */
  const updateSessionTitle = useCallback(
    async (sessionId: string, title: string | null) => {
      try {
        setUpdatingSessionTitle(true);
        await BcnController.updateSessionTitle({
          session_id: sessionId,
          session_title: title,
        });
        updateSession(sessionId, { sessionTitle: title ?? '' });
        toast.success(title === null ? '会话标题已清空' : '会话标题已更新');
        return true;
      } catch (error: any) {
        console.error(
          '[useGroupSessions] Failed to update session title:',
          error,
        );
        const msg = extractErrorMessage(error, '更新标题失败');
        toast.error(msg);
        return false;
      } finally {
        setUpdatingSessionTitle(false);
      }
    },
    [updateSession, setUpdatingSessionTitle],
  );

  /**
   * 清空当前会话
   */
  const clearCurrentSession = useCallback(() => {
    setActiveSessionId('');
    setCurrentSession(null);
    resetSessionMessages();
  }, [setActiveSessionId, setCurrentSession, resetSessionMessages]);

  /**
   * 判断用户是否已加入当前会话
   */
  const isUserInSession = useCallback(
    (userId: string | undefined): boolean => {
      if (!userId || !currentSession) return false;
      const humanActorId = `human_${userId}`;
      const member = currentSession.members.find(
        (m) => m.actorId === humanActorId,
      );
      return member?.mode === 'present';
    },
    [currentSession],
  );

  return {
    // State
    sessions,
    activeSessionId,
    isLoadingSessions,
    hasMoreSessions,
    sessionsTotal,
    currentSession,
    sessionMessages,
    isLoadingSessionMessages,
    isCreatingSession,
    isUpdatingSessionTitle,
    isJoiningSession,
    isLeavingSession,
    // Actions
    loadSessions,
    loadMoreSessions,
    searchSessions,
    createSession,
    selectSession,
    loadSessionMessages,
    joinSession,
    leaveSession,
    deleteSession,
    updateSessionMemberMode,
    updateSessionTitle,
    refreshCurrentSession,
    clearCurrentSession,
    isUserInSession,
    // Reset
    resetSessions: reset,
  };
}
