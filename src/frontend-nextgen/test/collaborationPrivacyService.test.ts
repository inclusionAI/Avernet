import type { CollaborationPrivacyOverview, PendingPublication } from '../src/domain/collaborationPrivacy/types';
import type {
  CollaborationPrivacyGateway,
  DirectSettingCommand,
  FriendApprovalCommand,
  PublicationCommand,
} from '../src/services/collaborationPrivacy/collaborationPrivacyGateway';
import { CollaborationPrivacyService } from '../src/services/collaborationPrivacy/collaborationPrivacyService';

function createOverview(): CollaborationPrivacyOverview {
  return {
    currentUser: { displayName: '示例用户', employeeNumber: 'SAMPLE-001', departmentPath: ['示例集团'] },
    organizationOptions: [['示例集团', '平台团队']],
    bots: [
      {
        id: 'joined',
        name: '已加入 Bot',
        engine: 'OpenClaw',
        joinedBcn: true,
        collaborationStatus: 'online',
        profilePublic: true,
        taskClaimingEnabled: false,
        dreamModelEnabled: false,
        publication: {
          user: { scope: 'all', organizationPaths: [] },
          bot: { scope: 'restricted', organizationPaths: [['示例集团', '平台团队']] },
        },
        pendingPublications: {},
        friendApproval: { mode: 'all', exemptOrganizationPaths: [] },
      },
      {
        id: 'private',
        name: '私有 Bot',
        engine: 'OpenClaw',
        joinedBcn: true,
        collaborationStatus: 'online',
        profilePublic: false,
        taskClaimingEnabled: false,
        dreamModelEnabled: false,
        publication: { user: { scope: 'none', organizationPaths: [] }, bot: { scope: 'none', organizationPaths: [] } },
        pendingPublications: {},
        friendApproval: { mode: 'none', exemptOrganizationPaths: [] },
      },
      {
        id: 'disabled',
        name: '未加入 Bot',
        engine: 'OpenClaw',
        joinedBcn: false,
        collaborationStatus: 'hidden',
        profilePublic: false,
        taskClaimingEnabled: false,
        dreamModelEnabled: false,
        publication: { user: { scope: 'none', organizationPaths: [] }, bot: { scope: 'none', organizationPaths: [] } },
        pendingPublications: {},
        friendApproval: { mode: 'none', exemptOrganizationPaths: [] },
      },
    ],
  };
}

class FakeGateway implements CollaborationPrivacyGateway {
  overview = createOverview();
  directCommands: DirectSettingCommand[] = [];
  publicationCommands: PublicationCommand[] = [];
  friendCommands: FriendApprovalCommand[] = [];
  signals: Array<AbortSignal | undefined> = [];
  holdPublications = false;
  publicationResolvers: Array<(value: { status: 'pending'; publication: PendingPublication }) => void> = [];
  holdDirectSettings = false;
  directResolvers: Array<(value: DirectSettingCommand['value']) => void> = [];

  async loadOverview(signal?: AbortSignal) {
    this.signals.push(signal);
    return this.overview;
  }
  async syncDepartment(signal?: AbortSignal) {
    this.signals.push(signal);
    return this.overview.currentUser;
  }
  async searchDepartments(keyword: string, signal?: AbortSignal) {
    if (signal?.aborted) throw Object.assign(new Error('Aborted'), { name: 'AbortError' });
    return this.overview.organizationOptions
      .filter((path: string[]) => path.join('/').includes(keyword))
      .map((path: string[]) => ({ deptNo: path.join('/'), path }));
  }
  async updateDirectSetting(command: DirectSettingCommand, signal?: AbortSignal) {
    this.signals.push(signal);
    this.directCommands.push(command);
    if (!this.holdDirectSettings) return command.value;
    return new Promise<DirectSettingCommand['value']>((resolve) => {
      this.directResolvers.push(() => resolve(command.value));
    });
  }
  async submitPublication(command: PublicationCommand, signal?: AbortSignal) {
    this.signals.push(signal);
    this.publicationCommands.push(command);
    const pending: PendingPublication = {
      id: `MOCK-${command.audience}`,
      audience: command.audience,
      target: command.config,
      submittedAt: '2026-08-18T00:00:00.000Z',
    };
    const result = { status: 'pending' as const, publication: pending };
    if (!this.holdPublications) return result;
    return new Promise<typeof result>((resolve) => {
      this.publicationResolvers.push(() => resolve(result));
    });
  }
  async updateFriendApproval(command: FriendApprovalCommand, signal?: AbortSignal) {
    this.signals.push(signal);
    this.friendCommands.push(command);
    return command.config;
  }
}

