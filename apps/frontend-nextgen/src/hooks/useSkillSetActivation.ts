import type { BotCapabilitySet } from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { toast } from 'sonner';

export interface PendingSkillSetToggle {
  id: string;
  active: boolean;
  slow: boolean;
}

/** 启用与停用共用等待态；响应确认前不改变开关状态。 */
export function useSkillSetActivation(
  botId: string | null,
  setSkillSets: Dispatch<SetStateAction<BotCapabilitySet[]>>,
) {
  const [pendingSkillSetToggle, setPendingSkillSetToggle] = useState<PendingSkillSetToggle>();
  const inFlight = useRef<{ botId: string; id: string } | null>(null);

  useEffect(() => {
    inFlight.current = null;
    setPendingSkillSetToggle(undefined);
  }, [botId]);

  const setSkillSetActive = useCallback(
    async (set: BotCapabilitySet, active: boolean) => {
      if (!botId || inFlight.current) return;
      const operation = { botId, id: set.id };
      inFlight.current = operation;
      setPendingSkillSetToggle({ id: set.id, active, slow: false });
      const slowTimer = setTimeout(() => {
        if (inFlight.current === operation) {
          setPendingSkillSetToggle({ id: set.id, active, slow: true });
        }
      }, 8_000);
      try {
        const result = await botEditorService.setSkillSetActive(botId, set, active);
        if (inFlight.current !== operation) return;
        setSkillSets((current) =>
          current.map((item) => (item.id === result.id ? { ...item, active: result.active } : item)),
        );
        if (result.active === active) toast.success(active ? '能力集已启用' : '能力集已停用');
        else toast.warning('能力集状态与操作目标不一致，请刷新后确认');
      } catch (error) {
        if (inFlight.current === operation) {
          toast.error(error instanceof Error ? error.message : '能力集状态修改失败');
        }
      } finally {
        clearTimeout(slowTimer);
        if (inFlight.current === operation) {
          inFlight.current = null;
          setPendingSkillSetToggle(undefined);
        }
      }
    },
    [botId, setSkillSets],
  );

  return { pendingSkillSetToggle, setSkillSetActive };
}
