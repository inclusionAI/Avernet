/** @jest-environment jsdom */
// 公开设置「好友名单」section + 管理抽屉 + 移除确认动线（bot-friend-roster）:
// 仅 Owner 可见;counts 展示「好友用户 N · 好友 Bot M」;抽屉两页签全量列表 +
// 客户端搜索（无截断，>2,000 好友全量可达);移除走二次确认,成功原地刷新 +
// toast,deleted:false 走"已被移除"中性提示;详情缺失的好友按原始 id 渲染且
// 可移除;Hook 只收到协作 Bot UUID(targetBot.id),绝不用后端行 bot.id。
import type { CollaborationBot, CollaborationPrivacyOverview } from '@/domain/collaborationPrivacy/types';
import * as rosterModule from '@/hooks/useBotFriendRoster';
import * as identityModule from '@/hooks/useHumanIdentity';
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import {
  deleteFriendConnection,
  listFriendConnections,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import {
  botCollaborationRepairService,
  type CollaborationRepairReport,
} from '@/services/botWorkshop/botCollaborationRepairService';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { collaborationPrivacyService } from '@/services/collaborationPrivacy';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { BotGeneralConfigDialog } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
// 避免通过 useHumanIdentity 透传加载 node_modules 中的 ESM 聊天适配器。
jest.mock('@tc-chat/adapters', () => ({}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), warning: jest.fn(), error: jest.fn() } }));
jest.mock('@/services/botWorkshop/botCollaborationRepairService', () => ({
  botCollaborationRepairService: { repair: jest.fn(), pending: jest.fn() },
}));
jest.mock('@/services/backendApi/collaboration/collaborationBotPublicRiskController', () => ({
  createBotPublicRiskCheck: jest.fn(),
  getCurrentBotPublicRiskCheck: jest.fn(),
  getBotPublicRiskCheck: jest.fn(),
  refreshBotPublicRiskCheck: jest.fn(),
}));
// 好友名单链路的网络边界:Hook/服务走真实实现,控制器 mock(类型导出在编译期
// 擦除,工厂只需替换运行时函数)。
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController', () => ({
  deleteFriendConnection: require('jest-mock').fn(),
  listFriendConnections: require('jest-mock').fn(),
}));
jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({
  queryCollaborationBots: require('jest-mock').fn(),
}));

const mockCapabilities = jest.fn();
jest.mock('@/capabilities', () => ({ getCapabilities: () => mockCapabilities() }));

const mockedUseHumanIdentity = jest.spyOn(identityModule, 'useHumanIdentity');
const listFriendConnectionsMock = listFriendConnections as unknown as jest.Mock;
const deleteFriendConnectionMock = deleteFriendConnection as unknown as jest.Mock;
const queryCollaborationBotsMock = queryCollaborationBots as unknown as jest.Mock;

const BOT_UUID = 'bot-1:900004'; // 协作 Bot UUID(targetBot.id),含 :port 后缀

/** 可变好友名单:移除动线里数据源随身变化,重拉读取最新数组。 */
const roster = {
  humans: [
    { actor: { type: 'human' as const, id: '447147' } },
    { actor: { type: 'human' as const, id: '448148' } },
  ],
  bots: [{ actor: { type: 'bot' as const, id: 'bot-friend:9' } }],
  humanTotal: 2,
};

const relationPage = (items: unknown[], total: number, page: number) => ({
  code: 20000,
  message: 'OK',
  data: { items, total, page, page_size: 100 },
  request_id: 'relations',
});

function wireRelationMock(custom?: (params: { target_type?: string; page?: number }) => unknown) {
  listFriendConnectionsMock.mockImplementation(
    async (params: { target_type?: string; page?: number }) => {
      if (custom) return custom(params);
      if (params.target_type === 'bot') return relationPage(roster.bots, roster.bots.length, 1);
      return relationPage(roster.humans, roster.humanTotal, params.page ?? 1);
    },
  );
  queryCollaborationBotsMock.mockResolvedValue({
    code: 20000,
    message: 'OK',
    data: {
      items: [
        { bot_id: 'human_447147', kind: 'human', name: '风太', status: 'online' },
        { bot_id: 'human_448148', kind: 'human', name: '雨太', status: 'hidden' },
        { bot_id: 'bot-friend:9', kind: 'bot', name: '协作小助', status: 'online' },
      ],
    },
    request_id: 'details',
  });
}

