// admin 共用的当前操作者 user_id 解析 + 懒加载兜底。
// 痛点：admin 三 Service（adminService/workOrderService/notificationService）原本直接读
// workspaceStore.activeIdentityId，但它在 workspace 页 initWorkspace 完成前为 null
// （尤其 admin/全局铃铛先于 init 触发）→ 直接报“未获取到当前用户身份”。
// ensureUserId：null 时主动调 identityService.loadIdentities 拉一次并写回 store，仍失败返回 null，
// Service 降级为 MISSING_IDENTITY_ERROR / unsupported。不依赖 React/DOM/toast（Service 层约束）。
// identityService 已做模块级单飞，此处无需重复去重。

import { getCapabilities } from '@/capabilities';
import { resolveUserId } from '@/services/workspace/botSessionService';
import { identityService } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';

/** 同步读当前已就绪身份，剥前缀得工号；未就绪返回 null（不主动拉取）。 */
export function readUserId(): string | null {
  const raw = useWorkspaceStore.getState().activeIdentityId;
  return raw ? resolveUserId(raw) : null;
}

/**
 * 确保有可用 user_id：缓存命中直接回；否则调 identityService.loadIdentities 拉取并写回 store，
 * 再读一次。仍拉不到（mine 失败）返回 null，调用方降级为 MISSING_IDENTITY_ERROR / unsupported。
 */
export async function ensureUserId(): Promise<string | null> {
  const cached = readUserId();
  if (cached) return cached;
  const res = await identityService.loadIdentities();
  if (!res.ok) return null;
  useWorkspaceStore.getState().setIdentities(res.data.identities, res.data.defaultActiveId);
  return readUserId();
}

/**
 * 同步读当前用户展示名（花名）；未就绪返回 null（不主动拉取）。
 * 经 getHumanIdentity 契约解析：内部构建取 __TERN__.user.nickName，Open Core 取 listMyBots human name，
 * 二者均缺失时该契约会回落到工号（userId）——本函数原样返回该字符串，由调用方按需判断是否为花名。
 */
export function readUserName(): string | null {
  const value = getCapabilities().getHumanIdentity().value;
  const name = value?.displayName?.trim();
  return name || null;
}

/**
 * 确保有可用的 user_name（花名）：与 ensureUserId 共用 identityService.loadIdentities 单飞拉取，
 * 缓存命中直接回；否则补拉并写回 store 再读一次。仍拉不到返回 null。
 * 用于创建空间等需记录创建者花名的场景。
 */
export async function ensureUserName(): Promise<string | null> {
  const cached = readUserName();
  if (cached) return cached;
  const res = await identityService.loadIdentities();
  if (!res.ok) return null;
  useWorkspaceStore.getState().setIdentities(res.data.identities, res.data.defaultActiveId);
  return readUserName();
}
