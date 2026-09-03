import type { FriendApprovalConfig, PendingPublication, PublicAudience } from '@/domain/collaborationPrivacy/types';

export type FriendCheckInStrategy = 'OPEN' | 'APPROVAL' | 'DEPT_FREE';
export type FriendExt = Record<string, unknown>;

export interface FriendApprovalAttributesSnapshot {
  friend_ext?: unknown;
  friend_check_in_strategy?: unknown;
}

export interface FriendApprovalBackendState {
  strategy: FriendCheckInStrategy;
  noCheckScopeFriendDeps: string[];
}

const approvalStrategies: FriendCheckInStrategy[] = ['OPEN', 'APPROVAL', 'DEPT_FREE'];
const pendingPublicationStatuses = new Set(['CREATED', 'PROCESSING', 'PENDING']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function toSafeApprovalUrl(value: unknown): string | undefined {
  const url = typeof value === 'string' ? value.trim() : '';
  if (!url) return undefined;
  if (url.startsWith('/') && !url.startsWith('//')) return url;

  try {
    return new URL(url).protocol === 'https:' ? url : undefined;
  } catch {
    return undefined;
  }
}

function mapPendingPublication(value: unknown, audience: PublicAudience): PendingPublication | undefined {
  if (!isRecord(value)) return undefined;
  const status = typeof value.status === 'string' ? value.status.trim().toUpperCase() : '';
  const id = typeof value.puid === 'string' ? value.puid.trim() : '';
  const visibility = typeof value.visibility === 'string' ? value.visibility.trim().toLowerCase() : '';
  if (!pendingPublicationStatuses.has(status) || !id || (visibility !== 'public' && visibility !== 'private')) {
    return undefined;
  }

  const target =
    visibility === 'private'
      ? { scope: 'none' as const, organizationPaths: [] }
      : Array.isArray(value.view_friend_deps) && value.view_friend_deps.length > 0
      ? { scope: 'restricted' as const, organizationPaths: [] }
      : { scope: 'all' as const, organizationPaths: [] };
  const approvalUrl = toSafeApprovalUrl(value.approval_url);
  return {
    id,
    audience,
    target,
    submittedAt: '',
    ...(approvalUrl ? { approvalUrl } : {}),
  };
}

export function mapPublicationApprovalsFromFriendExt(
  value: unknown,
): Partial<Record<PublicAudience, PendingPublication>> {
  if (!isRecord(value)) return {};
  const user = mapPendingPublication(value.public_user_approval, 'user');
  const bot = mapPendingPublication(value.public_agent_approval, 'bot');
  return { ...(user ? { user } : {}), ...(bot ? { bot } : {}) };
}

function normalizeDepartmentNos(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [
    ...new Set(
      value
        .filter((item): item is string => typeof item === 'string')
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ].sort((left, right) => left.localeCompare(right));
}

function asStrategy(value: unknown): FriendCheckInStrategy {
  const normalized = typeof value === 'string' ? value.trim().toUpperCase() : '';
  return approvalStrategies.includes(normalized as FriendCheckInStrategy)
    ? (normalized as FriendCheckInStrategy)
    : 'APPROVAL';
}

export function readFriendApprovalAttributes(snapshot: FriendApprovalAttributesSnapshot): FriendApprovalBackendState {
  const friendExt = isRecord(snapshot.friend_ext) ? snapshot.friend_ext : {};
  return {
    strategy: asStrategy(snapshot.friend_check_in_strategy),
    noCheckScopeFriendDeps: normalizeDepartmentNos(friendExt.no_check_scope_friend_deps),
  };
}

export function mapFriendApprovalAttributesToDomain(
  snapshot: FriendApprovalAttributesSnapshot,
): FriendApprovalConfig & { exemptDepartmentNos: string[] } {
  const state = readFriendApprovalAttributes(snapshot);
  return {
    mode: state.strategy === 'OPEN' ? 'none' : state.strategy === 'DEPT_FREE' ? 'partial_exempt' : 'all',
    exemptOrganizationPaths: [],
    exemptDepartmentNos: state.noCheckScopeFriendDeps,
  };
}

export function toFriendCheckInStrategy(config: FriendApprovalConfig): FriendCheckInStrategy {
  if (config.mode === 'none') return 'OPEN';
  if (config.mode === 'partial_exempt') return 'DEPT_FREE';
  return 'APPROVAL';
}

/**
 * friend_ext 在后端是顶层整体替换语义。先复制当前对象，再只替换免审批部门，
 * 避免丢失 public_*_approval、view_scope_*_friend_deps 等已有子字段。
 */
export function mergeFriendExtNoCheckScope(currentFriendExt: unknown, noCheckScopeFriendDeps: unknown): FriendExt {
  const nextFriendExt: FriendExt = isRecord(currentFriendExt) ? structuredClone(currentFriendExt) : {};
  nextFriendExt.no_check_scope_friend_deps = normalizeDepartmentNos(noCheckScopeFriendDeps);
  return nextFriendExt;
}

export function buildFriendApprovalAttributesPatch(
  current: FriendApprovalAttributesSnapshot,
  config: FriendApprovalConfig,
): { friend_ext: FriendExt; friend_check_in_strategy: FriendCheckInStrategy } {
  const departmentNos = config.mode === 'partial_exempt' ? config.exemptDepartmentNos ?? [] : [];
  const friendExt = mergeFriendExtNoCheckScope(current.friend_ext, departmentNos);
  const normalizedDepartmentNos = friendExt.no_check_scope_friend_deps as string[];
  if (config.mode === 'partial_exempt' && normalizedDepartmentNos.length === 0) {
    throw new Error('部分组织免审批时缺少部门编码，无法提交好友审批策略');
  }
  return {
    friend_ext: friendExt,
    friend_check_in_strategy: toFriendCheckInStrategy(config),
  };
}
