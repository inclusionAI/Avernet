import type { IdentityView } from '@/domain/collaboration';
import { useMemo } from 'react';
import type { UseGroupCollaborationPickerResult } from './useGroupCollaborationPicker';

/** 合并 picker 中所有 tab 的 Bot（好友 + 我的 + 可协作），并补充发起方 Bot（若为 bot 身份）。 */
export function useAllAvailableBots(
  activeIdentity: IdentityView | null | undefined,
  picker: UseGroupCollaborationPickerResult,
): Array<{ id: string; name: string }> {
  return useMemo(() => {
    const map = new Map<string, { id: string; name: string }>();
    [...picker.friends, ...picker.mine, ...picker.candidates].forEach((bot) => {
      map.set(bot.id, { id: bot.id, name: bot.name });
    });
    if (activeIdentity?.kind === 'bot') {
      map.set(activeIdentity.id, { id: activeIdentity.id, name: activeIdentity.displayName });
    }
    return Array.from(map.values());
  }, [activeIdentity, picker.candidates, picker.friends, picker.mine]);
}
