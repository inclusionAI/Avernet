import type {
  CurrentUserIdentity,
  OrganizationPath,
  OrganizationSearchEntry,
} from '@/domain/collaborationPrivacy/types';
import type { OrgDeptDto, OrgUserDto } from '@/services/backendApi';

/**
 * 将 /openapi/v1/org/user 返回的 OrgUserDto 映射为协作权限域的 CurrentUserIdentity。
 *
 * 字段映射：
 * - displayName    ← display_name
 * - employeeNumber ← user_id
 * - departmentPath ← dept_name 原样保留为单段展示值
 * - lastSyncedAt   ← 当前时间
 */
export function mapOrgUserToIdentity(dto: OrgUserDto): CurrentUserIdentity {
  return {
    displayName: dto.display_name,
    employeeNumber: dto.user_id,
    departmentPath: dto.dept_name ? [dto.dept_name] : [],
    lastSyncedAt: new Date().toISOString(),
  };
}

/**
 * 将 /openapi/v1/org/dept 返回的 OrgDeptDto 映射为组织路径（用于部门搜索候选项）。
 */
/** 部门候选项，同时携带部门编码和展示路径。 */
export function mapOrgDeptToEntry(dto: OrgDeptDto): OrganizationSearchEntry {
  return {
    deptNo: dto.dept_no,
    path: dto.dept_name
      .split('-')
      .map((segment) => segment.trim())
      .filter(Boolean),
  };
}

/**
 * @deprecated 使用 mapOrgDeptToEntry 代替，携带 deptNo 供 API 调用。
 */
export function mapOrgDeptToPath(dto: OrgDeptDto): OrganizationPath {
  return mapOrgDeptToEntry(dto).path;
}

/**
 * 将 /openapi/v1/collaboration/bots/mine 返回的 CollaborationBotDto 映射为协作权限域的 CollaborationBot。
 *
 * 本期只映射 DTO 已确认字段；publication、pendingPublications、friendApproval 使用安全默认值，
 * taskClaimingEnabled / dreamModelEnabled 取自 DTO task_claim_mode / task_dream_mode。
 */
import type {
  CollaborationBot,
  FriendApprovalConfig,
  PendingPublication,
  PublicAudience,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';
import type { CollaborationBotDto } from '@/services/backendApi';

function defaultPublicConfig(): Record<PublicAudience, PublicConfig> {
  return { user: { scope: 'none', organizationPaths: [] }, bot: { scope: 'none', organizationPaths: [] } };
}

function defaultFriendApproval(): FriendApprovalConfig {
  return { mode: 'none', exemptOrganizationPaths: [] };
}

export function mapBotDtoToDomain(dto: CollaborationBotDto): CollaborationBot {
  return {
    id: dto.bot_id,
    name: dto.name ?? '',
    engine: dto.provider?.name ?? 'unknown',
    joinedBcn: true,
    collaborationStatus: dto.status === 'online' || dto.status === 'hidden' ? dto.status : 'offline',
    profilePublic: dto.visibility === 'public',
    taskClaimingEnabled: Boolean(dto.task_claim_mode),
    dreamModelEnabled: Boolean(dto.task_dream_mode),
    publication: defaultPublicConfig(),
    pendingPublications: {} as Partial<Record<PublicAudience, PendingPublication>>,
    friendApproval: defaultFriendApproval(),
  };
}
