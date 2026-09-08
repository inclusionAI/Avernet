import type { IdentityView, ParticipantMode, ParticipantView, SessionView } from '@/domain/collaboration';
import { isSameHumanIdentity } from '@/domain/userIdentity';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface CollabPanelState {
  /** 是否展示底部协作面板：bot 视角恒显；human 视角仅在 human 姿态为 absent 时显示加入条。 */
  visible: boolean;
  /** human 视角 absent 时直接渲染「未加入当前会话」条（无 tab）。 */
  humanAbsentOnly: boolean;
  /** 当前浏览身份（bot 发言控制对象）。 */
  botActorId: string | null;
  botMode: 'auto' | 'muted' | null;
  botName: string;
  /** 会话内 human 成员（当前查看用户身份对应的协作者）。 */
  human: ParticipantView | null;
  humanJoined: boolean;
  humanName: string;
  humanAvatarUrl?: string;
  /** 是否存在可切换的 human 身份（「去发言」按钮可用性）。 */
  canSwitchToHuman: boolean;
  switchingBotMode: boolean;
  joining: boolean;
  setBotMode: (mode: 'auto' | 'muted') => Promise<void>;
  joinSession: () => Promise<boolean>;
  /** 退出当前会话（将 human mode 置为 absent）。 */
  leaveSession: () => Promise<boolean>;
  /** 切换到用户视角继续发言（对齐 open-claw「去发言」）。 */
  switchToHuman: () => void;
}

/**
 * useCollabPanel —— 「我的协作」协作群会话底部协作面板的状态编排。
 *
 * 参考 open-claw BottomPanel：
 * - bot 视角：Bot控制 tab（bot 发言模式 auto/muted 展示与切换）+ 用户协作 tab（human present/absent）；
 * - human 视角且 human.mode === 'absent'：只显示「未加入当前会话 + 加入」条。
 *
 * mode 变更统一经 sessions.updateMemberMode → PATCH /openapi/v1/collaboration/sessions/{sid}/participants/{actor}。
 */
