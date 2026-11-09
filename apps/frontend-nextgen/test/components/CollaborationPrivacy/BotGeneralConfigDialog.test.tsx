/** @jest-environment jsdom */
import { BotGeneralConfigDialog } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog';
import type { CollaborationBot, CollaborationPrivacyOverview } from '@/domain/collaborationPrivacy/types';
import * as identityModule from '@/hooks/useHumanIdentity';
import {
  getCurrentBotPublicRiskCheck,
  refreshBotPublicRiskCheck,
} from '@/services/backendApi/collaboration/collaborationBotPublicRiskController';
import {
  botCollaborationRepairService,
  type CollaborationRepairReport,
} from '@/services/bots/botCollaborationRepairService';
import { botPublishApprovalService } from '@/services/bots/botPublishApprovalService';
import { mapBotDto } from '@/services/bots/botMapper';
import { collaborationPrivacyService } from '@/services/collaborationPrivacy';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
// 避免通过 useHumanIdentity 透传加载 node_modules 中的 ESM 聊天适配器。
jest.mock('@tc-chat/adapters', () => ({}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), warning: jest.fn(), error: jest.fn() } }));
// 网络边界 mock，Hook 与共享 Store 使用真实实现（AC-34 竞态集成用例）。
jest.mock('@/services/bots/botCollaborationRepairService', () => ({
  botCollaborationRepairService: { repair: jest.fn(), pending: jest.fn() },
}));
// 风险动线网络边界与修复层同法：Hook/Store 走真实现，Controller 一律 mock。
jest.mock('@/services/backendApi/collaboration/collaborationBotPublicRiskController', () => ({
  createBotPublicRiskCheck: jest.fn(),
  getCurrentBotPublicRiskCheck: jest.fn(),
  getBotPublicRiskCheck: jest.fn(),
  refreshBotPublicRiskCheck: jest.fn(),
}));

const mockCapabilities = jest.fn();
jest.mock('@/capabilities', () => ({ getCapabilities: () => mockCapabilities() }));

const mockedUseHumanIdentity = jest.spyOn(identityModule, 'useHumanIdentity');
const mockRepair = jest.mocked(botCollaborationRepairService.repair);
const mockPending = jest.mocked(botCollaborationRepairService.pending);

const successReport: CollaborationRepairReport = {
  bot_id: 'bot-1',
  status: 'succeeded',
  items: [
    { component: 'ownership', status: 'succeeded', error_code: null },
    { component: 'profile', status: 'succeeded', error_code: null },
    { component: 'managers', status: 'succeeded', error_code: null },
  ],
};

const partialReport: CollaborationRepairReport = {
  ...successReport,
  status: 'partial_failed',
  items: [
    { component: 'ownership', status: 'succeeded', error_code: null },
    { component: 'profile', status: 'succeeded', error_code: null },
    { component: 'managers', status: 'unknown', error_code: 'delivery_timeout' },
  ],
};

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

const rowBot = (spaceKind: 'personal' | 'team' = 'personal', overrides: Record<string, unknown> = {}) =>
  mapBotDto({
    bot_id: 'bot-1',
    bot_name: '通用配置助手',
    engine: 'openclaw',
    status: 'ACTIVE',
    owner_id: '900004',
    ...overrides,
    ...(spaceKind === 'team' ? { space: { space_id: '12', kind: 'team' } } : {}),
  }).item;

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
  // 默认 Open Core 形态：发布审批组隐藏；internal 用例内显式覆盖 capability。
  // getPublicBotBackendEnabled 默认全栈开启：好友审批入口深链保持通知中心断言不变。
  mockCapabilities.mockReturnValue({
    getGeneralConfigPublishApprovalEnabled: () => ({ status: 'available', value: false }),
    getPublicBotBackendEnabled: () => true,
  });
  mockedUseHumanIdentity.mockReturnValue({
    status: 'ready',
    identity: { userId: '900004', displayName: '真实用户', online: true },
  });
  // 发布审批链路真源为 services/bots/botPublishApprovalService（botEditorService 的审批方法已外迁）。
  jest.spyOn(botPublishApprovalService, 'loadApproval').mockResolvedValue(false);
  jest.spyOn(botPublishApprovalService, 'saveApproval').mockResolvedValue({ data: { should_approval: false } } as never);
  mockPending.mockReturnValue(undefined);
  mockRepair.mockResolvedValue(successReport);
});

