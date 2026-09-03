import type {
  CollaborationBot,
  CurrentUserIdentity,
  FriendApprovalConfig,
  OrganizationPath,
  OrganizationSearchEntry,
  PublicAudience,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';
import type { CollaborationBotDto, OrgDeptDto, OrgUserDto } from '@/services/backendApi';
import { normalizeEmployeeNumber } from '@/utils/employeeNumber';
import { mapFriendApprovalAttributesToDomain, mapPublicationApprovalsFromFriendExt } from './friendApprovalAttributes';

export interface DepartmentScopeReference {
  deptNo: string;
  path?: OrganizationPath;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalizePath(value: unknown): OrganizationPath | undefined {
  const rawPath = Array.isArray(value) ? value : [];
  const path = rawPath
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter(Boolean);
  return path.length > 0 ? path : undefined;
}

function readDisplayPath(value: unknown): OrganizationPath | undefined {
  if (!isRecord(value)) return undefined;
  const arrayPath = normalizePath(value.path);
  if (arrayPath) return arrayPath;
  const name = String(value.deptName ?? value.dept_name ?? value.name ?? '').trim();
  return name ? [name] : undefined;
}

function readDepartmentNo(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (!isRecord(value)) return '';
  return String(value.deptNo ?? value.dept_no ?? '').trim();
}

export function readDepartmentScopeReferences(friendExt: unknown, key: string): DepartmentScopeReference[] {
  if (!isRecord(friendExt) || !Array.isArray(friendExt[key])) return [];
  const references = friendExt[key]
    .map((value) => ({
      deptNo: readDepartmentNo(value),
      path: readDisplayPath(value),
    }))
    .filter((reference) => reference.deptNo || reference.path);
  return [
    ...new Map(
      references.map((reference) => [reference.deptNo || reference.path?.join('\u0000') || '', reference]),
    ).values(),
  ];
}

/** 将 /openapi/v1/org/user 的完整部门名称作为单段原文保留。 */
export function mapOrgUserToIdentity(dto: OrgUserDto): CurrentUserIdentity {
  return {
    displayName: dto.display_name,
    employeeNumber: normalizeEmployeeNumber(dto.user_id),
    departmentPath: dto.dept_name ? [dto.dept_name] : [],
    lastSyncedAt: new Date().toISOString(),
  };
}

/** 部门候选项同时携带部门编码，并原样保留后端 dept_name 的分隔符。 */
export function mapOrgDeptToEntry(dto: OrgDeptDto): OrganizationSearchEntry {
  const departmentName = dto.dept_name.trim();
  return {
    deptNo: dto.dept_no,
    path: departmentName ? [departmentName] : [],
  };
}

/** @deprecated 使用 mapOrgDeptToEntry 代替，携带 deptNo 供 API 调用。 */
export function mapOrgDeptToPath(dto: OrgDeptDto): OrganizationPath {
  return mapOrgDeptToEntry(dto).path;
}

function mapCurrentPublication(
  visibility: CollaborationBotDto['visibility'],
  references: DepartmentScopeReference[],
): PublicConfig {
  const entries = references
    .filter((reference): reference is DepartmentScopeReference & { path: OrganizationPath } => Boolean(reference.path))
    .map((reference) => ({ deptNo: reference.deptNo, path: reference.path }));
  const organizationPaths = entries.map((entry) => entry.path);
  const scope =
    visibility === 'public'
      ? references.length > 0
        ? 'restricted'
        : 'all'
      : visibility === 'protected'
      ? 'restricted'
      : 'none';
  return {
    scope,
    organizationPaths: scope === 'restricted' ? organizationPaths : [],
    ...(scope === 'restricted' && entries.length > 0 ? { organizationEntries: entries } : {}),
  };
}

function mapCurrentPublications(dto: CollaborationBotDto): Record<PublicAudience, PublicConfig> {
  return {
    user: mapCurrentPublication(
      dto.user_visibility,
      readDepartmentScopeReferences(dto.friend_ext, 'view_scope_user_friend_deps'),
    ),
    bot: mapCurrentPublication(
      dto.visibility,
      readDepartmentScopeReferences(dto.friend_ext, 'view_scope_agent_friend_deps'),
    ),
  };
}

function defaultFriendApproval(): FriendApprovalConfig {
  return { mode: 'all', exemptOrganizationPaths: [] };
}

function mapEngineLabel(engine?: string, providerName?: string) {
  const rawEngine = engine?.trim() || providerName?.trim();
  if (!rawEngine) return 'unknown';
  const normalized = rawEngine.toLowerCase();
  if (normalized === 'openclaw') return 'OpenClaw';
  if (normalized === 'claude_code' || normalized === 'claudecode') return 'Claude Code';
  if (normalized === 'teclaw') return 'TEClaw';
  if (normalized === 'hermes') return 'Hermes';
  return rawEngine;
}

/** 将 /openapi/v1/collaboration/bots/mine 的 Bot DTO 映射到协作权限域。 */
export function mapBotDtoToDomain(dto: CollaborationBotDto): CollaborationBot {
  return {
    id: dto.bot_id,
    name: dto.name ?? '',
    engine: mapEngineLabel(dto.engine, dto.provider?.name),
    joinedBcn: true,
    collaborationStatus: dto.status === 'online' || dto.status === 'hidden' ? dto.status : 'offline',
    profilePublic: false, // Bot 画像公开由 BCSFuse fusion_enable 独立控制，不能从协作公开范围 visibility 推断。
    taskClaimingEnabled: dto.task_claim_mode ?? false,
    taskClaimStatus: dto.task_claim_mode ?? false ? 'authorized' : 'unauthorized',
    dreamModelEnabled: dto.task_dream_mode ?? false,
    publication: mapCurrentPublications(dto),
    pendingPublications: mapPublicationApprovalsFromFriendExt(dto.friend_ext),
    friendApproval: dto.friend_check_in_strategy
      ? mapFriendApprovalAttributesToDomain({
          friend_ext: dto.friend_ext,
          friend_check_in_strategy: dto.friend_check_in_strategy,
        })
      : defaultFriendApproval(),
  };
}