describe('CollaborationPrivacyService', () => {
  test('BCN 未加入时拒绝任何写操作', async () => {
    const gateway = new FakeGateway();
    const service = new CollaborationPrivacyService(gateway);
    await service.loadOverview();

    await expect(
      service.updateDirectSetting({ botId: 'disabled', setting: 'profilePublic', value: true }),
    ).rejects.toThrow('加入 BCN 后才能修改协作权限');
    expect(gateway.directCommands).toHaveLength(0);
  });

  test('提交公开变更只创建对应 audience pending，不提前改变生效值', async () => {
    const gateway = new FakeGateway();
    const service = new CollaborationPrivacyService(gateway);
    await service.loadOverview();

    const next = await service.submitPublication({
      botId: 'joined',
      audience: 'user',
      config: { scope: 'restricted', organizationPaths: [['示例集团', '平台团队']] },
    });

    expect(next.publication.user).toEqual({ scope: 'all', organizationPaths: [] });
    expect(next.pendingPublications.user?.id).toBe('MOCK-user');
    expect(next.pendingPublications.bot).toBeUndefined();
    expect(gateway.publicationCommands).toHaveLength(1);
  });

  test('restricted 和 partial_exempt 缺少范围时不调用 Gateway', async () => {
    const gateway = new FakeGateway();
    const service = new CollaborationPrivacyService(gateway);
    await service.loadOverview();

    await expect(
      service.submitPublication({
        botId: 'joined',
        audience: 'bot',
        config: { scope: 'restricted', organizationPaths: [] },
      }),
    ).rejects.toThrow('至少选择一个公开组织范围');
    await expect(
      service.updateFriendApproval({
        botId: 'joined',
        config: { mode: 'partial_exempt', exemptOrganizationPaths: [] },
      }),
    ).rejects.toThrow('至少选择一个免审批组织范围');
    expect(gateway.publicationCommands).toHaveLength(0);
    expect(gateway.friendCommands).toHaveLength(0);
  });

  test('双公开均 none 时拒绝修改好友审批策略', async () => {
    const gateway = new FakeGateway();
    const service = new CollaborationPrivacyService(gateway);
    await service.loadOverview();

    await expect(
      service.updateFriendApproval({
        botId: 'private',
        config: { mode: 'all', exemptOrganizationPaths: [] },
      }),
    ).rejects.toThrow('至少开放一种公开范围后才能修改好友审批策略');
  });

  test('同一 audience 存在 pending 时拒绝重复提交', async () => {
    const gateway = new FakeGateway();
    const service = new CollaborationPrivacyService(gateway);
    await service.loadOverview();
    await service.submitPublication({
      botId: 'joined',
      audience: 'user',
      config: { scope: 'none', organizationPaths: [] },
    });

    await expect(
      service.submitPublication({
        botId: 'joined',
        audience: 'user',
        config: { scope: 'none', organizationPaths: [] },
      }),
    ).rejects.toThrow('该公开范围已有待审批变更');
    expect(gateway.publicationCommands).toHaveLength(1);
  });

  test('归一化后配置无变化时不创建工单', async () => {
    const gateway = new FakeGateway();
    const service = new CollaborationPrivacyService(gateway);
    await service.loadOverview();

    await expect(
      service.submitPublication({
        botId: 'joined',
        audience: 'bot',
        config: {
          scope: 'restricted',
          organizationPaths: [
            ['示例集团', '平台团队'],
            ['示例集团', '平台团队'],
          ],
        },
      }),
    ).rejects.toThrow('配置未发生变化，无需提交工单');
    await expect(
      service.updateFriendApproval({
        botId: 'joined',
        config: { mode: 'all', exemptOrganizationPaths: [['不应保留']] },
      }),
    ).rejects.toThrow('配置未发生变化，无需保存');
    expect(gateway.publicationCommands).toHaveLength(0);
    expect(gateway.friendCommands).toHaveLength(0);
  });

  test('同一 audience 的并发提交只允许一个进入 Gateway，不同 audience 可独立提交', async () => {
    const gateway = new FakeGateway();
    gateway.holdPublications = true;
    const service = new CollaborationPrivacyService(gateway);
    await service.loadOverview();

    const userRequest = service.submitPublication({
      botId: 'joined',
      audience: 'user',
      config: { scope: 'none', organizationPaths: [] },
    });
    await expect(
      service.submitPublication({
        botId: 'joined',
        audience: 'user',
        config: { scope: 'restricted', organizationPaths: [['示例集团']] },
      }),
    ).rejects.toThrow('该公开范围正在提交，请勿重复操作');
    const botRequest = service.submitPublication({
      botId: 'joined',
      audience: 'bot',
      config: { scope: 'all', organizationPaths: [] },
    });

    expect(gateway.publicationCommands.map((command) => command.audience)).toEqual(['user', 'bot']);
    gateway.publicationResolvers.splice(0).forEach((resolve) =>
      resolve({
        status: 'pending',
        publication: {
          id: 'MOCK-resolved',
          audience: 'user',
          target: { scope: 'none', organizationPaths: [] },
          submittedAt: '2026-08-18T00:00:00.000Z',
        },
      }),
    );
    await Promise.all([userRequest, botRequest]);
  });

  test('同一直接设置提交中拒绝重复操作', async () => {
    const gateway = new FakeGateway();
    gateway.holdDirectSettings = true;
    const service = new CollaborationPrivacyService(gateway);
    await service.loadOverview();

    const request = service.updateDirectSetting({ botId: 'joined', setting: 'profilePublic', value: false });
    await expect(
      service.updateDirectSetting({ botId: 'joined', setting: 'profilePublic', value: false }),
    ).rejects.toThrow('该设置正在提交，请勿重复操作');
    expect(gateway.directCommands).toHaveLength(1);
    gateway.directResolvers.splice(0).forEach((resolve) => resolve(false));
    await request;
  });

  test('所有接口用例向 Gateway 透传取消信号', async () => {
    const gateway = new FakeGateway();
    const service = new CollaborationPrivacyService(gateway);
    const signal = new AbortController().signal;

    await service.loadOverview(signal);
    await service.syncDepartment(signal);
    await service.updateDirectSetting({ botId: 'joined', setting: 'profilePublic', value: false }, signal);
    await service.submitPublication(
      {
        botId: 'joined',
        audience: 'user',
        config: { scope: 'none', organizationPaths: [] },
      },
      signal,
    );
    await service.updateFriendApproval(
      {
        botId: 'joined',
        config: { mode: 'none', exemptOrganizationPaths: [] },
      },
      signal,
    );

    expect(gateway.signals).toHaveLength(5);
    gateway.signals.forEach((received) => expect(received).toBe(signal));
  });
});
