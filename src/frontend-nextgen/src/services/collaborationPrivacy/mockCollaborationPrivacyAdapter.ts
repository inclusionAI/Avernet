import { mapOverviewTransport, type CollaborationPrivacyOverviewTransport } from '@/domain/collaborationPrivacy/mapper';
import type { CollaborationPrivacyOverview, OrganizationSearchEntry } from '@/domain/collaborationPrivacy/types';
import type {
  CollaborationPrivacyGateway,
  DirectSettingCommand,
  FriendApprovalCommand,
  PublicationCommand,
} from './collaborationPrivacyGateway';

const MOCK_ENDPOINT = '/api/mock/collaboration-privacy/overview';
const delay = (duration: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, duration);
  });

export class MockCollaborationPrivacyAdapter implements CollaborationPrivacyGateway {
  private overview?: CollaborationPrivacyOverview;

  setOverview(overview: CollaborationPrivacyOverview) {
    this.overview = structuredClone(overview);
  }

  async loadOverview(signal?: AbortSignal): Promise<CollaborationPrivacyOverview> {
    const response = await fetch(MOCK_ENDPOINT, { signal });
    if (!response.ok) throw new Error('协作权限 Mock 数据加载失败');
    this.overview = mapOverviewTransport((await response.json()) as CollaborationPrivacyOverviewTransport);
    return structuredClone(this.overview);
  }

  private requireOverview(): CollaborationPrivacyOverview {
    if (!this.overview) throw new Error('协作权限数据尚未加载');
    return this.overview;
  }

  async syncDepartment() {
    await delay(350);
    return { ...structuredClone(this.requireOverview().currentUser), lastSyncedAt: new Date().toISOString() };
  }

  async searchDepartments(keyword: string, signal?: AbortSignal): Promise<OrganizationSearchEntry[]> {
    if (signal?.aborted) throw Object.assign(new Error('Aborted'), { name: 'AbortError' });
    const options = this.requireOverview().organizationOptions;
    return options.filter((path) => path.join('/').includes(keyword)).map((path) => ({ deptNo: path.join('/'), path }));
  }

  async updateDirectSetting(command: DirectSettingCommand) {
    await delay(300);
    const bot = this.requireOverview().bots.find((item) => item.id === command.botId);
    if (!bot) throw new Error('未找到目标 Bot');
    if (command.setting === 'collaborationStatus') bot.collaborationStatus = command.value as 'online' | 'hidden';
    else if (command.setting === 'profilePublic') bot.profilePublic = Boolean(command.value);
    else if (command.setting === 'taskClaimingEnabled') bot.taskClaimingEnabled = Boolean(command.value);
    else bot.dreamModelEnabled = Boolean(command.value);
    return command.value;
  }

  async submitPublication(command: PublicationCommand) {
    await delay(450);
    const pending = {
      id: `MOCK-${Date.now().toString().slice(-8)}`,
      audience: command.audience,
      target: structuredClone(command.config),
      submittedAt: new Date().toISOString(),
    };
    const bot = this.requireOverview().bots.find((item) => item.id === command.botId);
    if (!bot) throw new Error('未找到目标 Bot');
    bot.pendingPublications[command.audience] = pending;
    return { status: 'pending' as const, publication: pending };
  }

  async updateFriendApproval(command: FriendApprovalCommand) {
    await delay(350);
    const bot = this.requireOverview().bots.find((item) => item.id === command.botId);
    if (!bot) throw new Error('未找到目标 Bot');
    bot.friendApproval = structuredClone(command.config);
    return structuredClone(command.config);
  }
}
