/** useBotSkills — 拉取当前 Bot 的 Skill 列表（供 /skill 命令子面板使用）。 */
import type { ChatBotView } from '@/services/workspace/botSessionService';
import type { BotSkillView } from '@/services/workspace/botSkillService';
import { botSkillService } from '@/services/workspace/botSkillService';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface UseBotSkillsResult {
  skills: BotSkillView[];
  isLoading: boolean;
  loadSkills: () => Promise<BotSkillView[]>;
}

export function useBotSkills(bot: ChatBotView | null, userId: string | null, autoLoad = true): UseBotSkillsResult {
  const [skills, setSkills] = useState<BotSkillView[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const loadedKeyRef = useRef<string | null>(null);
  const key = JSON.stringify([bot?.realBotId, bot?.ownerId, bot?.runtimeStage, bot?.botType,
    bot?.engine, bot?.isTeamBot, bot?.isFriendBot, userId]);
  const currentKey = useRef(key);
  currentKey.current = key;
  const pending = useRef<{ key: string; promise: Promise<BotSkillView[]> } | null>(null);
  const cached = useRef<{ key: string; skills: BotSkillView[]; expiresAt: number } | null>(null);
  useEffect(() => {
    currentKey.current = key;
    return () => { currentKey.current = ''; };
  }, [key]);

  const loadSkills = useCallback(() => {
    if (!bot || bot.isFriendBot || !bot.chatable || !userId) return Promise.resolve([]);
    if (pending.current?.key === key) return pending.current.promise;
    if (!autoLoad && cached.current?.key === key && cached.current.expiresAt > Date.now()) {
      return Promise.resolve(cached.current.skills);
    }
    setIsLoading(true);
    const promise = botSkillService
      .listSkills(bot, userId)
      .then((res) => {
        if (currentKey.current !== key) return [];
        if (res.ok) {
          cached.current = { key, skills: res.data, expiresAt: Date.now() + 60_000 };
          setSkills(res.data);
          return res.data;
        }
        toast.error(res.error.friendlyMessage);
        return [];
      })
      .catch(() => {
        if (currentKey.current === key) toast.error('技能列表加载失败');
        return [];
      })
      .finally(() => {
        if (pending.current?.promise === promise) pending.current = null;
        if (currentKey.current === key) setIsLoading(false);
      });
    pending.current = { key, promise };
    return promise;
  }, [autoLoad, bot, key, userId]);

  useEffect(() => {
    if (loadedKeyRef.current === key) return;
    loadedKeyRef.current = key;
    setSkills([]);
    setIsLoading(false);
    if (!bot || bot.isFriendBot || !userId) {
      setSkills([]);
      return;
    }
    if (!bot.chatable) {
      setSkills([]);
      return;
    }
    if (autoLoad) void loadSkills();
  }, [key, bot, userId, autoLoad, loadSkills]);

  return { skills, isLoading, loadSkills };
}
