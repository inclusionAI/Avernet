import type { GroupView } from '@/domain/collaboration';
import { bcsfuseService, type FusionBotInfo } from '@/services/workspace/bcsfuseService';
import { useFuseStore, type FuseMessage, type FuseParticipant } from '@/stores/fuseStore';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

export type { FusionBotInfo };

/**
 * useFuse —— 融合模式问答 + Worker 画像公开配置。
 * 对齐 open-claw useFuse，接口改为 /openapi/v1/bcsfuse/* 且响应经 BackendApiEnvelope 包裹。
 */
export function useFuse(group: GroupView | null, sessionId: string | null) {
  const messagesMap = useFuseStore((s) => s.messagesMap);
  const fusingSessionIds = useFuseStore((s) => s.fusingSessionIds);
  const addMessage = useFuseStore((s) => s.addMessage);
  const updateMessage = useFuseStore((s) => s.updateMessage);
  const setSessionFusing = useFuseStore((s) => s.setSessionFusing);
  const setUnreadSession = useFuseStore((s) => s.setUnreadSession);
  const clearSessionMessages = useFuseStore((s) => s.clearSessionMessages);

  const messages = useMemo(() => (sessionId ? messagesMap[sessionId] || [] : []), [messagesMap, sessionId]);
  const isFusing = useMemo(() => (sessionId ? !!fusingSessionIds[sessionId] : false), [fusingSessionIds, sessionId]);

  const [fusionBots, setFusionBots] = useState<FusionBotInfo[]>([]);
  const [isLoadingFusionBots, setIsLoadingFusionBots] = useState(false);

  const fetchFusionBots = useCallback(async () => {
    if (!group?.participants) return;
    setIsLoadingFusionBots(true);
    const res = await bcsfuseService.getFusionBots(group.participants);
    setFusionBots(res.ok ? res.data : []);
    setIsLoadingFusionBots(false);
  }, [group]);

  useEffect(() => {
    if (group) void fetchFusionBots();
  }, [group, fetchFusionBots]);

  const submitQuestion = useCallback(
    async (question: string, selectedBotIds?: string[]) => {
      if (!group?.groupId || !sessionId || !question.trim() || isFusing) return;
      const participants = selectedBotIds?.length
        ? selectedBotIds
        : fusionBots.filter((b) => b.fusionEnable).map((b) => b.botUuid);
      if (participants.length === 0) return;
      const driverBotId =
        group.participants.find((p) => p.role === 'driver')?.actorId || group.participants[0]?.actorId || '';
      const participantInfos: FuseParticipant[] = participants
        .map((id) => {
          const bot = fusionBots.find((b) => b.botUuid === id);
          return bot ? { id: bot.botUuid, name: bot.name, avatar: bot.avatar } : null;
        })
        .filter(Boolean) as FuseParticipant[];
      const userMsg: FuseMessage = {
        id: `fuse-user-${Date.now()}`,
        role: 'user',
        content: question.trim(),
        timestamp: Date.now(),
        participants: participantInfos,
      };
      addMessage(sessionId, userMsg);
      const assistantMsgId = `fuse-assistant-${Date.now()}`;
      addMessage(sessionId, {
        id: assistantMsgId,
        role: 'assistant',
        content: '',
        timestamp: Date.now(),
        isLoading: true,
      });
      setSessionFusing(sessionId, true);
      const res = await bcsfuseService.postFuse(group.groupId, {
        session_id: sessionId,
        question: question.trim(),
        driver_bot_id: driverBotId,
        participants,
        fusion_mode: 'bot_profile_fuse',
        options: { timeout_ms: 180000 },
      });
      if (res.ok && res.data.success && res.data.summary) {
        updateMessage(sessionId, assistantMsgId, { content: res.data.summary, isLoading: false });
      } else {
        toast.error(res.ok ? res.data.error || '问答失败，请重试' : res.error.friendlyMessage);
        updateMessage(sessionId, assistantMsgId, { content: '问答失败，请重试', isLoading: false });
      }
      setSessionFusing(sessionId, false);
      setUnreadSession(sessionId, true);
    },
    [group, sessionId, isFusing, fusionBots, addMessage, updateMessage, setSessionFusing, setUnreadSession],
  );

  return { messages, isFusing, submitQuestion, clearSessionMessages, fusionBots, isLoadingFusionBots };
}