afterEach(() => {
  jest.clearAllMocks();
  useCollaborationPrivacyStore.getState().reset();
});

afterAll(() => {
  jest.restoreAllMocks();
});

describe('BotGeneralConfigDialog 公开设置弹窗（collab-permission-entry-migration AC-04~05/09~11）', () => {
  it('弹窗展示标题、副标题与 Bot 基础信息头，并渲染三组配置分区', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('公开设置')).toBeInTheDocument();
    expect(screen.getByText('管理该 Bot 在 BCN 网络中的协作状态及好友审批策略。')).toBeInTheDocument();
    expect(screen.getByText('通用配置助手')).toBeInTheDocument();
    expect(screen.getByText('openclaw')).toBeInTheDocument();
    expect(screen.getByText('Bot UUID')).toBeInTheDocument();
    // 验收修正：ID 显示协作域完整 UUID（含 :port 后缀），而非行数据 Bot ID。
    expect(screen.getByText('bot-1:900004')).toBeInTheDocument();
    expect(screen.queryByText('bot-1')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制 通用配置助手 的 Bot UUID' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 4, name: 'Bot 可见性' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 4, name: 'Bot 好友审批' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 4, name: '协作能力' })).toBeInTheDocument();
    expect(screen.getByText('公开 Bot 画像')).toBeInTheDocument();
    expect(screen.queryByText('任务认领')).not.toBeInTheDocument();
    expect(screen.queryByText('Dream Mode')).not.toBeInTheDocument();
  });

  it('加载中展示骨架占位（无障碍标签：正在加载公开设置）', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockImplementation(() => new Promise(() => {}) as never);

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    expect(screen.getByLabelText('正在加载公开设置')).toBeInTheDocument();
  });

  it('主接口失败时展示整窗错误态与重试入口', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockRejectedValue(new Error('网络异常'));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('公开设置加载失败')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('协作数据未命中该 Bot 时分区降级提示，不整窗报错', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('another-bot')));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('未获取到该 Bot 的协作配置')).toBeInTheDocument());
    expect(screen.getByText('该 Bot 可能尚未加入 BCN 协作网络；可稍后重试。')).toBeInTheDocument();
    expect(screen.queryByText('公开设置加载失败')).not.toBeInTheDocument();
  });

  it('未加入 BCN 的 Bot 展示只读横幅', async () => {
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBot(makeBot('bot-1:900004', false)));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getAllByText('加入 BCN 后才能修改协作权限')).toHaveLength(2));
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getAllByRole('switch')).toHaveLength(2);
    dialog.getAllByRole('switch').forEach((control) => expect(control).toBeDisabled());
  });
});

describe('BotGeneralConfigDialog Coding Bot 协作能力裁剪', () => {
  const codingBotRow = (capabilities: Record<string, unknown>) =>
    rowBot('personal', {
      engine: 'claude_code',
      template_type: 'generalCC',
      engine_properties: {
        template_config: { capabilities },
      },
    });

  it('模板未声明 BCN 时隐藏整个协作能力分组', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    const bot = codingBotRow({ member_management: true });
    expect(bot.runtime.isAgentCodingBot).toBe(true);
    expect(bot.runtime.bcnEnabled).toBe(false);

    render(<BotGeneralConfigDialog bot={bot} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('Bot 可见性')).toBeInTheDocument());
    expect(screen.queryByRole('heading', { level: 4, name: '协作能力' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('switch', { name: /参与协作群聊|公开 Bot 画像|任务认领|Dream Mode/ }),
    ).not.toBeInTheDocument();
  });

  it('模板声明 BCN 时仅展示参与协作群聊', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    const bot = codingBotRow({ enable_bcn_network: true });
    expect(bot.runtime.bcnEnabled).toBe(true);

    render(<BotGeneralConfigDialog bot={bot} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());
    expect(screen.getByRole('heading', { level: 4, name: '协作能力' })).toBeInTheDocument();
    expect(screen.queryByText('公开 Bot 画像')).not.toBeInTheDocument();
    expect(screen.queryByText('任务认领')).not.toBeInTheDocument();
    expect(screen.queryByText('Dream Mode')).not.toBeInTheDocument();
  });
});

