import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { DomainResult } from './identityService';
import { identityService } from './identityService';
import { applyIdentityLoadResult } from './identityStore';
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
    useWorkspaceStore.getState().setIsIdentityListLoading(true);
    try {
      const res = await identityService.loadIdentities();
      if (!res.ok) return res;
      // 若 store 已有 activeIdentityId（如 URL→Store effect 已按外链 session 切到用户身份），
      // 不用持久化默认身份覆盖，避免协作广场跳转后被切回上次持久化的 bot 角色。
      applyIdentityLoadResult(res.data);
      // 仅当选中群尚未设置时才按 preferredGroupId 选中，避免 URL→Store effect 已设好
      // 的 group + session 被 selectGroup 清掉 selectedSessionId（selectGroup 清会话选中态）。
      if (preferredGroupId && !useWorkspaceStore.getState().selectedGroupId) {
        useWorkspaceStore.getState().selectGroup(preferredGroupId);
      }
      return { ok: true, data: { defaultActiveId: res.data.defaultActiveId } };
    } finally {
      useWorkspaceStore.getState().setIsIdentityListLoading(false);
    }
  },
  /** 切换身份时持久化到 localStorage，供下次初始化回填默认 active。 */
  persistIdentity(id: string) {
    identityService.persistLastIdentityId(id);
  },
  /** 全局协作身份入口的切换用例：持久化选择并重置对应工作区状态。 */
  switchIdentity(id: string) {
    identityService.persistLastIdentityId(id);
    useWorkspaceStore.getState().setActiveIdentity(id);
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
