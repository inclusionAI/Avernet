import type { IdentityView, ParticipantMode, ParticipantView, SessionView } from '@/domain/collaboration';
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
  /** 会话内 human 成员（当前用户）。 */
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
    const found = session?.participants.find((p) => p.kind === 'human') ?? null;
    if (found) {
      humanRef.current = found;
      return found;
    }
    // participants 暂时为空（列表刷新中/session 为 null），回退到上次缓存的 human 状态。
    return humanRef.current;
  }, [session]);
  const humanJoined = human?.mode === 'present';
  const humanAbsent = human?.mode === 'absent';
  const humanName = human?.name ?? '我';

  const identities = useWorkspaceStore((s) => s.identities);
  const humanIdentityId = useMemo(
    () => identities.find((i) => i.kind === 'user' && !i.id.startsWith('test-'))?.id ?? null,
    [identities],
  );
  const setActiveIdentity = useWorkspaceStore((s) => s.setActiveIdentity);

  const switchToHuman = useCallback(() => {
    if (!humanIdentityId) {
      toast.error('未找到用户身份，请稍后重试');
      return;
    }
    setActiveIdentity(humanIdentityId);
    if (session) {
      const store = useWorkspaceStore.getState();
      store.selectGroup(session.groupId);
      store.selectSession(session.sessionId);
    }
  }, [humanIdentityId, session, setActiveIdentity]);

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
    const actorId = human?.actorId ?? humanIdentityId;
    if (!actorId) {
      toast.error('未找到用户身份，请稍后重试');
      return false;
    }
    setJoining(true);
    try {
      const ok = await updateMemberMode(session.sessionId, actorId, 'present');
      if (ok) {
        if (humanIdentityId) setActiveIdentity(humanIdentityId);
        const store = useWorkspaceStore.getState();
        store.selectGroup(session.groupId);
        store.selectSession(session.sessionId);
      }
      return ok;
    } finally {
      setJoining(false);
    }
  }, [human, humanIdentityId, session, setActiveIdentity, updateMemberMode]);

  const leaveSession = useCallback(async (): Promise<boolean> => {
    if (!session) return false;
    const actorId = human?.actorId ?? humanIdentityId;
    if (!actorId) return false;
    return updateMemberMode(session.sessionId, actorId, 'absent');
  }, [human, humanIdentityId, session, updateMemberMode]);

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
    canSwitchToHuman: !!humanIdentityId,
    switchingBotMode,
    joining,
    setBotMode,
    joinSession,
    leaveSession,
    switchToHuman,
  };
}
