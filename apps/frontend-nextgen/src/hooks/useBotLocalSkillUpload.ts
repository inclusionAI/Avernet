import type { BotEditorSkill } from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { toast } from 'sonner';

export function useBotLocalSkillUpload(botId: string | null, setSkills: Dispatch<SetStateAction<BotEditorSkill[]>>) {
  return useCallback(
    async (files: File[]) => {
      if (!botId) throw new Error('缺少 Bot 标识');
      try {
        const skill = await botEditorService.uploadSkillFolder(botId, files);
        setSkills((current) => [skill, ...current.filter((item) => item.id !== skill.id)]);
        toast.success('本地 Skill 已上传，请勾选后确认添加');
        return skill;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '本地 Skill 目录上传失败');
        throw error;
      }
    },
    [botId, setSkills],
  );
}
