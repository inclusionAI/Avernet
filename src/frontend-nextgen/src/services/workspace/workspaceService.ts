import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { DomainResult } from './identityService';
import { identityService } from './identityService';
import { TeamClawSupportProvider } from './supportProvider';

// 为兼容既有 import 路径（index re-export 与 workspaceService.test），透传 supportProvider 的公开导出。
export {
  buildPrivateWebsocketUrl,
  mapPrivateHistoryMessages,
  resolvePrivateWebsocketPath,
  TEAMCLAW_SUPPORT_BOT,
  TeamClawSupportProvider,
  type SupportChatRequest,
} from './supportProvider';

// Service 不依赖 React，不弹 toast；写 Store 通过同步 setter。
// Service 是唯一写 useWorkspaceStore 的位置；后续 Hook 调 Service，Component 只调 Hook。
export const workspaceService = {
  /** 初始加载：拉取可协作身份并入 Store，可选高亮指定 group。路由参数由 useWorkspacePage 通过 URL 驱动。 */
  async initWorkspace(preferredGroupId?: string): Promise<DomainResult<{ defaultActiveId: string | null }>> {
    const res = await identityService.loadIdentities();
    if (!res.ok) return res;
    useWorkspaceStore.getState().setIdentities(res.data.identities, res.data.defaultActiveId);
    if (preferredGroupId) useWorkspaceStore.getState().selectGroup(preferredGroupId);
    return { ok: true, data: { defaultActiveId: res.data.defaultActiveId } };
  },
  /** 切换身份时持久化到 localStorage，供下次初始化回填默认 active。 */
  persistIdentity(id: string) {
    identityService.persistLastIdentityId(id);
  },
  getOverview() {
    return {
      module: 'workspace',
      description: '对话协作 Service 组合 PrivateChat Controller、历史消息 Mapper 和 OpenClaw Provider。',
    };
  },
  createProvider() {
    return new TeamClawSupportProvider();
  },
};
