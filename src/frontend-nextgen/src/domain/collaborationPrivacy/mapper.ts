import { normalizeOrganizationPaths, normalizePublicConfig, validateFriendApproval } from './policies';
import type {
  CollaborationPrivacyOverview,
  CollaborationStatus,
  FriendApprovalMode,
  PublicAudience,
  PublicScope,
} from './types';

interface PublicConfigTransport {
  scope?: string;
  organization_paths?: string[][];
}
interface PendingTransport {
  work_order_id?: string;
  audience?: string;
  target?: PublicConfigTransport;
  submitted_at?: string;
}
export interface CollaborationPrivacyOverviewTransport {
  current_user?: { display_name?: string; employee_no?: string; department_path?: string[]; last_synced_at?: string };
  organization_options?: string[][];
  bots?: Array<{
    bot_id?: string;
    bot_name?: string;
    engine_name?: string;
    joined_bcn?: boolean;
    collaboration_status?: string;
    profile_public?: boolean;
    task_claiming_enabled?: boolean;
    dream_model_enabled?: boolean;
    publication?: Partial<Record<PublicAudience, PublicConfigTransport>>;
    friend_approval?: { mode?: string; exempt_organization_paths?: string[][] };
    pending_publications?: Partial<Record<PublicAudience, PendingTransport>>;
  }>;
}

const scopes: PublicScope[] = ['none', 'all', 'restricted'];
const approvalModes: FriendApprovalMode[] = ['none', 'all', 'partial_exempt'];
const statuses: CollaborationStatus[] = ['online', 'hidden', 'offline'];
function asScope(value?: string): PublicScope { return scopes.includes(value as PublicScope) ? value as PublicScope : 'none'; }
function asApprovalMode(value?: string): FriendApprovalMode {
  return approvalModes.includes(value as FriendApprovalMode) ? value as FriendApprovalMode : 'none';
}
function asStatus(value?: string): CollaborationStatus {
  return statuses.includes(value as CollaborationStatus) ? value as CollaborationStatus : 'offline';
}
function required(value: string | undefined, label: string): string {
  if (!value?.trim()) throw new Error(`Mock 数据缺少 ${label}`);
  return value.trim();
}

export function mapOverviewTransport(transport: CollaborationPrivacyOverviewTransport): CollaborationPrivacyOverview {
  const user = transport.current_user;
  if (!user) throw new Error('Mock 数据缺少当前用户');
  return {
    currentUser: {
      displayName: required(user.display_name, '用户名称'),
      employeeNumber: required(user.employee_no, '用户工号'),
      departmentPath: normalizeOrganizationPaths([user.department_path ?? []])[0] ?? [],
      lastSyncedAt: user.last_synced_at,
    },
    organizationOptions: normalizeOrganizationPaths(transport.organization_options ?? []),
    bots: (transport.bots ?? []).map((bot) => {
      const publication = (['user', 'bot'] as PublicAudience[]).reduce((result, audience) => {
        const current = bot.publication?.[audience];
        result[audience] = normalizePublicConfig({
          scope: asScope(current?.scope),
          organizationPaths: current?.organization_paths ?? [],
        });
        return result;
      }, {} as CollaborationPrivacyOverview['bots'][number]['publication']);
      const pendingPublications = (['user', 'bot'] as PublicAudience[]).reduce((result, audience) => {
        const pending = bot.pending_publications?.[audience];
        if (pending?.work_order_id && pending.target) {
          result[audience] = {
            id: pending.work_order_id,
            audience,
            target: normalizePublicConfig({
              scope: asScope(pending.target.scope),
              organizationPaths: pending.target.organization_paths ?? [],
            }),
            submittedAt: pending.submitted_at ?? '',
          };
        }
        return result;
      }, {} as CollaborationPrivacyOverview['bots'][number]['pendingPublications']);
      return {
        id: required(bot.bot_id, 'Bot ID'),
        name: required(bot.bot_name, 'Bot 名称'),
        engine: required(bot.engine_name, 'Bot 引擎'),
        joinedBcn: Boolean(bot.joined_bcn),
        collaborationStatus: asStatus(bot.collaboration_status),
        profilePublic: Boolean(bot.profile_public),
        taskClaimingEnabled: Boolean(bot.task_claiming_enabled),
        dreamModelEnabled: Boolean(bot.dream_model_enabled),
        publication,
        pendingPublications,
        friendApproval: validateFriendApproval({
          mode: asApprovalMode(bot.friend_approval?.mode),
          exemptOrganizationPaths: bot.friend_approval?.exempt_organization_paths ?? [],
        }),
      };
    }),
  };
}
