/**
 * useFuse - 智能问答 + Bot 画像公开配置 Hook
 *
 * 封装智能融合问答和 Worker Config 的业务逻辑
 * 遵循四层架构：Hook 层封装业务逻辑、API 调用、错误处理和 Toast
 */

import type { GroupInfo } from '@/pages/GroupChat/types';
import {
  handleApiError,
  handleNetworkError,
  showSuccessToast,
} from '@/utils/hooksErrorHandler';
import { useCallback, useEffect, useMemo, useState } from 'react';
import * as BcsfuseController from '@/services/backend-api/BcsfuseController';
import {
  useFuseStore,
  type FuseMessage,
  type FuseParticipant,
} from '@/stores/groupchat/fuseStore';

/** 融合可选 Bot 信息 */
export interface FusionBotInfo {
  botUuid: string;
  name: string;
  avatar?: string;
  fusionEnable: boolean;
}

export function useFuse(
  group: GroupInfo | null,
  sessionId: string | null,
  driverBotUuid?: string,
) {
  const messagesMap = useFuseStore((s) => s.messagesMap);
  const fusingSessionIds = useFuseStore((s) => s.fusingSessionIds);
  const addMessage = useFuseStore((s) => s.addMessage);
  const updateMessage = useFuseStore((s) => s.updateMessage);
  const setSessionFusing = useFuseStore((s) => s.setSessionFusing);
  const setUnreadSession = useFuseStore((s) => s.setUnreadSession);
  const clearSessionMessages = useFuseStore((s) => s.clearSessionMessages);

  // 按 sessionId 派生当前消息列表
  const messages = useMemo(
    () => (sessionId ? messagesMap[sessionId] || [] : []),
    [messagesMap, sessionId],
  );

  // 按 sessionId 派生当前 session 是否正在 fusing
  const isFusing = useMemo(
    () => (sessionId ? !!fusingSessionIds[sessionId] : false),
    [fusingSessionIds, sessionId],
  );

  // Bot 画像公开状态（driverBot）
  const [fusionEnable, setFusionEnable] = useState<boolean | null>(null);
  const [fusionConfigVersion, setFusionConfigVersion] = useState<number | null>(
    null,
  );
  const [isLoadingConfig, setIsLoadingConfig] = useState(false);
  const [isUpdatingConfig, setIsUpdatingConfig] = useState(false);

  // 融合可选 Bot 列表
  const [fusionBots, setFusionBots] = useState<FusionBotInfo[]>([]);
  const [isLoadingFusionBots, setIsLoadingFusionBots] = useState(false);

  // 获取 Worker 配置（driverBot）
  const fetchWorkerConfig = useCallback(async () => {
    if (!driverBotUuid) return;
    // 用户视角（human_ 开头）不需要查询画像配置
    if (driverBotUuid.startsWith('human_')) return;
    setIsLoadingConfig(true);
    try {
      const res = await BcsfuseController.getWorkerConfig({
        worker_id: driverBotUuid,
      });
      if (res.success !== false) {
        setFusionEnable(res.fusion_enable);
        setFusionConfigVersion(res.version);
      }
    } catch (error) {
      console.error('[Bcsfuse] Failed to fetch worker config:', error);
      setFusionEnable(false);
    } finally {
      setIsLoadingConfig(false);
    }
  }, [driverBotUuid]);

  // 获取所有 Bot 参与者的画像公开状态
  const fetchFusionBots = useCallback(async () => {
    if (!group?.participants) return;
    const botParticipants = group.participants.filter((p) => p.type === 'bot');
    if (botParticipants.length === 0) {
      setFusionBots([]);
      return;
    }

    setIsLoadingFusionBots(true);
    try {
      const results = await Promise.allSettled(
        botParticipants.map(async (bot) => {
          const botUuid = bot.botUuid || bot.id;
          try {
            const res = await BcsfuseController.getWorkerConfig({
              worker_id: botUuid,
            });
            return {
              botUuid,
              name: bot.name,
              avatar: bot.avatar,
              fusionEnable: res.success !== false && res.fusion_enable === true,
            };
          } catch {
            return {
              botUuid,
              name: bot.name,
              avatar: bot.avatar,
              fusionEnable: false,
            };
          }
        }),
      );

      const bots = results
        .filter(
          (r): r is PromiseFulfilledResult<FusionBotInfo> =>
            r.status === 'fulfilled',
        )
        .map((r) => r.value);

      setFusionBots(bots);
    } catch (error) {
      console.error('[Bcsfuse] Failed to fetch fusion bots:', error);
      // 降级：所有 bot 默认不可选
      setFusionBots(
        botParticipants.map((bot) => ({
          botUuid: bot.botUuid || bot.id,
          name: bot.name,
          avatar: bot.avatar,
          fusionEnable: false,
        })),
      );
    } finally {
      setIsLoadingFusionBots(false);
    }
  }, [group]);

  // 首次加载时获取配置（用户视角跳过）
  useEffect(() => {
    if (driverBotUuid) {
      fetchWorkerConfig();
    }
  }, [driverBotUuid, fetchWorkerConfig]);

  // 面板打开时获取融合 Bot 列表
  useEffect(() => {
    if (group) {
      fetchFusionBots();
    }
  }, [group, fetchFusionBots]);

  // 切换画像公开
  const toggleFusionEnable = useCallback(
    async (enable: boolean) => {
      if (!driverBotUuid) return;
      setIsUpdatingConfig(true);
      try {
        const res = await BcsfuseController.updateWorkerConfig({
          worker_id: driverBotUuid,
          fusion_enable: enable,
          version: fusionConfigVersion ?? undefined,
        });
        if (res.success !== false) {
          setFusionEnable(res.fusion_enable);
          setFusionConfigVersion(res.version);
          showSuccessToast({
            module: 'Bcsfuse',
            action: enable ? '开启Bot画像公开' : '关闭Bot画像公开',
          });
          // 刷新融合 Bot 列表
          await fetchFusionBots();
        } else {
          handleApiError(res, {
            module: 'Bcsfuse',
            action: '修改Bot画像公开设置',
          });
        }
      } catch (error: any) {
        console.error('[Bcsfuse] Failed to update worker config:', error);
        if (error?.response?.status === 409) {
          await fetchWorkerConfig();
        }
        handleNetworkError(error, {
          module: 'Bcsfuse',
          action: '修改Bot画像公开设置',
        });
      } finally {
        setIsUpdatingConfig(false);
      }
    },
    [driverBotUuid, fusionConfigVersion, fetchWorkerConfig, fetchFusionBots],
  );

  /**
   * 提交问题并获取融合回答
   */
  const submitQuestion = useCallback(
    async (question: string, selectedBotIds?: string[]) => {
      if (!group?.id || !sessionId || !question.trim() || isFusing) return;

      // 使用选中的 bot 或降级为所有 fusionEnable 的 bot
      const participants =
        selectedBotIds && selectedBotIds.length > 0
          ? selectedBotIds
          : fusionBots.filter((b) => b.fusionEnable).map((b) => b.botUuid);

      if (participants.length === 0) {
        console.warn('[useFuse] No fusion participants available');
        return;
      }

      const driverBotId =
        group.participants.find((p) => p.role === 'driver')?.botUuid ||
        group.participants[0]?.botUuid ||
        '';

      // 构建参与者信息（用于消息展示）
      const participantInfos: FuseParticipant[] = participants
        .map((id) => {
          const bot = fusionBots.find((b) => b.botUuid === id);
          return bot
            ? { id: bot.botUuid, name: bot.name, avatar: bot.avatar }
            : null;
        })
        .filter((p): p is FuseParticipant => p !== null);

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

      try {
        const response = await BcsfuseController.postFuse({
          group_id: group.id,
          session_id: sessionId,
          question: question.trim(),
          driver_bot_id: driverBotId,
          participants,
          fusion_mode: 'bot_profile_fuse',
          options: { timeout_ms: 180000 },
        });

        if (response.success !== false && response.recommendation?.summary) {
          updateMessage(sessionId, assistantMsgId, {
            content: response.recommendation.summary,
            isLoading: false,
          });
        } else {
          handleApiError(response, {
            module: 'Bcsfuse',
            action: '融合模式',
          });
          updateMessage(sessionId, assistantMsgId, {
            content: '问答失败，请重试',
            isLoading: false,
          });
        }
      } catch (error) {
        console.error('[Bcsfuse] Fuse request failed:', error);
        handleNetworkError(error, {
          module: 'Bcsfuse',
          action: '融合模式',
        });
        updateMessage(sessionId, assistantMsgId, {
          content: '网络异常，请重试',
          isLoading: false,
        });
      } finally {
        setSessionFusing(sessionId, false);
        // 请求完成后标记该 session 未读，面板打开时由组件清除
        setUnreadSession(sessionId, true);
      }
    },
    [
      group,
      sessionId,
      isFusing,
      fusionBots,
      addMessage,
      updateMessage,
      setSessionFusing,
      setUnreadSession,
    ],
  );

  return {
    messages,
    isFusing,
    submitQuestion,
    clearSessionMessages,
    fusionEnable,
    isLoadingConfig,
    isUpdatingConfig,
    toggleFusionEnable,
    fusionBots,
    isLoadingFusionBots,
  };
}