describe('发布审批组（collab-permission-entry-migration AC-06~08，SHAPE:INTERNAL × 团队空间）', () => {
  const internalCapabilities = {
    getGeneralConfigPublishApprovalEnabled: () => ({ status: 'available', value: true }),
    getPublicBotBackendEnabled: () => true,
  };

  it('internal × 团队空间 Bot：发布审批读取真实配置并可持久化切换（AC-06/07）', async () => {
    mockCapabilities.mockReturnValue(internalCapabilities);
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));
    jest.mocked(botPublishApprovalService.loadApproval).mockResolvedValue(true);

    render(<BotGeneralConfigDialog bot={rowBot('team', { bot_type: 'service' })} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());

    // 组序：发布审批为首组，其后 Bot 可见性 / Bot 好友审批 / 协作能力。
    const sectionTitles = screen.getAllByRole('heading', { level: 4 }).map((heading) => heading.textContent);
    expect(sectionTitles).toEqual(['发布审批', 'Bot 可见性', 'Bot 好友审批', '协作能力']);

    const approvalSwitch = await screen.findByRole('switch', { name: '关闭共同编辑者发布需 Owner 审批' });
    fireEvent.click(approvalSwitch);
    await waitFor(() => expect(botPublishApprovalService.saveApproval).toHaveBeenCalledWith('bot-1', false, '900004'));
    expect(screen.getByRole('switch', { name: '开启共同编辑者发布需 Owner 审批' })).toBeInTheDocument();
  });

  it('internal × 个人空间 Bot：不渲染发布审批，Bot 可见性成为首组（AC-06 边界）', async () => {
    mockCapabilities.mockReturnValue(internalCapabilities);
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    render(<BotGeneralConfigDialog bot={rowBot('personal')} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());

    expect(screen.queryByText('发布审批')).not.toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 4 })[0].textContent).toBe('Bot 可见性');
  });

  it('internal × 团队空间个人 Bot：分区渲染但开关禁用置关并提示，不发审批配置请求', async () => {
    mockCapabilities.mockReturnValue(internalCapabilities);
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    render(<BotGeneralConfigDialog bot={rowBot('team')} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());

    expect(screen.getByText('发布审批')).toBeInTheDocument();
    expect(screen.getByText('个人 Bot 暂不支持协作者申请')).toBeInTheDocument();
    const approvalSwitch = screen.getByRole('switch', { name: '开启共同编辑者发布需 Owner 审批' });
    expect(approvalSwitch).toBeDisabled();
    expect(botEditorService.loadApproval).not.toHaveBeenCalled();
  });

  it('Open Core × 团队空间 Bot：不渲染发布审批（SHAPE:INTERNAL，AC-06）', async () => {
    // beforeEach 默认 Open Core 形态（capability value: false），无需覆盖。
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    render(<BotGeneralConfigDialog bot={rowBot('team')} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());

    expect(screen.queryByText('发布审批')).not.toBeInTheDocument();
  });

  it('未加入 BCN 时发布审批开关仍可切换，不跟随只读（AC-08）', async () => {
    mockCapabilities.mockReturnValue(internalCapabilities);
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBot(makeBot('bot-1:900004', false)));

    render(<BotGeneralConfigDialog bot={rowBot('team', { bot_type: 'service' })} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getAllByText('加入 BCN 后才能修改协作权限')).toHaveLength(2));

    const approvalSwitch = screen.getByRole('switch', { name: '开启共同编辑者发布需 Owner 审批' });
    expect(approvalSwitch).toBeEnabled();
    fireEvent.click(approvalSwitch);
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: '关闭共同编辑者发布需 Owner 审批' })).toBeInTheDocument(),
    );
    // 协作能力开关仍全部只读（排除发布审批开关）。
    const dialog = within(screen.getByRole('dialog'));
    const collaborationSwitches = dialog
      .getAllByRole('switch')
      .filter((control) => !control.getAttribute('aria-label')?.includes('共同编辑者发布'));
    expect(collaborationSwitches).toHaveLength(2);
    collaborationSwitches.forEach((control) => expect(control).toBeDisabled());
  });

  it('协作数据未命中该 Bot 时发布审批照常渲染（分区降级原则）', async () => {
    mockCapabilities.mockReturnValue(internalCapabilities);
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('another-bot')));

    render(<BotGeneralConfigDialog bot={rowBot('team')} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('未获取到该 Bot 的协作配置')).toBeInTheDocument());
    expect(screen.getByText('发布审批')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '开启共同编辑者发布需 Owner 审批' })).toBeInTheDocument();
  });

  it('关闭弹窗重新打开后重新读取持久化发布审批配置', async () => {
    mockCapabilities.mockReturnValue(internalCapabilities);
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    const first = render(<BotGeneralConfigDialog bot={rowBot('team', { bot_type: 'service' })} onClose={jest.fn()} />);
    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('switch', { name: '开启共同编辑者发布需 Owner 审批' }));
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: '关闭共同编辑者发布需 Owner 审批' })).toBeInTheDocument(),
    );
    first.unmount();

    render(<BotGeneralConfigDialog bot={rowBot('team', { bot_type: 'service' })} onClose={jest.fn()} />);
    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());
    expect(screen.getByRole('switch', { name: '开启共同编辑者发布需 Owner 审批' })).toBeInTheDocument();
    expect(botPublishApprovalService.loadApproval).toHaveBeenCalledTimes(2);
  });
});

