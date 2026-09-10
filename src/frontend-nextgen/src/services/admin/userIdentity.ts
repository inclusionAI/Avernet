// admin 共用的当前操作者 user_id / user_name 解析 + 懒加载兜底。
// 数据源：getCapabilities().getHumanIdentity() — canonical 能力，按部署态产出匹配网关签名
// 主体的 id（内部 staffNo / Open Core 阿里云 BCS id），与 useHumanIdentity / httpClient
// injectUserId / capability-workshop / account badge 同源。admin 不直接读
// workspaceStore.activeIdentityId（bots/mine 在内部裁出 BCS id，与内部网关签的 staffNo 不符
// → 403 UserIdMismatch）；readUserName 早已同源，此处对齐 readUserId。
// ensureUserId：能力值未就绪时主动调 identityService.loadIdentities 拉一次并写回 store
// （Open Core me 兜底分支；内部 staffNo 同步命中），仍失败返回 null，Service 降级为
// MISSING_IDENTITY_ERROR / unsupported。不依赖 React/DOM/toast（Service 层约束）。
// identityService 已做模块级单飞，此处无需重复去重。

import { getCapabilities } from '@/capabilities';
import { resolveUserId } from '@/services/workspace/botSessionService';
import { identityService } from '@/services/workspace/identityService';
import { applyIdentityLoadResult } from '@/services/workspace/identityStore';

/** 同步读当前已就绪操作者 user_id（经 canonical getHumanIdentity 能力），剥前缀兜底；未就绪返回 null（不主动拉取）。 */
export function readUserId(): string | null {
  const raw = getCapabilities().getHumanIdentity().value?.userId?.trim();
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
  applyIdentityLoadResult(res.data);
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
  applyIdentityLoadResult(res.data);
  return readUserName();
}
