/** @jest-environment jsdom */
import { BotGeneralConfigDialog } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog';
import { applyExternalViewScopeToBot } from '@/domain/collaborationPrivacy/externalBotVisibility';
import type { CollaborationBot } from '@/domain/collaborationPrivacy/types';
import * as identityModule from '@/hooks/useHumanIdentity';
import type { CollaborationBotDto } from '@/services/backendApi';
import { externalBotToGeneralConfig } from '@/services/botWorkshop/externalBotService';
import { collaborationPrivacyService } from '@/services/collaborationPrivacy';
import { mapBotDtoToDomain } from '@/services/collaborationPrivacy/mappers';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getGeneralConfigPublishApprovalEnabled: () => ({ value: false }),
    getRestrictedPublicationScopeEnabled: () => ({ value: false }),
  }),
}));

const row = externalBotToGeneralConfig({ id: 'external-1', name: '外部助手' });

beforeEach(() => {
  useCollaborationPrivacyStore.getState().reset();
  jest.spyOn(identityModule, 'useHumanIdentity').mockReturnValue({
    status: 'ready',
    identity: { userId: 'user-1', displayName: '用户', online: true },
  });
});
afterEach(() => jest.restoreAllMocks());

it.each<[CollaborationBotDto['visibility'], Record<string, unknown>, string]>([
  ['private', {}, '不可见'],
  ['public', {}, '全部可见'],
  ['protected', {}, '全部可见'],
  [
    'protected',
    { view_scope_agent_friend_deps: [{ deptNo: 'D1', deptName: '研发' }] },
    '限定组织可申请 · 1 个组织范围',
  ],
])('外部 Bot 通用配置弹窗按外部归一化展示 %s / %j，且仅开放支持的配置', async (visibility, friendExt, label) => {
  // 服务边界收到的是运行期外部归一化结果；此处用生产归一器构造夹具，避免手写映射漂移。
  const dto: CollaborationBotDto = {
    kind: 'bot',
    bot_id: row.id,
    name: row.name,
    status: 'online',
    visibility,
    user_visibility: 'private',
    friend_ext: friendExt,
  };
  const bot = applyExternalViewScopeToBot(mapBotDtoToDomain(dto), dto);
  jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue({
    currentUser: { displayName: '用户', employeeNumber: 'user-1', departmentPath: [] },
    organizationOptions: [],
    bots: [bot],
  });
  render(<BotGeneralConfigDialog bot={row} isExternalBot onClose={jest.fn()} />);
  await screen.findByText('参与协作群聊');
  expect(screen.queryByText('对用户可见性')).not.toBeInTheDocument();
  for (const hidden of ['公开 Bot 画像', '任务认领', 'Dream Mode']) {
    expect(screen.queryByText(hidden)).not.toBeInTheDocument();
  }
  expect(screen.getAllByRole('switch')).toHaveLength(1);
  expect(screen.getByText(`当前可见性：${label}`)).toBeInTheDocument();
  expect(screen.getByText('好友审批策略')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '修改可见性' }));
  expect(await screen.findByText('Bot 可见性：对 Bot')).toBeInTheDocument();
});

it('外部 Bot 修改可见性时按保存语义提交并直接生效，不渲染审批链路', async () => {
  // 刻意保留 friend_ext 的审批类字段及其派生 pending：生产管线在外部加载时已清空 pending，
  // 此处构造异常上游数据验证展示层兜底——外部 Bot 任何情况下都不渲染审批态。
  const bot: CollaborationBot = {
    ...mapBotDtoToDomain({
      kind: 'bot',
      bot_id: row.id,
      name: row.name,
      status: 'online',
      visibility: 'protected',
      user_visibility: 'private',
      friend_ext: {
        view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部'],
        public_agent_approval: {
          status: 'CREATED',
          puid: 'PU-1',
          visibility: 'public',
          approval_url: 'https://example.com/inspection',
        },
      },
    }),
    publication: {
      user: { scope: 'none', organizationPaths: [] },
      bot: { scope: 'restricted', organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']] },
    },
  };
  jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue({
    currentUser: { displayName: '用户', employeeNumber: 'user-1', departmentPath: [] },
    organizationOptions: [],
    bots: [bot],
  });
  const mockedSubmitPublication = jest
    .spyOn(collaborationPrivacyService, 'submitPublication')
    .mockResolvedValue({ ...bot, publication: { ...bot.publication, bot: { scope: 'all', organizationPaths: [] } } });
  render(<BotGeneralConfigDialog bot={row} isExternalBot onClose={jest.fn()} />);
  await screen.findByText('当前可见性：限定组织可申请 · 1 个组织范围');

  // 外部 Bot 无审批渲染：即使 friend_ext 带审批类字段也不出待审批/审批链接。
  expect(screen.queryByText('待审批')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '查看审批进度' })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '修改可见性' }));
  expect(await screen.findByRole('button', { name: '保存' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '提交审批' })).not.toBeInTheDocument();
  // 能力开关关闭也不隐藏限定组织可申请（外部合同）。
  expect(screen.getByRole('radio', { name: /限定组织可申请/ })).toBeInTheDocument();
  // 已保存的名称范围原样回显，可直接再编辑。
  expect(screen.getByText('蚂蚁集团-CRO线-大安全技术部')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('radio', { name: /全部可见/ }));
  fireEvent.click(screen.getByRole('button', { name: '保存' }));

  await waitFor(() =>
      expect(mockedSubmitPublication).toHaveBeenCalledWith(
        expect.objectContaining({
          botId: row.id,
          audience: 'bot',
          isExternalBot: true,
          // 编辑器把原始 selected 状态交给 Service，收敛归一由 Service 负责（服务级测试已锁定）。
          config: expect.objectContaining({ scope: 'all' }),
          deptEntries: undefined,
        }),
      ),
    );
  // 保存成功后编辑器关闭回到配置视图。
  await waitFor(() => expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument());
  expect(screen.getByText('当前可见性：全部可见')).toBeInTheDocument();
  expect(mockedSubmitPublication).toHaveBeenCalledTimes(1);
});
