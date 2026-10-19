import type { UseGroupCollaborationPickerResult } from '@/pages/Workspace/hooks/useGroupCollaborationPicker';
import type { Dispatch, SetStateAction } from 'react';
import { useEffect, useRef } from 'react';

/**
 * 协作目标智能推荐结果的自动全选控制：goal 来源的推荐落地后自动勾选全部结果（含离线 Bot）；
 * 画像筛选搜索结果不自动选中；用户手动取消过的 Bot 在再次推荐时不被自动勾回（用户取消优先），
 * 手动重新勾选即解除记录；弹窗重置时清空取消记录。
 */
export function useGoalAutoSelect(
  picker: Pick<UseGroupCollaborationPickerResult, 'recommendSource' | 'isLoadingRecommend' | 'recommendations'>,
  setBotUuids: Dispatch<SetStateAction<string[]>>,
) {
  // 推荐态下用户手动取消过的 Bot 集合。
  const goalDeselectedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (picker.recommendSource !== 'goal' || picker.isLoadingRecommend || picker.recommendations.length === 0) return;
    const incoming = picker.recommendations
      .filter((bot) => !goalDeselectedRef.current.has(bot.id))
      .map((bot) => bot.id);
    if (incoming.length === 0) return;
    setBotUuids((prev) =>
      incoming.every((id) => prev.includes(id)) ? prev : Array.from(new Set([...prev, ...incoming])),
    );
  }, [picker.recommendSource, picker.isLoadingRecommend, picker.recommendations, setBotUuids]);

  return {
    /** 取消勾选时记录：goal 推荐态下的推荐项记入排除集，再次推荐不自动勾回；非 goal 推荐态无副作用。 */
    recordCancel: (id: string) => {
      if (picker.recommendSource === 'goal' && picker.recommendations.some((bot) => bot.id === id)) {
        goalDeselectedRef.current.add(id);
      }
    },
    /** 手动重新勾选视为撤销取消意愿，解除排除记录。 */
    clearCancel: (id: string) => {
      goalDeselectedRef.current.delete(id);
    },
    /** 弹窗重置时清空取消记录。 */
    reset: () => {
      goalDeselectedRef.current = new Set();
    },
  };
}

export default useGoalAutoSelect;