const makeBot = (id: string, joinedBcn = true): CollaborationBot => ({
  id,
  name: '通用配置助手',
  engine: 'OpenClaw',
  joinedBcn,
  collaborationStatus: 'online',
  profilePublic: false,
  taskClaimingEnabled: false,
  dreamModelEnabled: false,
  publication: {
    user: { scope: 'all', organizationPaths: [] },
    bot: { scope: 'none', organizationPaths: [] },
  },
  pendingPublications: {},
  friendApproval: { mode: 'all', exemptOrganizationPaths: [] },
});

const overviewWithBot = (bot: CollaborationBot): CollaborationPrivacyOverview => ({
  currentUser: { displayName: '真实用户', employeeNumber: '900004', departmentPath: [] },
  organizationOptions: [],
  bots: [bot],
});

const rowBot = (overrides: Record<string, unknown> = {}) =>
  mapBotDto({
    bot_id: 'bot-1',
    bot_name: '通用配置助手',
    engine: 'openclaw',
    status: 'ACTIVE',
    owner_id: '900004',
    ...overrides,
  }).item;

function renderDialog(options: { ownerId?: string; isExternalBot?: boolean } = {}) {
  jest
    .spyOn(collaborationPrivacyService, 'loadOverview')
    .mockResolvedValue(overviewWithBot(makeBot(BOT_UUID)));
  return render(
    <BotGeneralConfigDialog
      bot={rowBot(options.ownerId ? { owner_id: options.ownerId } : {})}
      isExternalBot={options.isExternalBot}
      onClose={jest.fn()}
    />,
  );
}

// 抽屉打开并等首屏列表就绪。
async function openDrawer() {
  fireEvent.click(await screen.findByRole('button', { name: '管理好友' }));
  await waitFor(() => expect(screen.getByPlaceholderText('搜索好友')).toBeInTheDocument());
}

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
  useCollaborationPrivacyStore.getState().reset();
  roster.humans = [
    { actor: { type: 'human' as const, id: '447147' } },
    { actor: { type: 'human' as const, id: '448148' } },
  ];
  roster.bots = [{ actor: { type: 'bot' as const, id: 'bot-friend:9' } }];
  roster.humanTotal = 2;
  mockCapabilities.mockReturnValue({
    getGeneralConfigPublishApprovalEnabled: () => ({ status: 'available', value: false }),
    getPublicBotBackendEnabled: () => true,
  });
  mockedUseHumanIdentity.mockReturnValue({
    status: 'ready',
    identity: { userId: '900004', displayName: '真实用户', online: true },
  });
  jest.spyOn(botEditorService, 'loadApproval').mockResolvedValue(false);
  jest.spyOn(botEditorService, 'saveApproval').mockResolvedValue({ data: { should_approval: false } } as never);
  jest.mocked(botCollaborationRepairService.pending).mockReturnValue(undefined);
  jest.mocked(botCollaborationRepairService.repair).mockResolvedValue({} as CollaborationRepairReport);
  // 显式清零调用记录：模块级 mock 的 calls 跨用例累积,翻页计数断言必须只看本用例。
  listFriendConnectionsMock.mockClear();
  deleteFriendConnectionMock.mockClear();
  queryCollaborationBotsMock.mockClear();
  wireRelationMock();
});

afterEach(async () => {
  // 先刷干在途的 mock promise 链（已 resolve 的微任务）再清计数:
  // 否则上一用例的 fetch-all 翻页会把调用记录落到下一用例。
  await act(async () => {});
  jest.clearAllMocks();
  useCollaborationPrivacyStore.getState().reset();
});

