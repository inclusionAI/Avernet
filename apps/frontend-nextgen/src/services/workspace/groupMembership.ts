import type { WorkspaceRouteMembership } from '@/domain/workspaceRoute';
import { listGroups } from '@/services/backendApi/collaboration/collaborationGroupController';
import { isEnvelopeSuccessAnyDialect } from '@/services/backendApi/types';
import type { DomainResult } from './identityService';

/** 详情是 target-independent；只有当前 View Actor 的列表条目才能确认参与分类。
 * 不复用侧栏搜索/首屏缓存，不把未命中当作 session_only，也不修改关系或分页缓存。
 */
export async function resolveGroupMembership(
  groupId: string,
  viewBotId?: string,
  signal?: AbortSignal,
): Promise<DomainResult<WorkspaceRouteMembership | null>> {
  try {
    for (const membership of ['session_only', 'direct'] as const) {
      let offset = 0;
      while (true) {
        if (signal?.aborted) throw new Error('Membership lookup cancelled');
        const response = await listGroups({ kind: 'normal', membership, offset, limit: 100,
          ...(viewBotId ? { view_bot_id: viewBotId } : {}) });
        if (signal?.aborted) throw new Error('Membership lookup cancelled');
        const page = response.data;
        if (!isEnvelopeSuccessAnyDialect(response) || !page || !Array.isArray(page.items)
          || typeof page.total !== 'number' || !Number.isInteger(page.total) || page.total < 0) throw new Error('Invalid membership page');
        const target = page.items.find((group) => group.kind === 'normal' && group.group_id === groupId);
        if (target) {
          if (target.membership !== membership) throw new Error('Missing or inconsistent membership');
          return { ok: true, data: target.membership };
        }
        offset += page.items.length;
        if (offset >= page.total) break;
        if (!page.items.length) throw new Error('Incomplete membership page');
      }
    }
    // 两类均无明确条目：不猜测为任何一种参与方式。
    return { ok: true, data: null };
  } catch {
    return { ok: false, error: { code: 'GROUP_MEMBERSHIP_LOAD_FAILED',
      friendlyMessage: '无法确认该群的参与方式，请稍后重试。', canRetry: true } };
  }
}
