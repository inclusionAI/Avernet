/** @jest-environment jsdom */
import { BotGeneralConfigDialog } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog';
import type { CollaborationBot, CollaborationPrivacyOverview } from '@/domain/collaborationPrivacy/types';
import * as identityModule from '@/hooks/useHumanIdentity';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { collaborationPrivacyService } from '@/services/collaborationPrivacy';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

const mockCapabilities = jest.fn();
jest.mock('@/capabilities', () => ({ getCapabilities: () => mockCapabilities() }));

const mockedUseHumanIdentity = jest.spyOn(identityModule, 'useHumanIdentity');

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

const rowBot = (spaceKind: 'personal' | 'team' = 'personal') =>
  mapBotDto({
    bot_id: 'bot-1',
    bot_name: '通用配置助手',
    engine: 'openclaw',
    status: 'ACTIVE',
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
  mockCapabilities.mockReturnValue({
    getGeneralConfigPublishApprovalEnabled: () => ({ status: 'available', value: false }),
  });
  mockedUseHumanIdentity.mockReturnValue({
    status: 'ready',
    identity: { userId: '900004', displayName: '真实用户', online: true },
  });
  jest.spyOn(botEditorService, 'loadApproval').mockResolvedValue(false);
  jest.spyOn(botEditorService, 'saveApproval').mockResolvedValue({ data: { should_approval: false } } as never);
});

afterEach(() => {
  jest.clearAllMocks();
  useCollaborationPrivacyStore.getState().reset();
});

afterAll(() => {
  jest.restoreAllMocks();
});

describe('BotGeneralConfigDialog 通用配置弹窗（collab-permission-entry-migration AC-04~05/09~11）', () => {
  it('弹窗展示标题、副标题与 Bot 基础信息头，并渲染三组配置分区', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('通用配置')).toBeInTheDocument();
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
    expect(screen.getByText('Dream Mode')).toBeInTheDocument();
  });

  it('加载中展示骨架占位（无障碍标签：正在加载通用配置）', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockImplementation(() => new Promise(() => {}) as never);

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    expect(screen.getByLabelText('正在加载通用配置')).toBeInTheDocument();
  });

  it('主接口失败时展示整窗错误态与重试入口', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockRejectedValue(new Error('网络异常'));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('通用配置加载失败')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('协作数据未命中该 Bot 时分区降级提示，不整窗报错', async () => {
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('another-bot')));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('未获取到该 Bot 的协作配置')).toBeInTheDocument());
    expect(screen.getByText('该 Bot 可能尚未加入 BCN 协作网络；可稍后重试。')).toBeInTheDocument();
    expect(screen.queryByText('通用配置加载失败')).not.toBeInTheDocument();
  });

  it('未加入 BCN 的 Bot 展示只读横幅', async () => {
    jest
      .spyOn(collaborationPrivacyService, 'loadOverview')
      .mockResolvedValue(overviewWithBot(makeBot('bot-1:900004', false)));

    render(<BotGeneralConfigDialog bot={rowBot()} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getAllByText('加入 BCN 后才能修改协作权限')).toHaveLength(2));
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getAllByRole('switch')).toHaveLength(4);
    dialog.getAllByRole('switch').forEach((control) => expect(control).toBeDisabled());
  });
});
describe('发布审批组（collab-permission-entry-migration AC-06~08，SHAPE:INTERNAL × 团队空间）', () => {
  const internalCapabilities = {
    getGeneralConfigPublishApprovalEnabled: () => ({ status: 'available', value: true }),
  };

  it('internal × 团队空间 Bot：发布审批读取真实配置并可持久化切换（AC-06/07）', async () => {
    mockCapabilities.mockReturnValue(internalCapabilities);
    jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overviewWithBot(makeBot('bot-1:900004')));
    jest.mocked(botEditorService.loadApproval).mockResolvedValue(true);

    render(<BotGeneralConfigDialog bot={rowBot('team')} onClose={jest.fn()} />);

    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());

    // 组序：发布审批为首组，其后 Bot 可见性 / Bot 好友审批 / 协作能力。
    const sectionTitles = screen.getAllByRole('heading', { level: 4 }).map((heading) => heading.textContent);
    expect(sectionTitles).toEqual(['发布审批', 'Bot 可见性', 'Bot 好友审批', '协作能力']);

    const approvalSwitch = await screen.findByRole('switch', { name: '关闭共同编辑者发布需 Owner 审批' });
    fireEvent.click(approvalSwitch);
    await waitFor(() => expect(botEditorService.saveApproval).toHaveBeenCalledWith('bot-1', false, undefined));
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

    render(<BotGeneralConfigDialog bot={rowBot('team')} onClose={jest.fn()} />);

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
    expect(collaborationSwitches).toHaveLength(4);
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

    const first = render(<BotGeneralConfigDialog bot={rowBot('team')} onClose={jest.fn()} />);
    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('switch', { name: '开启共同编辑者发布需 Owner 审批' }));
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: '关闭共同编辑者发布需 Owner 审批' })).toBeInTheDocument(),
    );
    first.unmount();

    render(<BotGeneralConfigDialog bot={rowBot('team')} onClose={jest.fn()} />);
    await waitFor(() => expect(screen.getByText('参与协作群聊')).toBeInTheDocument());
    expect(screen.getByRole('switch', { name: '开启共同编辑者发布需 Owner 审批' })).toBeInTheDocument();
    expect(botEditorService.loadApproval).toHaveBeenCalledTimes(2);
  });
});
