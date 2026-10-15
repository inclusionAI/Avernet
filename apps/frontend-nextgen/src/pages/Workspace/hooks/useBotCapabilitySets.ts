/** useBotCapabilitySets — 拉取当前 Bot 已激活的能力集（供「通用协作能力」只读查看）。
 *
 * 数据链复用 BotWorkshop 同源只读接口（design D2）：listSkillSetResources 一次取全量
 * 集 + 内嵌 mcps，明细 skills 按需逐集拉取（失败以 failed 标记，不阻塞其他集）。
 * 接口调用与 DTO 映射经 botCapabilitySetService 下沉（分层：Hook → Service → API Controller）；
 * 团队 Bot 实体 owner 必须显式传 bot.ownerId。好友 Bot / 不可会话 Bot 不装配
 * （返回 null），好友/团队判定留在装配层，组件零业务分类判断（design D4）。 */
import type {
  CapabilitySetDetailResult,
  CapabilitySetView,
  ComposerCapabilitySetsState,
} from '@/components/Workspace/CapabilitySetViewer/types';
import { listActiveCapabilitySets, listCapabilitySetSkills } from '@/services/workspace/botCapabilitySetService';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

/** 是否对该 Bot 装配能力集查看（我的/团队 Bot；好友 Bot 不展示，规格「能力集区展示资格」）。 */
export function isCapabilitySetBot(bot: ChatBotView | null): boolean {
  return !!bot && !bot.isFriendBot && !!bot.chatable;
}

export function useBotCapabilitySets(
  bot: ChatBotView | null,
  userId: string | null,
): ComposerCapabilitySetsState | null {
  const [sets, setSets] = useState<CapabilitySetView[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const inFlightRef = useRef(false);
  // bot/userId 变化即失效既有数据（旧 Bot 的集不跨会话泄漏）。
  const key = `${bot?.realBotId ?? ''}:${bot?.ownerId ?? ''}:${userId ?? ''}`;
  useEffect(() => {
    setSets([]);
    setIsLoading(false);
    inFlightRef.current = false;
  }, [key]);

  const load = useCallback(() => {
    if (!bot || !isCapabilitySetBot(bot) || !userId || inFlightRef.current) return;
    inFlightRef.current = true;
    setIsLoading(true);
    void listActiveCapabilitySets(bot.realBotId, bot.ownerId)
      .then((activeSets) => {
        setSets(activeSets);
      })
      .catch((err: unknown) => {
        toast.error(err instanceof Error ? err.message : '能力集列表请求失败');
      })
      .finally(() => {
        inFlightRef.current = false;
        setIsLoading(false);
      });
  }, [bot, userId]);

  /** 按需拉取单集 skills 明细（展开【查看明细】时调用）；失败以 failed 标记，不抛错。 */
  const fetchSetSkills = useCallback(
    async (setId: string): Promise<CapabilitySetDetailResult> => {
      if (!bot) return { skills: [], failed: true };
      try {
        const skills = await listCapabilitySetSkills(bot.realBotId, setId, bot.ownerId);
        return { skills, failed: false };
      } catch {
        return { skills: [], failed: true };
      }
    },
    [bot],
  );

  if (!isCapabilitySetBot(bot) || !userId) return null;
  return { sets, isLoading, load, fetchSetSkills };
}