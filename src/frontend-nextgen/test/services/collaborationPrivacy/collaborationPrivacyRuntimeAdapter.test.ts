import type { BcsPublishResult, OrgUserDto } from '@/services/backendApi';
import { createCollaborationPrivacyApiAdapter } from '@/services/collaborationPrivacy/collaborationPrivacyApiAdapter';
import type { PublicationCommand } from '@/services/collaborationPrivacy/collaborationPrivacyGateway';
import { createCollaborationPrivacyRuntimeAdapter } from '@/services/collaborationPrivacy/collaborationPrivacyRuntimeAdapter';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

const orgUser: OrgUserDto = {
  user_id: '447147',
  username: 'testuser',
  display_name: '测试用户',
  full_name: '测试用户',
  tenant: 'ant',
  dept_no: 'A4195',
  dept_name: '全网巡检',
  dept_path: '00001/A4195',
};

/**
 * 构造一个已加载空 Overview 的运行期适配器,便于只聚焦 submitPublication 的终态映射。
 * publishBotPublic 固定返回给定结果,不触达真实网络。
 */
function createAdapterWithPublishResult(publishResult: BcsPublishResult) {
  const listMyBots = jest.fn<(...args: any[]) => any>();
  const getCollaborationBot = jest.fn<(...args: any[]) => any>();
  const patchCollaborationBot = jest.fn<(...args: any[]) => any>();
  const apiAdapter = createCollaborationPrivacyApiAdapter({
    listMyBots,
    getCollaborationBot,
    patchCollaborationBot,
  });
  listMyBots.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 20 } });

  const getOrgUser = jest.fn<(...args: any[]) => any>();
  getOrgUser.mockResolvedValue({ success: true, data: orgUser });
  const listOrgDepts = jest.fn<(...args: any[]) => any>();
  const publishBotPublic = jest.fn<(...args: any[]) => any>();
  publishBotPublic.mockResolvedValue({ success: true, data: publishResult });
  const getWorkerConfig = jest.fn<(...args: any[]) => any>();
  const updateWorkerConfig = jest.fn<(...args: any[]) => any>();
  const grantTaskClaim = jest.fn<(...args: any[]) => any>();
  const revokeTaskClaim = jest.fn<(...args: any[]) => any>();

  const adapter = createCollaborationPrivacyRuntimeAdapter({
    apiAdapter,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
    taskGrant: { grantTaskClaim, revokeTaskClaim },
    getWorkerConfig,
    updateWorkerConfig,
  });
  return { adapter, publishBotPublic };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('collaborationPrivacyRuntimeAdapter.submitPublication', () => {
  it('把 SKIPPED 终态映射为已完成(跳过审批直接通过),且不携带审批链接', async () => {
    const { adapter } = createAdapterWithPublishResult({
      success: true,
      puid: 'pub-skip-1',
      approval_url: null,
      state: 'SKIPPED',
      last_operate: null,
      visibility: 'public',
      visibility_field: null,
      error_msg: null,
    });
    await adapter.loadOverview('447147');

    const command: PublicationCommand = {
      botId: 'bot-1',
      audience: 'user',
      config: { scope: 'all', organizationPaths: [] },
    };
    const result = await adapter.submitPublication(command);

    expect(result).toEqual({ status: 'completed', config: { scope: 'all', organizationPaths: [] } });
    expect((result as { approvalUrl?: string }).approvalUrl).toBeUndefined();
  });

  it('限制公开范围遇到 SKIPPED 时,按本次请求的受限配置直接生效', async () => {
    const { adapter } = createAdapterWithPublishResult({
      success: true,
      puid: 'pub-skip-2',
      approval_url: '',
      state: 'SKIPPED',
      last_operate: null,
      visibility: 'public',
      visibility_field: 'view',
      error_msg: null,
    });
    await adapter.loadOverview('447147');

    const result = await adapter.submitPublication({
      botId: 'bot-1',
      audience: 'bot',
      config: { scope: 'restricted', organizationPaths: [] },
      deptEntries: [{ deptNo: 'A4195', deptName: '全网巡检' }],
    });

    expect(result).toEqual({ status: 'completed', config: { scope: 'restricted', organizationPaths: [] } });
    expect((result as { approvalUrl?: string }).approvalUrl).toBeUndefined();
  });
});