afterAll(() => {
  jest.restoreAllMocks();
});

describe('公开设置「好友名单」section', () => {
  it('Owner 看到分区与分类计数,Hook 收到协作 Bot UUID 而非后端行 id', async () => {
    const rosterSpy = jest.spyOn(rosterModule, 'useBotFriendRoster');
    renderDialog();

    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    expect(screen.getByRole('heading', { level: 4, name: '好友名单' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '管理好友' })).toBeInTheDocument();
    expect(rosterSpy).toHaveBeenCalledWith(BOT_UUID);
    expect(
      listFriendConnectionsMock.mock.calls.every(
        ([params]) => (params as { actor_id: string }).actor_id === BOT_UUID,
      ),
    ).toBe(true);
    rosterSpy.mockRestore();
  });

  it('非 Owner(manager)渲染里没有该分区', async () => {
    renderDialog({ ownerId: '900005' });

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());
    expect(screen.queryByRole('heading', { level: 4, name: '好友名单' })).not.toBeInTheDocument();
  });

  it('外部 Bot 变体对 Owner 同样渲染该分区', async () => {
    renderDialog({ isExternalBot: true });

    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    expect(screen.getByRole('heading', { level: 4, name: '好友名单' })).toBeInTheDocument();
  });
});

describe('管理好友抽屉', () => {
  it('抽屉取 nested 层级,高于父 Modal 遮罩,移除确认框仍在抽屉之上', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    await openDrawer();

    // 抽屉遮罩与内容都取 nested Token(父 Modal 为 --z-dialog,低一级)。
    const nested = Array.from(document.querySelectorAll('body *')).filter((node) =>
      node.classList?.contains('z-[var(--z-nested-drawer)]'),
    );
    expect(nested).toHaveLength(2);
    expect(document.querySelector('[class*="z-[var(--z-drawer)]"]')).toBeNull();

    fireEvent.click(screen.getAllByRole('button', { name: '移除' })[0]);
    const alert = await screen.findByRole('alertdialog');
    expect(alert.className).toContain('z-[var(--z-critical-dialog)]');
  });

  it('两页签渲染全量列表,搜索按名字与 id 过滤且只影响当前页签', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    await openDrawer();

    // 默认好友用户页签
    expect(screen.getByText('风太')).toBeInTheDocument();
    expect(screen.getByText('雨太')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('搜索好友'), { target: { value: '448148' } });
    expect(screen.queryByText('风太')).not.toBeInTheDocument();
    expect(screen.getByText('雨太')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '好友 Bot' }));
    fireEvent.change(screen.getByPlaceholderText('搜索好友'), { target: { value: '' } });
    expect(screen.getByText('协作小助')).toBeInTheDocument();
    expect(screen.getByText('bot-friend:9')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('搜索好友'), { target: { value: '代码' } });
    expect(screen.queryByText('协作小助')).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('搜索好友'), { target: { value: 'bot-friend' } });
    expect(screen.getByText('bot-friend:9')).toBeInTheDocument();
  });

  it('>20 页(2,050 好友)全量拉取,最后一页的好友也能被搜到', async () => {
    roster.humanTotal = 2050;
    wireRelationMock((params) => {
      if (params.target_type === 'bot') return relationPage([], 0, 1);
      const page = params.page ?? 1;
      const start = (page - 1) * 100;
      const size = page === 21 ? 50 : 100;
      const ids = Array.from({ length: size }, (_, index) => `${start + index + 1}`);
      return relationPage(
        ids.map((id) => ({ actor: { type: 'human', id } })),
        2050,
        page,
      );
    });
    queryCollaborationBotsMock.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { items: [] },
      request_id: 'details',
    });

    renderDialog();
    await waitFor(() => expect(screen.getByText('好友用户 2050 · 好友 Bot 0')).toBeInTheDocument());
    await openDrawer();

    fireEvent.change(screen.getByPlaceholderText('搜索好友'), { target: { value: '2050' } });
    await waitFor(() => expect(screen.getByText('2050')).toBeInTheDocument());
    // 2,050 = 21 页全量翻完（每页 100,最后一页 50),无截断、无多余首拉。
    expect(
      listFriendConnectionsMock.mock.calls.filter(
        ([params]) => (params as { target_type?: string }).target_type === 'human',
      ),
    ).toHaveLength(21);
  });

  it('详情缺失的好友按原始 id 渲染且仍可移除', async () => {
    queryCollaborationBotsMock.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { items: [] },
      request_id: 'details',
    });
    deleteFriendConnectionMock.mockResolvedValue({ code: 20000, message: 'OK', data: { deleted: true } });

    renderDialog();
    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    await openDrawer();
    expect(screen.getByText('448148')).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: '移除' })[1]);
    expect(await screen.findByText(/确定移除好友「448148」吗/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确定移除' }));
    await waitFor(() =>
      expect(deleteFriendConnectionMock).toHaveBeenCalledWith(
        expect.objectContaining({ target_actor_type: 'human', target_actor_id: '448148' }),
      ),
    );
  });
});

