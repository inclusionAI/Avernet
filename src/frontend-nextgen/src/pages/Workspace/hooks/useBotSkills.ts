/** useBotSkills — 拉取当前 Bot 的 Skill 列表（供 /skill 命令子面板使用）。 */
import type { ChatBotView } from '@/services/workspace/botSessionService';
import type { BotSkillView } from '@/services/workspace/botSkillService';
import { botSkillService } from '@/services/workspace/botSkillService';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface UseBotSkillsResult {
  skills: BotSkillView[];
  isLoading: boolean;
  loadSkills: () => void;
}

export function useBotSkills(bot: ChatBotView | null, userId: string | null): UseBotSkillsResult {
  const [skills, setSkills] = useState<BotSkillView[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const loadedKeyRef = useRef<string | null>(null);

  const loadSkills = useCallback(() => {
    if (!bot || !userId) return;
    setIsLoading(true);
    void botSkillService
      .listSkills(bot, userId)
      .then((res) => {
        if (res.ok) setSkills(res.data);
        else toast.error(res.error.friendlyMessage);
      })
      .finally(() => setIsLoading(false));
  }, [bot, userId]);

  useEffect(() => {
    const key = `${bot?.realBotId ?? ''}_${userId ?? ''}`;
    if (loadedKeyRef.current === key) return;
    loadedKeyRef.current = key;
    if (!bot || !userId) {
      setSkills([]);
      return;
    }
    if (!bot.chatable) {
      setSkills([]);
      return;
    }
    loadSkills();
  }, [bot, userId, loadSkills]);

  return { skills, isLoading, loadSkills };
}
