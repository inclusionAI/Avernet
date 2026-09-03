import type {
  CollaborationBot,
  OrganizationSearchEntry,
  PendingPublication,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';
import {
  type BcsPublicRequest,
  type BcsPublishResult,
  type CollaborationBotDto,
  type OrgDeptDto,
  type OrgUserDto,
} from '@/services/backendApi';
import { isEnvelopeFailure, type BackendApiEnvelope } from '@/services/backendApi/types';
import { type PublicationResult } from './collaborationPrivacyGateway';
import { buildFriendApprovalAttributesPatch, mapFriendApprovalAttributesToDomain } from './friendApprovalAttributes';
import {
  mapBotDtoToDomain,
  mapOrgDeptToEntry,
  readDepartmentScopeReferences,
  type DepartmentScopeReference,
} from './mappers';

export function assertOrgUserResponse(response: BackendApiEnvelope<OrgUserDto>): OrgUserDto {
  if (isEnvelopeFailure(response) || !response.data) {
    throw new Error(response.message || '当前用户组织信息接口返回异常');
  }
  return response.data;
}

export function assertDepartmentResponse(response: BackendApiEnvelope<OrgDeptDto[]>): OrgDeptDto[] {
  if (isEnvelopeFailure(response) || !Array.isArray(response.data)) {
    throw new Error(response.message || '部门搜索接口返回异常');
  }
  return response.data;
}

export function toPublicationRequest(command: {
  audience: 'user' | 'bot';
  config: PublicConfig;
  deptEntries?: { deptNo: string; deptName: string }[];
}): BcsPublicRequest {
  const configuredEntries =
    command.deptEntries ??
    command.config.organizationEntries?.map((entry) => ({
      deptNo: entry.deptNo,
      deptName: entry.path.join(' / '),
    }));
  const viewDepts =
    command.config.scope === 'restricted'
      ? configuredEntries?.map((entry) => ({ deptNo: entry.deptNo, deptName: entry.deptName }))
      : undefined;

  if (
    command.config.scope === 'restricted' &&
    (!viewDepts?.length || viewDepts.some((entry) => !entry.deptNo.trim()))
  ) {
    throw new Error('组织范围缺少部门编码，无法提交公开范围变更');
  }

  return {
    public_scope: command.audience === 'user' ? 'user' : 'agent',
    visibility: command.config.scope === 'none' ? 'private' : 'public',
    view_depts: viewDepts?.length ? viewDepts : null,
  };
}

export function assertPublicationResponse(response: BackendApiEnvelope<BcsPublishResult>): BcsPublishResult {
  if (isEnvelopeFailure(response) || !response.data || !response.data.success) {
    throw new Error(response.data?.error_msg || response.message || '公开范围变更提交失败');
  }
  return response.data;
}

function toSafeApprovalUrl(value: string | null | undefined): string | undefined {
  const url = value?.trim();
  if (!url) return undefined;
  if (url.startsWith('/') && !url.startsWith('//')) return url;

  try {
    return new URL(url).protocol === 'https:' ? url : undefined;
  } catch {
    return undefined;
  }
}

export function createPublicationResult(
  command: { audience: 'user' | 'bot'; config: PublicConfig },
  result: BcsPublishResult,
): PublicationResult {
  if (result.state === 'CREATED' || result.state === 'PROCESSING') {
    const approvalUrl = toSafeApprovalUrl(result.approval_url);
    const publication: PendingPublication = {
      id: result.puid ?? `PENDING-${command.audience}`,
      audience: command.audience,
      target: structuredClone(command.config),
      submittedAt: new Date().toISOString(),
      ...(approvalUrl ? { approvalUrl } : {}),
    };
    return { status: 'pending', publication };
  }

  if (command.config.scope === 'none' && (result.state === null || result.state === undefined)) {
    return { status: 'completed', config: { scope: 'none', organizationPaths: [] } };
  }
  if (result.state !== 'COMPLETED') {
    throw new Error('公开范围接口返回了无法识别的终态');
  }
  if (result.last_operate === 'DISAGREE' || result.last_operate === 'CANCEL') {
    throw new Error('公开范围变更未通过，当前生效配置保持不变');
  }

  const config: PublicConfig =
    result.visibility === 'private' ? { scope: 'none', organizationPaths: [] } : structuredClone(command.config);
  return { status: 'completed', config };
}

interface BotDepartmentReferences {
  user: DepartmentScopeReference[];
  bot: DepartmentScopeReference[];
  friendApproval: DepartmentScopeReference[];
}

function readBotDepartmentReferences(dto: CollaborationBotDto): BotDepartmentReferences {
  return {
    user: readDepartmentScopeReferences(dto.friend_ext, 'view_scope_user_friend_deps'),
    bot: readDepartmentScopeReferences(dto.friend_ext, 'view_scope_agent_friend_deps'),
    friendApproval: readDepartmentScopeReferences(dto.friend_ext, 'no_check_scope_friend_deps'),
  };
}

function resolveDepartmentEntries(
  references: DepartmentScopeReference[],
  resolvedByDepartmentNo: Map<string, OrganizationSearchEntry>,
): OrganizationSearchEntry[] {
  const entries = references
    .map((reference) =>
      reference.path
        ? { deptNo: reference.deptNo, path: reference.path }
        : resolvedByDepartmentNo.get(reference.deptNo),
    )
    .filter((entry): entry is OrganizationSearchEntry => Boolean(entry?.path.length));
  return [...new Map(entries.map((entry) => [entry.deptNo || entry.path.join('\u0000'), entry])).values()];
}

export function applyResolvedDepartmentEntries(
  bot: CollaborationBot,
  references: BotDepartmentReferences,
  resolvedByDepartmentNo: Map<string, OrganizationSearchEntry>,
): CollaborationBot {
  const userEntries = resolveDepartmentEntries(references.user, resolvedByDepartmentNo);
  const botEntries = resolveDepartmentEntries(references.bot, resolvedByDepartmentNo);
  const friendEntries = resolveDepartmentEntries(references.friendApproval, resolvedByDepartmentNo);
  return {
    ...bot,
    publication: {
      user: {
        ...bot.publication.user,
        organizationPaths: bot.publication.user.scope === 'restricted' ? userEntries.map((entry) => entry.path) : [],
        ...(bot.publication.user.scope === 'restricted' && userEntries.length > 0
          ? { organizationEntries: userEntries }
          : {}),
      },
      bot: {
        ...bot.publication.bot,
        organizationPaths: bot.publication.bot.scope === 'restricted' ? botEntries.map((entry) => entry.path) : [],
        ...(bot.publication.bot.scope === 'restricted' && botEntries.length > 0
          ? { organizationEntries: botEntries }
          : {}),
      },
    },
    friendApproval:
      bot.friendApproval.mode === 'partial_exempt'
        ? {
            ...bot.friendApproval,
            exemptOrganizationPaths: friendEntries.map((entry) => entry.path),
            ...(friendEntries.length > 0 ? { exemptOrganizationEntries: friendEntries } : {}),
          }
        : bot.friendApproval,
  };
}

export async function hydrateDepartmentScopes(
  dtos: CollaborationBotDto[],
  listDepartments: (params: { keyword: string }, signal?: AbortSignal) => Promise<BackendApiEnvelope<OrgDeptDto[]>>,
  signal?: AbortSignal,
): Promise<CollaborationBot[]> {
  const referencesByBot = new Map(dtos.map((dto) => [dto.bot_id, readBotDepartmentReferences(dto)]));
  const allReferences = [...referencesByBot.values()].flatMap((references) => [
    ...references.user,
    ...references.bot,
    ...references.friendApproval,
  ]);
  const resolvedByDepartmentNo = new Map<string, OrganizationSearchEntry>();
  allReferences.forEach((reference) => {
    if (reference.deptNo && reference.path) {
      resolvedByDepartmentNo.set(reference.deptNo, { deptNo: reference.deptNo, path: reference.path });
    }
  });
  const departmentNos = [...new Set(allReferences.map((reference) => reference.deptNo).filter(Boolean))].filter(
    (departmentNo) => !resolvedByDepartmentNo.has(departmentNo),
  );

  await Promise.all(
    departmentNos.map(async (departmentNo) => {
      try {
        const departments = assertDepartmentResponse(await listDepartments({ keyword: departmentNo }, signal));
        const exact = departments.find((department) => department.dept_no === departmentNo);
        if (exact) resolvedByDepartmentNo.set(departmentNo, mapOrgDeptToEntry(exact));
      } catch (error) {
        if (signal?.aborted || (error as Error).name === 'AbortError') throw error;
      }
    }),
  );

  return dtos.map((dto) =>
    applyResolvedDepartmentEntries(
      mapBotDtoToDomain(dto),
      referencesByBot.get(dto.bot_id) ?? { user: [], bot: [], friendApproval: [] },
      resolvedByDepartmentNo,
    ),
  );
}

export function assertFriendApprovalPatchResponse(
  bot: CollaborationBotDto,
  expectedPatch: ReturnType<typeof buildFriendApprovalAttributesPatch>,
) {
  const friendExt = bot.friend_ext;
  if (
    !friendExt ||
    typeof friendExt !== 'object' ||
    Array.isArray(friendExt) ||
    !Object.prototype.hasOwnProperty.call(friendExt, 'no_check_scope_friend_deps') ||
    bot.friend_check_in_strategy === undefined
  ) {
    throw new Error('Bot 更新接口未返回已生效的好友审批策略');
  }

  const confirmed = mapFriendApprovalAttributesToDomain({
    friend_ext: friendExt,
    friend_check_in_strategy: bot.friend_check_in_strategy,
  });
  const expectedDepartmentNos = expectedPatch.friend_ext.no_check_scope_friend_deps as string[];
  if (
    bot.friend_check_in_strategy !== expectedPatch.friend_check_in_strategy ||
    JSON.stringify(confirmed.exemptDepartmentNos) !== JSON.stringify(expectedDepartmentNos)
  ) {
    throw new Error('Bot 更新接口未返回已生效的好友审批策略');
  }
  return confirmed;
}
