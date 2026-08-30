import type {
  CollaborationPrivacyOverview,
  PendingPublication,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';
import {
  getOrgUser,
  listOrgDepts,
  publishBotPublic,
  type BcsPublicRequest,
  type BcsPublishResult,
  type OrgDeptDto,
  type OrgUserDto,
} from '@/services/backendApi';
import type { BackendApiEnvelope } from '@/services/backendApi/types';
import { collaborationPrivacyApiAdapter, type CollaborationPrivacyApiAdapter } from './collaborationPrivacyApiAdapter';
import {
  type CollaborationPrivacyGateway,
  type DirectSettingCommand,
  type FriendApprovalCommand,
  type PublicationCommand,
  type PublicationResult,
} from './collaborationPrivacyGateway';
import { mapBotDtoToDomain, mapOrgDeptToEntry, mapOrgUserToIdentity } from './mappers';
import { MockCollaborationPrivacyAdapter } from './mockCollaborationPrivacyAdapter';

interface FriendApprovalDelegate {
  setOverview(overview: CollaborationPrivacyOverview): void;
  updateFriendApproval(command: FriendApprovalCommand, signal?: AbortSignal): Promise<FriendApprovalCommand['config']>;
}

export interface CollaborationPrivacyRuntimeDependencies {
  apiAdapter: CollaborationPrivacyApiAdapter;
  getOrgUser: typeof getOrgUser;
  listOrgDepts: typeof listOrgDepts;
  publishBotPublic: typeof publishBotPublic;
  friendApprovalAdapter: FriendApprovalDelegate;
}

function assertOrgUserResponse(response: BackendApiEnvelope<OrgUserDto>): OrgUserDto {
  if (Number(response.code) !== 200000 || !response.data) {
    throw new Error(response.message || '当前用户组织信息接口返回异常');
  }
  return response.data;
}

function assertDepartmentResponse(response: BackendApiEnvelope<OrgDeptDto[]>): OrgDeptDto[] {
  if (Number(response.code) !== 200000 || !Array.isArray(response.data)) {
    throw new Error(response.message || '部门搜索接口返回异常');
  }
  return response.data;
}

function toPublicationRequest(command: PublicationCommand): BcsPublicRequest {
  const viewDepts =
    command.config.scope === 'restricted'
      ? command.deptEntries?.map((entry) => ({ deptNo: entry.deptNo, deptName: entry.deptName }))
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
  if (Number(response.code) !== 200000 || !response.data || !response.data.success) {
    throw new Error(response.data?.error_msg || response.message || '公开范围变更提交失败');
  }
  return response.data;
}

function createPublicationResult(command: PublicationCommand, result: BcsPublishResult): PublicationResult {
  if (result.state === 'PROCESSING') {
    const publication: PendingPublication = {
      id: result.puid ?? `PENDING-${command.audience}`,
      audience: command.audience,
      target: structuredClone(command.config),
      submittedAt: new Date().toISOString(),
    };
    return { status: 'pending', publication };
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

export function createCollaborationPrivacyRuntimeAdapter(
  dependencies: CollaborationPrivacyRuntimeDependencies = {
    apiAdapter: collaborationPrivacyApiAdapter,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
    friendApprovalAdapter: new MockCollaborationPrivacyAdapter(),
  },
): CollaborationPrivacyGateway {
  let overview: CollaborationPrivacyOverview | undefined;

  return {
    async loadOverview(signal) {
      const [managedBots, userResponse] = await Promise.all([
        dependencies.apiAdapter.listManagedBots({}, signal),
        dependencies.getOrgUser(signal),
      ]);
      const nextOverview: CollaborationPrivacyOverview = {
        currentUser: mapOrgUserToIdentity(assertOrgUserResponse(userResponse)),
        organizationOptions: [],
        bots: managedBots.items.map(mapBotDtoToDomain),
      };
      overview = nextOverview;
      dependencies.friendApprovalAdapter.setOverview(nextOverview);
      return structuredClone(nextOverview);
    },

    async syncDepartment(signal) {
      return mapOrgUserToIdentity(assertOrgUserResponse(await dependencies.getOrgUser(signal)));
    },

    async searchDepartments(keyword, signal) {
      return assertDepartmentResponse(await dependencies.listOrgDepts({ keyword }, signal)).map(mapOrgDeptToEntry);
    },

    async updateDirectSetting(command: DirectSettingCommand, signal) {
      // 四项直设均已落到 managed-bot PATCH：status / visibility / task_claim_mode / task_dream_mode。
      const body =
        command.setting === 'collaborationStatus'
          ? { status: command.value as 'online' | 'hidden' }
          : command.setting === 'profilePublic'
          ? { visibility: command.value ? ('public' as const) : ('protected' as const) }
          : command.setting === 'taskClaimingEnabled'
          ? { task_claim_mode: command.value as boolean }
          : { task_dream_mode: command.value as boolean };
      await dependencies.apiAdapter.patchManagedBot(command.botId, body, signal);
      return command.value;
    },

    async submitPublication(command, signal) {
      const result = assertPublicationResponse(
        await dependencies.publishBotPublic(command.botId, toPublicationRequest(command), signal),
      );
      return createPublicationResult(command, result);
    },

    async updateFriendApproval(command, signal) {
      if (!overview) throw new Error('协作权限数据尚未加载');
      return dependencies.friendApprovalAdapter.updateFriendApproval(command, signal);
    },
  };
}