export function useCollabPanel(
  session: SessionView | null,
  activeIdentity: IdentityView | null,
  updateMemberMode: (sessionId: string, actorId: string, mode: ParticipantMode) => Promise<boolean>,
  authenticatedUserId?: string | null,
  authenticatedUserName?: string | null,
): CollabPanelState {
  const [switchingBotMode, setSwitchingBotMode] = useState(false);
  const [joining, setJoining] = useState(false);

  const isBotViewer = activeIdentity?.kind === 'bot';
  const botActorId = isBotViewer ? activeIdentity?.id ?? null : null;

  const botParticipant = useMemo(() => {
    if (!session || !botActorId) return null;
    return session.participants.find((p) => p.actorId === botActorId || p.actorId === botActorId.split(':')[0]) ?? null;
  }, [session, botActorId]);

  const botMode: 'auto' | 'muted' | null = useMemo(() => {
    const mode = botParticipant?.mode;
    if (mode === 'muted') return 'muted';
    if (mode === 'auto') return 'auto';
    // bot actor 即便不在会话成员里，bot 视角也默认展示 auto 可控态（与 open-claw 缺省一致）。
    return isBotViewer && session ? 'auto' : null;
  }, [botParticipant, isBotViewer, session]);

  const identities = useWorkspaceStore((s) => s.identities);
  const humanIdentity = useMemo(
    () => identities.find((i) => i.kind === 'user' && !i.id.startsWith('test-')) ?? null,
    [identities],
  );
  const humanIdentityId = authenticatedUserId ?? humanIdentity?.id ?? null;
  // 加入/退出会话与「去发言」切换身份用的 actor id：必须优先 store 人类身份 id
  //（= mine 接口 human 条目的 bot_id，形如 human_xxx）。authenticatedUserId 是规范化
  // OpenAPI user_id（无 human_ 前缀），放进 participants/{actor} 路径参数或
  // setActiveIdentity（按身份 id 精确匹配）都会落空。匹配会话成员仍用 humanIdentityId
  //（isSameHumanIdentity 两侧归一化，前缀无关）。
  const humanActorId = humanIdentity?.id ?? authenticatedUserId ?? null;
  const humanIdentityName = authenticatedUserName?.trim() || humanIdentity?.displayName || '';
  const setActiveIdentity = useWorkspaceStore((s) => s.setActiveIdentity);

  // 列表接口刷新会暂时把 participants 置空（列表不返回 participants），
  // 用 ref 暂存最近一次非空的 human 成员，避免面板在刷新间隙闪烁消失。
  // 切换身份（如「去发言」）时 session 会短暂变为 null，此时不清空缓存，
  // 避免身份切换后 human 状态丢失导致「用户发言模式」提示消失。
  const humanRef = useRef<ParticipantView | null>(null);
  const lastSessionIdRef = useRef<string | null>(null);
  const human = useMemo(() => {
    const currentSessionId = session?.sessionId ?? null;
    // 仅在切换到不同的非空会话时清空缓存，session 为 null 时保持缓存。
    if (currentSessionId && currentSessionId !== lastSessionIdRef.current) {
      lastSessionIdRef.current = currentSessionId;
      humanRef.current = null;
    } else if (currentSessionId) {
      lastSessionIdRef.current = currentSessionId;
    }
    const found =
      session?.participants.find(
        (p) => p.kind === 'human' && isSameHumanIdentity(p.actorId, humanIdentityId, 'human'),
      ) ?? null;
    if (found) {
      humanRef.current = found;
      return found;
    }
    // participants 暂时为空（列表刷新中/session 为 null），回退到上次缓存的 human 状态。
    return humanRef.current;
  }, [humanIdentityId, session]);
  const humanJoined = human?.mode === 'present';
  const humanAbsent = human?.mode === 'absent';
  const humanName =
    (human?.name ?? humanIdentityName) ||
    (activeIdentity?.kind === 'user' ? activeIdentity.displayName : '用户协作身份');

  // 切到用户身份并落到当前群会话。setActiveIdentity 会恢复该身份上次记忆的
  // 视图/选中态（可能停留在某个单聊页），若不显式覆盖 view 与选中态，
  // URL 同步 effect 会按恢复结果回填旧单聊，导致跳转落错页面且视图来回闪烁。
  const openGroupSessionAsHuman = useCallback((target: SessionView) => {
    const store = useWorkspaceStore.getState();
    store.setView('group');
    // 恢复的展开态可能不含目标群，侧栏需展开目标群（与 useWorkspacePage.ensureGroupExpanded 对齐）。
    if (!store.expandedGroupIds[target.groupId]) store.toggleGroupExpanded(target.groupId);
    store.selectGroup(target.groupId);
    store.selectSession(target.sessionId);
  }, []);

  const switchToHuman = useCallback(() => {
    if (!humanActorId) {
      toast.error('未找到用户身份，请稍后重试');
      return;
    }
    setActiveIdentity(humanActorId);
    if (session) openGroupSessionAsHuman(session);
  }, [humanActorId, openGroupSessionAsHuman, session, setActiveIdentity]);

  const setBotMode = useCallback(
    async (mode: 'auto' | 'muted') => {
      if (!session || !botActorId || mode === botMode) return;
      setSwitchingBotMode(true);
      try {
        await updateMemberMode(session.sessionId, botActorId, mode);
      } finally {
        setSwitchingBotMode(false);
      }
    },
    [session, botActorId, botMode, updateMemberMode],
  );

  const joinSession = useCallback(async (): Promise<boolean> => {
    if (!session) return false;
    const actorId = human?.actorId ?? humanActorId;
    if (!actorId) {
      toast.error('未找到用户身份，请稍后重试');
      return false;
    }
    setJoining(true);
    try {
      const ok = await updateMemberMode(session.sessionId, actorId, 'present');
      if (ok) {
        if (humanActorId) setActiveIdentity(humanActorId);
        openGroupSessionAsHuman(session);
      }
      return ok;
    } finally {
      setJoining(false);
    }
  }, [human, humanActorId, openGroupSessionAsHuman, session, setActiveIdentity, updateMemberMode]);

  const leaveSession = useCallback(async (): Promise<boolean> => {
    if (!session) return false;
    const actorId = human?.actorId ?? humanActorId;
    if (!actorId) return false;
    return updateMemberMode(session.sessionId, actorId, 'absent');
  }, [human, humanActorId, session, updateMemberMode]);

  // bot 视角恒显;human 视角 absent 时显示加入条;human 视角 present 时显示「在会话中隐身」条。
  const humanAbsentOnly = !isBotViewer && !!session && humanAbsent;
  const visible = !!session && (isBotViewer || humanAbsent || humanJoined);

  return {
    visible,
    humanAbsentOnly,
    botActorId,
    botMode,
    botName: activeIdentity?.displayName ?? 'Bot',
    human,
    humanJoined,
    humanName,
    humanAvatarUrl: human?.avatarUrl,
    canSwitchToHuman: !!humanActorId,
    switchingBotMode,
    joining,
    setBotMode,
    joinSession,
    leaveSession,
    switchToHuman,
  };
}
