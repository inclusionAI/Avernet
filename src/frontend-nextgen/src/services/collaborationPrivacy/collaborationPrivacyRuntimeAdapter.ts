import type {
  CollaborationBot,
  CollaborationPrivacyOverview,
  OrganizationSearchEntry,
  PendingPublication,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';
import {
  getWorkerConfig as fetchWorkerConfig,
  getOrgUser,
  grantTaskClaim,
  listOrgDepts,
  publishBotPublic,
  revokeTaskClaim,
  updateWorkerConfig as saveWorkerConfig,
  type BcsfuseWorkerConfigDto,
  type BcsPublicRequest,
  type BcsPublishResult,
  type CollaborationBotDto,
  type OrgDeptDto,
  type OrgUserDto,
} from '@/services/backendApi';
import { isEnvelopeFailure, type BackendApiEnvelope } from '@/services/backendApi/types';
import { normalizeEmployeeNumber } from '@/utils/employeeNumber';
import { collaborationPrivacyApiAdapter, type CollaborationPrivacyApiAdapter } from './collaborationPrivacyApiAdapter';
import {
  type CollaborationPrivacyGateway,
  type DirectSettingCommand,
  type PublicationCommand,
  type PublicationResult,
} from './collaborationPrivacyGateway';
import { buildFriendApprovalAttributesPatch, mapFriendApprovalAttributesToDomain } from './friendApprovalAttributes';
import {
  mapBotDtoToDomain,
  mapOrgDeptToEntry,
  mapOrgUserToIdentity,
  readDepartmentScopeReferences,
  type DepartmentScopeReference,
} from './mappers';

export interface CollaborationPrivacyRuntimeDependencies {
  apiAdapter: CollaborationPrivacyApiAdapter;
  getOrgUser: typeof getOrgUser;
  listOrgDepts: typeof listOrgDepts;
  publishBotPublic: typeof publishBotPublic;
  taskGrant: {
    grantTaskClaim: typeof grantTaskClaim;
    revokeTaskClaim: typeof revokeTaskClaim;
  };
  getWorkerConfig: typeof fetchWorkerConfig;
  updateWorkerConfig: typeof saveWorkerConfig;
}

function assertWorkerConfigResponse(response: BcsfuseWorkerConfigDto, botId: string): boolean {
  if (!response || response.worker_id !== botId || typeof response.fusion_enable !== 'boolean') {
    throw new Error('Bot 画像公开配置接口返回了无法识别的数据');
  }
  return response.fusion_enable;
}

function assertOrgUserResponse(response: BackendApiEnvelope<OrgUserDto>): OrgUserDto {
  if (isEnvelopeFailure(response) || !response.data) {
    throw new Error(response.message || '当前用户组织信息接口返回异常');
  }
  return response.data;
}

function normalizeOrgUser(dto: OrgUserDto): OrgUserDto {
  return {
    ...dto,
    user_id: normalizeEmployeeNumber(dto.user_id),
  };
}

function assertDepartmentResponse(response: BackendApiEnvelope<OrgDeptDto[]>): OrgDeptDto[] {
  if (isEnvelopeFailure(response) || !Array.isArray(response.data)) {
    throw new Error(response.message || '部门搜索接口返回异常');
  }
  return response.data;
}

function toPublicationRequest(command: PublicationCommand): BcsPublicRequest {
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

function assertPublicationResponse(response: BackendApiEnvelope<BcsPublishResult>): BcsPublishResult {
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

function createPublicationResult(command: PublicationCommand, result: BcsPublishResult): PublicationResult {
  if (command.config.scope === 'none') {
    return { status: 'completed', config: { scope: 'none', organizationPaths: [] } };
  }

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

  if (result.state === 'SKIPPED') {
    // 跳过审批直接通过:工单已直接放行,无审批链接,按本次请求配置生效。
    const config: PublicConfig =
      result.visibility === 'private' ? { scope: 'none', organizationPaths: [] } : structuredClone(command.config);
    return { status: 'completed', config };
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

function applyResolvedDepartmentEntries(
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

async function hydrateDepartmentScopes(
  dtos: CollaborationBotDto[],
  listDepartments: typeof listOrgDepts,
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

async function hydrateProfilePublic(
  bots: CollaborationBot[],
  getWorkerConfig: typeof fetchWorkerConfig,
  signal?: AbortSignal,
): Promise<CollaborationBot[]> {
  const states = await Promise.all(
    bots.map(async (bot) => {
      try {
        const response = await getWorkerConfig(bot.id, signal);
        return { botId: bot.id, enabled: assertWorkerConfigResponse(response, bot.id) };
      } catch (error) {
        if (signal?.aborted || (error as Error).name === 'AbortError') throw error;
        return { botId: bot.id, enabled: false, unavailable: true };
      }
    }),
  );
  const statesByBotId = new Map(states.map((state) => [state.botId, state]));
  return bots.map((bot) => {
    const state = statesByBotId.get(bot.id);
    return {
      ...bot,
      profilePublic: state?.enabled ?? false,
      profilePublicStatus: state?.unavailable ? 'unavailable' : 'ready',
    };
  });
}

function assertFriendApprovalPatchResponse(
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

export function createCollaborationPrivacyRuntimeAdapter(
  dependencies: CollaborationPrivacyRuntimeDependencies = {
    apiAdapter: collaborationPrivacyApiAdapter,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
    taskGrant: { grantTaskClaim, revokeTaskClaim },
    getWorkerConfig: fetchWorkerConfig,
    updateWorkerConfig: saveWorkerConfig,
  },
): CollaborationPrivacyGateway {
  let overview: CollaborationPrivacyOverview | undefined;
  let managedBotSnapshots = new Map<string, CollaborationBotDto>();

  return {
    async loadOverview(userId, signal) {
      const userResponse = await dependencies.getOrgUser(normalizeEmployeeNumber(userId), signal);
      const currentUser = normalizeOrgUser(assertOrgUserResponse(userResponse));
      const managedBots = await dependencies.apiAdapter.listManagedBots(
        { kind: 'bot', user_id: currentUser.user_id },
        signal,
      );
      const physicalBots = managedBots.items.filter((item) => item.kind === 'bot');
      // 部门回显和画像公开配置是两条独立链路；并行启动，避免部门搜索慢/失败时看不到 BCSFuse 请求。
      const baseBots = physicalBots.map(mapBotDtoToDomain);
      const [botsWithDepartments, botsWithProfile] = await Promise.all([
        hydrateDepartmentScopes(physicalBots, dependencies.listOrgDepts, signal),
        hydrateProfilePublic(baseBots, dependencies.getWorkerConfig, signal),
      ]);
      const profileByBotId = new Map(botsWithProfile.map((bot) => [bot.id, bot]));
      const nextOverview: CollaborationPrivacyOverview = {
        currentUser: mapOrgUserToIdentity(currentUser),
        organizationOptions: [],
        bots: botsWithDepartments.map((bot) => {
          const profile = profileByBotId.get(bot.id);
          return profile
            ? { ...bot, profilePublic: profile.profilePublic, profilePublicStatus: profile.profilePublicStatus }
            : bot;
        }),
      };
      managedBotSnapshots = new Map(physicalBots.map((item) => [item.bot_id, structuredClone(item)]));
      overview = nextOverview;
      return structuredClone(nextOverview);
    },

    async refreshManagedBot(botId, signal) {
      if (!overview) throw new Error('协作权限数据尚未加载');
      const dto = await dependencies.apiAdapter.getManagedBot(botId, signal);
      if (dto.kind !== 'bot') throw new Error('目标不是可配置的物理 Bot');
      const previousDto = managedBotSnapshots.get(botId);
      const dtoWithPreservedEngine =
        dto.engine?.trim() || !previousDto?.engine ? dto : { ...dto, engine: previousDto.engine };
      const [refreshedBot] = await hydrateDepartmentScopes([dtoWithPreservedEngine], dependencies.listOrgDepts, signal);
      if (!refreshedBot) throw new Error('Bot 详情接口未返回可配置的 Bot');
      const [refreshedBotWithProfile] = await hydrateProfilePublic(
        [refreshedBot],
        dependencies.getWorkerConfig,
        signal,
      );
      managedBotSnapshots.set(botId, structuredClone(dtoWithPreservedEngine));
      overview.bots = overview.bots.map((bot) => (bot.id === botId ? refreshedBotWithProfile : bot));
      return structuredClone(refreshedBotWithProfile);
    },

    async syncDepartment(userId, signal) {
      const userResponse = await dependencies.getOrgUser(normalizeEmployeeNumber(userId), signal);
      return mapOrgUserToIdentity(normalizeOrgUser(assertOrgUserResponse(userResponse)));
    },

    async searchDepartments(keyword, signal) {
      return assertDepartmentResponse(await dependencies.listOrgDepts({ keyword }, signal)).map(mapOrgDeptToEntry);
    },

    async updateDirectSetting(command: DirectSettingCommand, signal) {
      if (command.setting === 'taskClaimingEnabled') {
        throw new Error('任务认领开关请通过 enable/disableTaskClaim 提交(grant/revoke + PATCH task_claim_mode 双写)');
      }

      if (command.setting === 'dreamModelEnabled') {
        await dependencies.apiAdapter.patchManagedBot(
          command.botId,
          { task_dream_mode: Boolean(command.value) },
          signal,
        );
        return command.value;
      }

      if (command.setting === 'profilePublic') {
        const response = await dependencies.updateWorkerConfig(
          command.botId,
          { fusion_enable: Boolean(command.value) },
          signal,
        );
        return assertWorkerConfigResponse(response, command.botId);
      }
      await dependencies.apiAdapter.patchManagedBot(
        command.botId,
        { status: command.value as 'online' | 'hidden' },
        signal,
      );
      return command.value;
    },

    async submitPublication(command, signal) {
      const currentOverview = overview;
      if (!currentOverview) throw new Error('协作权限数据尚未加载');
      const result = assertPublicationResponse(
        await dependencies.publishBotPublic(
          command.botId,
          currentOverview.currentUser.employeeNumber,
          toPublicationRequest(command),
          signal,
        ),
      );
      return createPublicationResult(command, result);
    },

    async updateFriendApproval(command, signal) {
      if (!overview) throw new Error('协作权限数据尚未加载');
      const currentBot = managedBotSnapshots.get(command.botId);
      if (!currentBot) throw new Error('未找到要更新好友审批策略的 Bot');
      const patch = buildFriendApprovalAttributesPatch(currentBot, command.config);
      const patchedBot = await dependencies.apiAdapter.patchManagedBot(command.botId, patch, signal);
      const confirmed = assertFriendApprovalPatchResponse(patchedBot, patch);
      managedBotSnapshots.set(
        command.botId,
        structuredClone({
          ...patchedBot,
          friend_ext: { ...patch.friend_ext, ...patchedBot.friend_ext },
          friend_check_in_strategy: patchedBot.friend_check_in_strategy,
        }),
      );
      return {
        mode: confirmed.mode,
        exemptOrganizationPaths:
          confirmed.mode === 'partial_exempt' ? structuredClone(command.config.exemptOrganizationPaths) : [],
        exemptDepartmentNos: confirmed.exemptDepartmentNos,
        ...(confirmed.mode === 'partial_exempt' && command.config.exemptOrganizationEntries?.length
          ? { exemptOrganizationEntries: structuredClone(command.config.exemptOrganizationEntries) }
          : {}),
      };
    },
    async enableTaskClaim(botId: string, signal?: AbortSignal) {
      if (!overview) throw new Error('协作权限数据尚未加载');
      const bot = overview.bots.find((item) => item.id === botId);
      if (!bot) throw new Error('未找到目标 Bot');
      const resp = await dependencies.taskGrant.grantTaskClaim({ bcs_bot_id: bot.id }, signal);
      if (isEnvelopeFailure(resp) || !resp.data) throw new Error(resp.message || '任务认领授权失败');
      try {
        await dependencies.apiAdapter.patchManagedBot(botId, { task_claim_mode: true }, signal);
      } catch (error) {
        await dependencies.taskGrant.revokeTaskClaim({ bcs_bot_id: bot.id }, signal).catch(() => undefined);
        throw error;
      }
      const updated: CollaborationBot = { ...bot, taskClaimingEnabled: true, taskClaimStatus: 'authorized' };
      overview.bots = overview.bots.map((item) => (item.id === botId ? updated : item));
      return structuredClone(updated);
    },
    async disableTaskClaim(botId: string, signal?: AbortSignal) {
      if (!overview) throw new Error('协作权限数据尚未加载');
      const bot = overview.bots.find((item) => item.id === botId);
      if (!bot) throw new Error('未找到目标 Bot');
      const resp = await dependencies.taskGrant.revokeTaskClaim({ bcs_bot_id: bot.id }, signal);
      if (isEnvelopeFailure(resp) || !resp.data) throw new Error(resp.message || '任务认领撤销失败');
      try {
        await dependencies.apiAdapter.patchManagedBot(botId, { task_claim_mode: false }, signal);
      } catch (error) {
        await dependencies.taskGrant.grantTaskClaim({ bcs_bot_id: bot.id }, signal).catch(() => undefined);
        throw error;
      }
      const updated: CollaborationBot = { ...bot, taskClaimingEnabled: false, taskClaimStatus: 'unauthorized' };
      overview.bots = overview.bots.map((item) => (item.id === botId ? updated : item));
      return structuredClone(updated);
    },
  };
}
