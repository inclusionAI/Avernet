import type { BotRuntimeStage } from '@/domain/botWorkshop';
import { useLocation } from '@umijs/max';

/** The explicit route identity/stage is shared by the editor and extension panels. */
export function useBotEditorAddress() {
  const params = new URLSearchParams(useLocation().search);
  const stage = params.get('runtime_stage');
  const runtimeStage: BotRuntimeStage | undefined =
    stage === 'draft' || stage === 'verify' || stage === 'online' ? stage : undefined;
  return {
    id: params.get('id'),
    ownerId: params.get('owner_id')?.trim() || undefined,
    requestedEditable: params.get('type') === 'edit',
    runtimeStage,
    /** 深链指定的初始 tab（如资源管理跳转带 ?tab=resource）；合法性由 useHostingTabs 校验。 */
    requestedTab: params.get('tab')?.trim() || undefined,
  };
}