describe('移除好友动线', () => {
  it('确认后按 Owner 参数删除,原地重拉并 toast 成功', async () => {
    deleteFriendConnectionMock.mockImplementation(async (params: { target_actor_id: string }) => {
      roster.humans = roster.humans.filter((item) => item.actor.id !== params.target_actor_id);
      roster.humanTotal = roster.humans.length;
      return { code: 20000, message: 'OK', data: { deleted: true } };
    });

    renderDialog();
    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    await openDrawer();
    fireEvent.click(screen.getAllByRole('button', { name: '移除' })[0]);

    expect(
      await screen.findByText(
        '移除后双方好友关系将同步解除，对方将在通知中心收到系统消息。确定移除好友「风太」吗？',
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确定移除' }));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(deleteFriendConnectionMock).toHaveBeenCalledWith({
      actor_type: 'bot',
      actor_id: BOT_UUID,
      target_actor_type: 'human',
      target_actor_id: '447147',
    });
    await waitFor(() => expect(screen.getByText('好友用户 1 · 好友 Bot 1')).toBeInTheDocument());
    expect(screen.queryByText('风太')).not.toBeInTheDocument();
    expect(screen.getByText('雨太')).toBeInTheDocument();
  });

  it('取消确认保留该行且不发请求', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    await openDrawer();
    fireEvent.click(screen.getAllByRole('button', { name: '移除' })[0]);
    fireEvent.click(await screen.findByRole('button', { name: '取消' }));

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '确定移除' })).not.toBeInTheDocument(),
    );
    expect(deleteFriendConnectionMock).not.toHaveBeenCalled();
    expect(screen.getByText('风太')).toBeInTheDocument();
  });

  it('deleted:false 走"已被移除"中性提示且无错误态', async () => {
    deleteFriendConnectionMock.mockResolvedValue({ code: 20000, message: 'OK', data: { deleted: false } });

    renderDialog();
    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    await openDrawer();
    fireEvent.click(screen.getAllByRole('button', { name: '移除' })[0]);
    fireEvent.click(await screen.findByRole('button', { name: '确定移除' }));

    await waitFor(() => expect(toast.warning).toHaveBeenCalled());
    expect(toast.error).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '确定移除' })).not.toBeInTheDocument(),
    );
  });

  it('移除 Bot 好友的确认文案点明通知其 Owner', async () => {
    deleteFriendConnectionMock.mockResolvedValue({ code: 20000, message: 'OK', data: { deleted: true } });

    renderDialog();
    await waitFor(() => expect(screen.getByText('好友用户 2 · 好友 Bot 1')).toBeInTheDocument());
    await openDrawer();
    fireEvent.click(screen.getByRole('button', { name: '好友 Bot' }));
    fireEvent.click(screen.getByRole('button', { name: '移除' }));

    expect(
      await screen.findByText(/该 Bot 的 Owner 将在通知中心收到系统消息。确定移除好友 Bot「协作小助」吗？/),
    ).toBeInTheDocument();
  });
});
