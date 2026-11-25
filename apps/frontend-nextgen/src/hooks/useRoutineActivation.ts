import type { BotEditorRoutine } from '@/domain/botEditor';
import { mapRoutine } from '@/services/botWorkshop/botEditorMappers';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { toast } from 'sonner';

export interface PendingRoutineToggle {
  id: string;
  enabled: boolean;
  slow: boolean;
}

/** 定时任务启停的就地等待态（对齐 useSkillSetActivation）：响应确认前不改变开关状态，
 * 成功后按服务端返回原位更新列表，避免整页 load 导致面板闪烁。 */
export function useRoutineActivation(
  botId: string | null,
  ownerId: string | undefined,
  setRoutines: Dispatch<SetStateAction<BotEditorRoutine[]>>,
) {
  const [pendingRoutineToggle, setPendingRoutineToggle] = useState<PendingRoutineToggle>();
  const inFlight = useRef<{ botId: string; id: string } | null>(null);

  useEffect(() => {
    inFlight.current = null;
    setPendingRoutineToggle(undefined);
  }, [botId]);

  const toggleRoutine = useCallback(
    async (routine: BotEditorRoutine) => {
      if (!botId || inFlight.current) return;
      const enabled = !routine.enabled;
      const operation = { botId, id: routine.id };
      inFlight.current = operation;
      setPendingRoutineToggle({ id: routine.id, enabled, slow: false });
      const slowTimer = setTimeout(() => {
        if (inFlight.current === operation) {
          setPendingRoutineToggle({ id: routine.id, enabled, slow: true });
        }
      }, 8_000);
      try {
        const envelope = await botEditorService.updateRoutine(
          botId,
          routine.id,
          { ...routine, enabled, timeoutSecs: routine.timeoutSecs ?? 86400 },
          ownerId,
        );
        if (inFlight.current !== operation) return;
        // 推写失败必须如实报错：后端 envelope 缺 data 视为本次更新未生效，回退等待态并提示。
        if (!envelope.data) throw new Error('服务端未返回更新后的任务数据');
        const next = mapRoutine(envelope.data);
        setRoutines((current) => current.map((item) => (item.id === next.id ? next : item)));
        if (next.enabled === enabled) toast.success(enabled ? '定时任务已启用' : '定时任务已停用');
        else toast.warning('定时任务状态与操作目标不一致，请刷新后确认');
      } catch (error) {
        if (inFlight.current === operation) {
          toast.error(error instanceof Error ? error.message : '定时任务状态修改失败');
        }
      } finally {
        clearTimeout(slowTimer);
        if (inFlight.current === operation) {
          inFlight.current = null;
          setPendingRoutineToggle(undefined);
        }
      }
    },
    [botId, ownerId, setRoutines],
  );

  return { pendingRoutineToggle, toggleRoutine };
}