describe('内部 Bot 协作能力修复行（AC-23/24/27/28/34）', () => {
  const codingBotRow = (capabilities: Record<string, unknown>) =>
    rowBot('personal', {
      engine: 'claude_code',
      template_type: 'generalCC',
      engine_properties: { template_config: { capabilities } },
    });

  it('Owner 点击「修复信息」按 Backend bot.id 与认证 Human 发起一次修复，在途禁用', async () => {
    let finish!: (report: CollaborationRepairReport) => void;
    mockRepair.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '修复信息' }));

    expect(mockRepair).toHaveBeenCalledTimes(1);
    expect(mockRepair).toHaveBeenCalledWith('bot-1', '900004');
    expect(screen.getByRole('button', { name: '修复中…' })).toBeDisabled();

    await act(async () => {
      finish(partialReport);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText('归属：已修复')).toBeInTheDocument();
    expect(screen.getByText('基本资料：已修复')).toBeInTheDocument();
    expect(screen.getByText('协作者：结果未确认')).toBeInTheDocument();
    // 修复分项固定三项（backend 移出 caller_config），不再出现「调用配置」。
    expect(screen.queryByText(/调用配置：/)).not.toBeInTheDocument();
    // 分项结果已由列表承载，不再额外弹出错误提示。
    expect(toast.warning).toHaveBeenCalledTimes(1);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('修复成功后配置静默刷新，面板不切入加载骨架', async () => {
    let finish!: (report: CollaborationRepairReport) => void;
    mockRepair.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    let refreshInFlight!: (value: ReturnType<typeof overviewWithBot>) => void;
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValueOnce(overviewWithBot(makeBot('bot-1:900004')))
      .mockImplementation(() => new Promise((resolve) => { refreshInFlight = resolve; }));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '修复信息' }));

    await act(async () => {
      finish(successReport);
      await Promise.resolve();
      await Promise.resolve();
    });

    // 刷新在途期间：内容保持展示，不出现整面板加载骨架。
    expect(screen.queryByLabelText('正在加载公开设置')).not.toBeInTheDocument();
    expect(screen.getByLabelText('修复 Bot 信息')).toBeInTheDocument();
    expect(refreshInFlight).toBeDefined();
  });

  it('请求失败只走全局错误提示一次，行内展示错误文案', async () => {
    mockRepair.mockRejectedValue(new Error('后端超时'));
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '修复信息' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('未能确认修复结果，请稍后重试'));
    expect(toast.error).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['非 Owner', rowBot('personal', { owner_id: 'someone-else' })],
    ['锁被他人持有', rowBot('personal', { lock: { locked: true, holder_user_id: 'other-user' } })],
  ])('%s 时修复按钮禁用且不能提交', async (_case, bot) => {
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    render(<BotGeneralConfigDialog bot={bot} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());
    const button = screen.getByRole('button', { name: '修复信息' });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(mockRepair).not.toHaveBeenCalled();
  });

  it.each(['hidden', 'offline'] as const)('协作状态 %s 仍保留 Owner 修复入口', async (status) => {
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(
        overviewWithBot({ ...makeBot('bot-1:900004'), collaborationStatus: status }),
      );

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled());
  });

  it('privacy 加载中仍独立展示可用修复行，不被骨架覆盖', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockImplementation(() => new Promise(() => {}) as never);

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    expect(screen.getByLabelText('正在加载公开设置')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 4, name: '协作能力' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled();
  });

  it('privacy 读取失败仍独立展示可用修复行', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockRejectedValue(new Error('网络异常'));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('公开设置加载失败')).toBeInTheDocument());
    expect(screen.getByRole('heading', { level: 4, name: '协作能力' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled();
  });

  it('privacy 空结果仍独立展示可用修复行', async () => {
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBot(makeBot('another-bot')));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('未获取到该 Bot 的协作配置')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled();
  });

  it('未声明 BCN 的 Coding Bot 不出现修复入口', async () => {
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    const bot = codingBotRow({ member_management: true });
    expect(bot.runtime.isAgentCodingBot).toBe(true);
    expect(bot.runtime.bcnEnabled).toBe(false);

    render(<BotGeneralConfigDialog bot={bot} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('Bot 可见性')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '修复信息' })).not.toBeInTheDocument();
  });

  // AC-34：真实 repair Hook + 真实 privacy Hook/共享 Store，仅 mock 网络 service；
  // POST 已结束、A 的自动配置刷新仍在途时切换到 B，A 的迟到结果不得改写 B。
  function mockDeferredOverviewLoads() {
    const deferreds: Array<{
      resolve: (value: CollaborationPrivacyOverview) => void;
      reject: (reason: unknown) => void;
    }> = [];
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          deferreds.push({ resolve, reject });
        }),
    );
    return deferreds;
  }

  async function switchDuringAReconfiguration(
    settleStaleRefresh: (deferreds: ReturnType<typeof mockDeferredOverviewLoads>) => Promise<void>,
  ) {
    const deferreds = mockDeferredOverviewLoads();
    mockRepair.mockResolvedValue(successReport);
    const onClose = jest.fn();
    const view = render(<BotGeneralConfigDialog bot={rowBot()} onClose={onClose} />);
    await waitFor(() => expect(deferreds).toHaveLength(1));
    await act(async () => {
      deferreds[0].resolve(overviewWithBot(makeBot('bot-1:900004')));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByRole('button', { name: '修复信息' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: '修复信息' }));
    // POST 已完成，A 的自动配置刷新（第二次 loadOverview）仍在途。
    await waitFor(() => expect(deferreds).toHaveLength(2));

    view.rerender(
      <BotGeneralConfigDialog
        bot={rowBot('personal', { bot_id: 'bot-2', bot_name: 'Bot B' })}
        onClose={onClose}
      />,
    );
    await waitFor(() => expect(deferreds).toHaveLength(3));
    await act(async () => {
      deferreds[2].resolve(overviewWithBot(makeBot('bot-2:900004')));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByText('bot-2:900004')).toBeInTheDocument());
    const before = useCollaborationPrivacyStore.getState();

    await settleStaleRefresh(deferreds);

    const after = useCollaborationPrivacyStore.getState();
    expect(after.overview).toBe(before.overview);
    expect(after.error).toBe(before.error);
    expect(after.loading).toBe(before.loading);
    expect(screen.getByText('bot-2:900004')).toBeInTheDocument();
    expect(screen.queryByText('bot-1:900004')).not.toBeInTheDocument();
    // 切 Bot 后不得重发修复 POST。
    expect(mockRepair).toHaveBeenCalledTimes(1);
    expect(mockRepair).toHaveBeenCalledWith('bot-1', '900004');
  }

  it('切 Bot 后 A 迟到的配置刷新成功不覆盖 B', async () => {
    await switchDuringAReconfiguration(async (deferreds) => {
      await act(async () => {
        deferreds[1].resolve(overviewWithBot(makeBot('bot-1:900004')));
        await Promise.resolve();
        await Promise.resolve();
      });
    });
  });

  it('切 Bot 后 A 迟到的配置刷新失败不覆盖 B', async () => {
    await switchDuringAReconfiguration(async (deferreds) => {
      await act(async () => {
        deferreds[1].reject(new Error('A 的刷新失败'));
        await Promise.resolve();
        await Promise.resolve();
      });
    });
  });

  it('审批中行级提供「刷新状态」入口，点击后经 refresh 端点重读最新记录（bot-public-risk-check）', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));
    // approving 起钩子不再自动轮询（审批终态由后端工单回调推进），current 仅弹窗打开恢复时查一次。
    const approvingRecord = {
      risk_check_id: 'rk-1',
      phase: 'APPROVING',
      security_status: 'PASS',
      detail_url: null,
      auto_poll_paused: false,
      auto_poll_until: null,
      next_action_at: null,
      target: { public_scope: 'user', visibility: 'PUBLIC', view_depts: [] },
      error_reason: null,
      invalidation_reason: null,
      approval_puid: 'WO-1',
    };
    const mockRiskCurrent = jest.mocked(getCurrentBotPublicRiskCheck);
    const mockRiskRefresh = jest.mocked(refreshBotPublicRiskCheck);
    mockRiskCurrent.mockResolvedValue({ code: 200000, message: 'OK', data: approvingRecord });
    mockRiskRefresh.mockResolvedValue({ code: 200000, message: 'OK', data: { outcome: 'NOT_QUERIED', record: approvingRecord } });

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    const refreshButton = await screen.findByRole('button', { name: '刷新状态' });
    expect(screen.getByRole('button', { name: '审批中' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '查看审批进度' })).toBeInTheDocument();
    expect(mockRiskCurrent).toHaveBeenCalledWith('bot-1:900004', '900004', { public_scope: 'user' });

    fireEvent.click(refreshButton);

    await waitFor(() => expect(mockRiskRefresh).toHaveBeenCalledWith('bot-1:900004', '900004', 'rk-1'));
  });

  it('审批期行带 pending 投影时「刷新状态」仍可见，双刷后行回正常态（bot-public-risk-check）', async () => {
    // 复刻线上场景：APPROVING 时后端把同一申请写进 BCS friend_ext（status=PROCESSING），
    // 前端即出现 pendingPublications 投影；行级刷新入口不得因此隐藏。
    const approvingRecord = {
      risk_check_id: 'rk-1',
      phase: 'APPROVING',
      security_status: 'PASS',
      detail_url: 'https://example.test/risk/rk-1',
      auto_poll_paused: false,
      auto_poll_until: null,
      next_action_at: null,
      target: { public_scope: 'user', visibility: 'public', view_depts: [] },
      error_reason: null,
      invalidation_reason: null,
      approval_puid: 'WO-1',
    };
    const publishedRecord = { ...approvingRecord, phase: 'PUBLISHED' };
    const pendingBot = makeBot('bot-1:900004');
    pendingBot.pendingPublications = {
      user: {
        id: 'WO-1',
        audience: 'user',
        target: { scope: 'all', organizationPaths: [] },
        submittedAt: '',
        approvalUrl: 'https://approval.example.test/ticket/WO-1',
      },
    };
    const loadSpy = jest.spyOn(collaborationPrivacyService, 'loadOverview');
    let overview = overviewWithBot(pendingBot);
    loadSpy.mockImplementation(async () => overview);
    const mockRiskCurrent = jest.mocked(getCurrentBotPublicRiskCheck);
    const mockRiskRefresh = jest.mocked(refreshBotPublicRiskCheck);
    mockRiskCurrent.mockResolvedValue({ code: 200000, message: 'OK', data: approvingRecord });
    // refresh 端点对非 CHECKING phase 无副作用地透传最新记录；审批完成后后端已翻成 PUBLISHED。
    // 翻转时点挂在 refresh 上：进入 approving 边沿的自动静默重载（useCollaborationPrivacy）
    // 仍读到 pending——审批回调前 friend_ext 一直为 PROCESSING，与线上一致。
    mockRiskRefresh.mockImplementation(async () => {
      overview = overviewWithBot(makeBot('bot-1:900004'));
      return { code: 200000, message: 'OK', data: { outcome: 'NOT_QUERIED', record: publishedRecord } };
    });

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    const refreshButton = await screen.findByRole('button', { name: '刷新状态' });
    expect(screen.getByText('待审批')).toBeInTheDocument();

    fireEvent.click(refreshButton);

    await waitFor(() => expect(mockRiskRefresh).toHaveBeenCalledWith('bot-1:900004', '900004', 'rk-1'));
    // 双刷第二条：refresh 后静默重载总览，行级「待审批」随 friend_ext 终态同步清除。
    await waitFor(() => {
      const editButtons = screen.getAllByRole('button', { name: '修改可见性' });
      expect(editButtons.length).toBeGreaterThan(0);
      editButtons.forEach((button) => expect(button).toBeEnabled());
    });
    expect(screen.queryByText('待审批')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '刷新状态' })).not.toBeInTheDocument();
  });
});
