/** @jest-environment jsdom */

import BotTable from '@/components/BotWorkshop/BotCard';
import type { BotTableProps } from '@/components/BotWorkshop/BotCard/BotTable';
import * as botController from '@/services/backendApi/bots/botController';
import type { BotDomain } from '@/services/botWorkshop';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { getInventoryActionAvailability } from '@/services/botWorkshop/botPolicy';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/hooks/useDesktopStartProgress', () => ({
  useDesktopStartProgress: () => ({
    state: { stepIndex: 1, outcome: 'running', percent: 42, detail: '42 MB / 100 MB' },
    error: '',
    retry: jest.fn(),
  }),
}));

const noop = () => undefined;

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
});

function renderTable(bot: BotDomain, props: Partial<BotTableProps> = {}) {
  return render(<BotTable bots={[bot]} onView={noop} {...props} />);
}

/** 第 0 行是表头,数据行从 1 开始。 */
function dataRow() {
  return screen.getAllByRole('row')[1];
}

function cellForHeader(name: string) {
  const headerIndex = screen.getAllByRole('columnheader').findIndex((header) => header.textContent === name);
  return dataRow().querySelectorAll('td')[headerIndex];
}

function versionCell() {
  return cellForHeader('版本');
}

function tagsCell() {
  return cellForHeader('Bot 标签');
}

function containersCell() {
  return cellForHeader('容器信息');
}

describe('BotTable 表格结构', () => {
  const bot = mapBotDto({ bot_id: 'b1', bot_name: 'Openclaw Bot', engine: 'openclaw', status: 'ACTIVE' }).item;

  test('预发服务 Bot 使用预发验证按钮打开详情，而不是进入对话协作', () => {
    const serviceBot = mapBotDto({
      bot_id: 'verify-bot', bot_name: '验证 Bot', engine: 'openclaw', bot_type: 'service',
      status: 'ACTIVE', publish: { id: 1, status: 'VALIDATING', version: 1 },
      actions: ['view', 'publish_online', 'restart_publish', 'cancel_staging'],
    }).item;
    const onEdit = jest.fn();
    const onConversation = jest.fn();
    renderTable(serviceBot, {
      onEdit, onConversation,
      getInventoryActions: (item) => ({
        view: getInventoryActionAvailability(item, 'view'),
        edit: getInventoryActionAvailability(item, 'edit'),
        chat: getInventoryActionAvailability(item, 'chat'),
      }),
    });
    fireEvent.click(screen.getByRole('button', { name: '预发验证 验证 Bot' }));
    expect(onEdit).toHaveBeenCalledWith(serviceBot);
    expect(onConversation).not.toHaveBeenCalled();
  });

  test('描述恒显独立列，容器信息仍独立于 Bot 标签', () => {
    renderTable(bot);

    expect(screen.getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Bot信息',
      '描述',
      '状态',
      '版本',
      'Bot 标签',
      '容器信息',
      '操作',
    ]);
    expect(screen.getByRole('table', { name: 'Bot 列表' })).toHaveClass('min-w-[1132px]');
    expect(screen.getByRole('columnheader', { name: '操作' })).toHaveClass('sticky', 'right-0');
  });

  test('长描述保留独立列，避免重要信息被截短', () => {
    const longDescriptionBot = {
      ...bot,
      description: '用于承接跨团队知识问答与自动化任务处理的服务机器人，包含完整的业务上下文。',
    };
    renderTable(longDescriptionBot);

    expect(screen.getByRole('columnheader', { name: '描述' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Bot 列表' })).toHaveClass('min-w-[1132px]');
  });

  test('数据行固定 h-18（dmore 稿 72px）行高并带 hover 反馈', () => {
    renderTable(bot);

    expect(dataRow().className).toContain('h-18');
    expect(dataRow().className).toContain('hover:bg-muted/40');
    expect(dataRow().className).toContain('transition-colors');
  });

  test('行点击进入详情', () => {
    const onView = jest.fn();
    render(<BotTable bots={[bot]} onView={onView} />);

    fireEvent.click(dataRow());

    expect(onView).toHaveBeenCalledWith(bot);
  });

  test('无详情权限时行点击只触发基础查看，即使 Coding Bot 也不进入对话', () => {
    const restrictedBot = mapBotDto({
      bot_id: 'restricted-coding',
      bot_name: '受限 Coding Bot',
      engine: 'claude_code',
      template_type: 'generalCC',
      display_state: 'running',
    }).item;
    const onView = jest.fn();
    const onConversation = jest.fn();
    render(
      <BotTable bots={[restrictedBot]} onView={onView} onConversation={onConversation} canEnterDetail={() => false} />,
    );

    fireEvent.click(dataRow());

    expect(onView).toHaveBeenCalledWith(restrictedBot);
    expect(onConversation).not.toHaveBeenCalled();
  });

  test('部署中的桌面 Bot 通过折叠行展示初始化步骤，且整行不可进入详情', () => {
    const deployingBot = mapBotDto({
      bot_id: 'desktop-deploying',
      bot_name: 'Desktop Bot',
      bot_type: 'desktop',
      engine: 'openclaw',
      status: 'PENDING',
    }).item;
    const onView = jest.fn();
    render(<BotTable bots={[deployingBot]} onView={onView} />);

    expect(dataRow()).not.toHaveAttribute('tabindex');
    fireEvent.click(dataRow());
    expect(onView).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '展开桌面 Bot 状态详情' }));
    expect(screen.getByText('桌面环境初始化')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: '桌面 Bot 初始化步骤' })).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: '下载进度' })).toHaveAttribute('value', '42');

    fireEvent.click(screen.getByRole('button', { name: '收起桌面 Bot 状态详情' }));
    expect(screen.queryByText('桌面环境初始化')).not.toBeInTheDocument();
  });

  test('离线桌面 Bot 将打开客户端入口放入折叠详情', () => {
    const offlineBot = mapBotDto({
      bot_id: 'desktop-offline',
      bot_name: 'Offline Desktop Bot',
      bot_type: 'desktop',
      engine: 'openclaw',
      display_state: 'local_offline',
    }).item;
    render(<BotTable bots={[offlineBot]} onView={noop} />);

    expect(screen.getByText('已下线')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开客户端' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开桌面 Bot 状态详情' }));
    expect(screen.getByText('设备离线')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开客户端' })).toBeInTheDocument();
  });

  test('机器人信息列渲染 40px DiceBear 头像（dmore 稿）、名称与 entityKey', () => {
    renderTable(bot);

    const avatar = dataRow().querySelector('img');
    expect(avatar).toHaveAttribute('src', expect.stringMatching(/^data:image\/svg\+xml/));
    expect(avatar).toHaveClass('size-10', 'rounded-full');
    expect(screen.getByText('Openclaw Bot')).toBeInTheDocument();
    expect(screen.getByText(bot.entityKey)).toBeInTheDocument();
  });

  test('没有数据时只渲染表头,空态由页面承担', () => {
    render(<BotTable bots={[]} onView={noop} />);

    expect(screen.getAllByRole('columnheader')).toHaveLength(7);
    expect(screen.getAllByRole('row')).toHaveLength(1);
    expect(screen.queryByText('暂无 Bot')).not.toBeInTheDocument();
  });
});

describe('BotTable health check action', () => {
  test('健康检查入口随「三个点」气泡菜单展示并触发回调', () => {
    const bot = mapBotDto({ bot_id: 'b1', bot_name: 'Openclaw Bot', engine: 'openclaw', status: 'ACTIVE' }).item;
    const onHealthCheck = jest.fn();
    renderTable(bot, {
      onHealthCheck,
      getHealthCheckAvailability: () => ({ action: 'health-check', visible: true, enabled: true }),
      onAction: jest.fn().mockResolvedValue(undefined),
    });

    fireEvent.click(screen.getByRole('button', { name: '管理 Openclaw Bot' }));
    screen.getByRole('button', { name: 'Openclaw Bot 健康检查' });
    fireEvent.click(screen.getByRole('button', { name: 'Openclaw Bot 健康检查' }));
    expect(onHealthCheck).toHaveBeenCalledWith(bot);
  });

  test('hides health check when availability is invisible', () => {
    const bot = mapBotDto({ bot_id: 'b2', bot_name: 'TEClaw Bot', engine: 'teclaw', status: 'ACTIVE' }).item;
    renderTable(bot, {
      onHealthCheck: noop,
      getHealthCheckAvailability: () => ({ action: 'health-check', visible: false, enabled: false }),
      onAction: jest.fn().mockResolvedValue(undefined),
    });

    fireEvent.click(screen.getByRole('button', { name: '管理 TEClaw Bot' }));
    expect(screen.queryByRole('button', { name: 'TEClaw Bot 健康检查' })).not.toBeInTheDocument();
  });
});

describe('BotTable task instances action', () => {
  test('opens the scoped list from the「三个点」menu for an explicit collaborator', () => {
    const bot = mapBotDto({ bot_id: 'task-bot', bot_name: 'Task Bot', engine: 'openclaw', status: 'ACTIVE' }).item;
    const onOpenTaskInstances = jest.fn();
    renderTable(bot, {
      onOpenTaskInstances,
      canOpenTaskInstances: () => true,
      onOpenLogs: noop,
      getLogAction: () => ({ action: 'logs', visible: true, enabled: true }),
      onAction: jest.fn().mockResolvedValue(undefined),
    });

    expect(screen.getByRole('button', { name: '查看 Task Bot 日志' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '管理 Task Bot' }));
    fireEvent.click(screen.getByRole('button', { name: '查看 Task Bot 任务实例' }));
    expect(onOpenTaskInstances).toHaveBeenCalledWith(bot);
  });

  test('hides task instances from an unrelated space member', () => {
    const bot = mapBotDto({ bot_id: 'other-bot', bot_name: 'Other Bot', engine: 'openclaw', status: 'ACTIVE' }).item;
    renderTable(bot, { onOpenTaskInstances: jest.fn(), canOpenTaskInstances: () => false, onAction: jest.fn().mockResolvedValue(undefined) });

    fireEvent.click(screen.getByRole('button', { name: '管理 Other Bot' }));
    expect(screen.queryByRole('button', { name: '查看 Other Bot 任务实例' })).not.toBeInTheDocument();
  });
});

describe('BotTable conversation action', () => {
  test('renders the conversation entry and delegates navigation', () => {
    const bot = mapBotDto({
      bot_id: 'b3',
      bot_name: 'Chat Bot',
      engine: 'openclaw',
      status: 'ACTIVE',
      actions: ['chat', 'view'],
    }).item;
    const onConversation = jest.fn();

    renderTable(bot, {
      onConversation,
      getInventoryActions: () => ({ chat: { action: 'chat', visible: true, enabled: true } }),
    });
    fireEvent.click(screen.getByRole('button', { name: '与 Chat Bot 对话' }));

    expect(onConversation).toHaveBeenCalledWith(bot);
  });
  test('非 Owner 对话入口置灰', () => {
    const bot = mapBotDto({ bot_id: 'b4', bot_name: 'Shared Bot', engine: 'openclaw', actions: ['chat'] }).item;
    const onConversation = jest.fn();
    renderTable(bot, {
      onConversation,
      canOpenConversation: () => false,
      getInventoryActions: () => ({ chat: { action: 'chat', visible: true, enabled: true } }),
    });
    const chatButton = screen.getByRole('button', { name: '与 Shared Bot 对话' });
    expect(chatButton).toBeDisabled();
    expect(onConversation).not.toHaveBeenCalled();
  });

  test('同一时刻只展开一个更多操作菜单', () => {
    const first = mapBotDto({ bot_id: 'first', bot_name: 'First Bot', actions: ['view'] }).item;
    const second = mapBotDto({ bot_id: 'second', bot_name: 'Second Bot', actions: ['view'] }).item;
    render(<BotTable bots={[first, second]} onView={noop} onAction={async () => undefined} />);

    const [firstTrigger, secondTrigger] = screen.getAllByRole('button', { name: /管理 .* Bot/ });
    fireEvent.click(firstTrigger);
    expect(firstTrigger).toHaveAttribute('data-state', 'open');
    fireEvent.click(secondTrigger);
    expect(firstTrigger).toHaveAttribute('data-state', 'closed');
    expect(secondTrigger).toHaveAttribute('data-state', 'open');
  });

  test('同一 Bot 的不同卡片也只展开触发的一个更多操作菜单', () => {
    const first = mapBotDto({
      bot_id: 'same-bot',
      card_id: 'service:same-bot:1',
      bot_name: 'First Bot',
      actions: ['view'],
    }).item;
    const second = mapBotDto({
      bot_id: 'same-bot',
      card_id: 'service:same-bot:2',
      bot_name: 'Second Bot',
      actions: ['view'],
    }).item;
    render(<BotTable bots={[first, second]} onView={noop} onAction={async () => undefined} />);

    const [firstTrigger, secondTrigger] = screen.getAllByRole('button', { name: /管理 .* Bot/ });
    fireEvent.click(firstTrigger);
    expect(firstTrigger).toHaveAttribute('data-state', 'open');
    expect(secondTrigger).toHaveAttribute('data-state', 'closed');
    fireEvent.click(secondTrigger);
    expect(firstTrigger).toHaveAttribute('data-state', 'closed');
    expect(secondTrigger).toHaveAttribute('data-state', 'open');
  });
  test('异常态 Coding Bot 的去使用入口置灰', () => {
    const bot = mapBotDto({
      bot_id: 'failed-coding',
      bot_name: 'Failed Coding Bot',
      engine: 'claude_code',
      template_type: 'generalCC',
      engine_properties: { template_config: { template_name: 'Coding Bot' } },
      status: 'FAILED',
    }).item;
    renderTable(bot, { onConversation: jest.fn() });

    expect(screen.getByRole('button', { name: '去使用 Failed Coding Bot' })).toBeDisabled();
  });
  test('团队空间新增 Owner 列并显示名称', () => {
    const bot = {
      ...mapBotDto({
        bot_id: 'b5',
        bot_name: 'Team Bot',
        engine: 'openclaw',
        owner_entity_id: '1001',
        space: { space_id: '1', kind: 'team' },
      }).item,
      ownerName: '小华',
    };
    renderTable(bot);
    expect(screen.getByRole('columnheader', { name: 'Owner' })).toBeInTheDocument();
    expect(screen.getByText('小华')).toBeInTheDocument();
  });
});

describe('BotTable engine version entry points', () => {
  test('shows a confirmed update badge and separates version update from ordinary restart', async () => {
    const bot = mapBotDto({
      bot_id: 'engine-bot',
      bot_name: 'Engine Bot',
      owner_entity_id: 'owner-1',
      engine: 'openclaw',
      status: 'ACTIVE',
      actions: ['engine_restart', 'restart'],
    }).item;
    const getVersion = jest.spyOn(botController, 'getBotEngineVersion').mockResolvedValue({
      code: 200000,
      data: {
        detection: { status: 'known' },
        current: { raw_version: '1.2.0' },
        release_target: { status: 'available', release_id: 'REL-2' },
        comparison: 'older',
        restart_hint: 'recommended',
        context: { bot_id: 'engine-bot', stage: 'draft', checked_at: '2026-10-20T00:00:00Z' },
      },
    } as never);
    try {
      renderTable(bot, {
        containerUserId: 'caller-1',
        onAction: async () => undefined,
      });

      fireEvent.click(await screen.findByRole('button', { name: /引擎有更新/ }));
      expect(await screen.findByText('1.2.0')).toBeInTheDocument();
      expect(screen.getByText('REL-2')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '重启并更新' })).toBeInTheDocument();
      expect(getVersion).toHaveBeenCalledWith(
        'engine-bot',
        expect.objectContaining({
          userId: 'caller-1',
          ownerId: 'owner-1',
          stage: 'draft',
        }),
      );

      fireEvent.click(screen.getByRole('button', { name: '取消' }));
      fireEvent.click(screen.getByRole('button', { name: '管理 Engine Bot' }));
      expect(await screen.findByRole('button', { name: '引擎信息' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '更新引擎版本' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /重启 Bot/ })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /重启引擎/ })).toBeInTheDocument();
    } finally {
      getVersion.mockRestore();
    }
  });
});

test('TeClaw 即使后端声明重启动作也置灰容器与引擎入口', () => {
  const bot = mapBotDto({
    bot_id: 'te-1',
    bot_name: 'Te Bot',
    engine: 'teclaw',
    status: 'ACTIVE',
    actions: ['restart', 'engine_restart', 'view'],
  }).item;
  renderTable(bot, { onAction: jest.fn().mockResolvedValue(undefined) });
  fireEvent.click(screen.getByRole('button', { name: '管理 Te Bot' }));
  expect(screen.getByRole('button', { name: /重启 Bot/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /重启引擎/ })).toBeDisabled();
});

describe('BotTable backend action contract', () => {
  test('does not render chat or edit when backend only allows view', () => {
    const bot = mapBotDto({
      bot_id: 'offline-service',
      bot_name: 'Offline Bot',
      engine: 'openclaw',
      kind: 'service',
      display_state: 'service_offline',
      actions: ['view'],
    }).item;

    renderTable(bot, {
      onConversation: noop,
      onEdit: noop,
      getInventoryActions: () => ({ view: { action: 'view', visible: true, enabled: true } }),
    });

    expect(screen.getByRole('button', { name: '查看 Offline Bot 详情' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '与 Offline Bot 对话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑 Offline Bot' })).not.toBeInTheDocument();
  });

  test('renders a disabled edit action with the backend reason', () => {
    const bot = mapBotDto({
      bot_id: 'local-offline',
      bot_name: 'Local Offline Bot',
      engine: 'openclaw',
      kind: 'local',
      display_state: 'local_offline',
      actions: ['view'],
      disabled_actions: { edit: 'device offline' },
    }).item;

    renderTable(bot, {
      onEdit: noop,
      getInventoryActions: () => ({
        edit: { action: 'edit', visible: true, enabled: false, disabledReason: 'device offline' },
      }),
    });

    expect(screen.getByRole('button', { name: '编辑 Local Offline Bot' })).toBeDisabled();
  });
});

describe('BotTable management actions', () => {
  test('closes the management menu before opening delete confirmation', () => {
    const bot = mapBotDto({
      bot_id: 'b4',
      bot_name: 'Delete Bot',
      engine: 'openclaw',
      status: 'ACTIVE',
      actions: ['delete'],
    }).item;

    renderTable(bot, { onAction: jest.fn() });
    fireEvent.click(screen.getByRole('button', { name: '管理 Delete Bot' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByText('确认删除 Bot')).toBeInTheDocument();
    expect(screen.queryByText('变更归属空间')).not.toBeInTheDocument();
  });

  test('shows a red lock and confirms stealing another editor lock', async () => {
    const onClaimLock = jest.fn().mockResolvedValue(undefined);
    const bot = {
      ...mapBotDto({
        bot_id: 'b5',
        bot_name: 'Locked Service Bot',
        bot_type: 'service',
        kind: 'service',
        engine: 'openclaw',
        display_state: 'service_draft',
      }).item,
      ownership: 'team' as const,
      lock: { status: 'other' as const, holderName: '李四', lockedAt: '2026-08-26 10:00' },
    };

    renderTable(bot, { onClaimLock });
    fireEvent.click(screen.getByRole('button', { name: '抢占 Locked Service Bot 的编辑锁' }));

    const dialog = within(screen.getByRole('alertdialog'));
    expect(dialog.getByText('该 Bot 正在被编辑')).toBeInTheDocument();
    expect(dialog.getByText(/李四/)).toBeInTheDocument();
    expect(dialog.getByText(/2026-08-26 10:00/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '抢锁并编辑' }));

    await waitFor(() => expect(onClaimLock).toHaveBeenCalledWith(bot));
  });
});

describe('BotTable 通用配置菜单项（collab-permission-entry-migration AC-01~03）', () => {
  const bot = mapBotDto({ bot_id: 'b-gc', bot_name: '配置 Bot', engine: 'openclaw', status: 'ACTIVE' }).item;

  test('门禁可用时菜单项可点击并触发回调', () => {
    const onGeneralConfig = jest.fn();
    renderTable(bot, {
      onAction: jest.fn(),
      getGeneralConfigAvailability: () => ({ enabled: true }),
      onGeneralConfig,
    });
    fireEvent.click(screen.getByRole('button', { name: '管理 配置 Bot' }));
    fireEvent.click(screen.getByRole('button', { name: '通用配置' }));

    expect(onGeneralConfig).toHaveBeenCalledWith(bot);
  });

  test('门禁不可用时菜单项禁用', () => {
    renderTable(bot, {
      onAction: jest.fn(),
      getGeneralConfigAvailability: () => ({ enabled: false, disabledReason: '仅 Bot 管理员可变更通用配置' }),
      onGeneralConfig: jest.fn(),
    });
    fireEvent.click(screen.getByRole('button', { name: '管理 配置 Bot' }));

    expect(screen.getByRole('button', { name: /通用配置/ })).toBeDisabled();
  });

  test('未传回调时不渲染通用配置菜单项', () => {
    renderTable(bot, { onAction: jest.fn() });
    fireEvent.click(screen.getByRole('button', { name: '管理 配置 Bot' }));

    expect(screen.queryByRole('button', { name: /通用配置/ })).not.toBeInTheDocument();
  });
});

describe('BotTable runtime labels', () => {
  test('服务 Bot 版本列只显示发布版本,不再重复显示主版本文字', () => {
    const bot = mapBotDto({
      bot_id: 'service-version-row',
      card_id: 'service-version-row:3',
      bot_name: '版本 Bot',
      kind: 'service',
      live_version: 2,
      publication_version: 3,
      display_state: 'service_online',
    }).item;

    renderTable(bot);

    expect(within(versionCell()).getByText('V3')).toBeInTheDocument();
    expect(within(versionCell()).queryByText('v2')).not.toBeInTheDocument();
  });

  test('服务 Bot 缺发布版本时以占位符兜底', () => {
    const bot = mapBotDto({
      bot_id: 'no-publication-version',
      bot_name: '无发布版本 Bot',
      kind: 'service',
      display_state: 'service_online',
    }).item;

    renderTable(bot);

    expect(within(versionCell()).getByText('—')).toBeInTheDocument();
  });

  test('非服务化 Bot 无版本,以占位符兜底', () => {
    const bot = mapBotDto({
      bot_id: 'non-service-version-row',
      bot_name: '非服务 Bot',
      engine: 'openclaw',
      status: 'ACTIVE',
    }).item;

    renderTable(bot);

    expect(within(versionCell()).getByText('—')).toBeInTheDocument();
  });

  test('创建失败展示准确状态', () => {
    const bot = mapBotDto({
      bot_id: 'failed-row',
      bot_name: '失败 Bot',
      display_state: 'failed',
      status: 'FAILED',
      disabled_actions: { restart: 'bot provisioning failed' },
    }).item;

    renderTable(bot);

    expect(screen.getByText('创建失败')).toBeInTheDocument();
  });

  test('Coding Bot 展示模板名称而不是 claude_code', () => {
    const bot = mapBotDto({
      bot_id: 'architect-row',
      bot_name: '架构 Bot 实例',
      engine: 'claude_code',
      template_type: 'generalCC',
      engine_properties: { template_config: { template_name: '架构 Bot' } },
      status: 'ACTIVE',
    }).item;

    renderTable(bot);

    expect(screen.getByText('架构 Bot')).toBeInTheDocument();
    expect(screen.queryByText('claude_code')).not.toBeInTheDocument();
  });

  test('历史个人 Coding Bot 缺少模板名称时展示个人 Coding Bot', () => {
    const bot = mapBotDto({
      bot_id: 'personal-coding-row',
      bot_name: '个人 Coding Bot 实例',
      engine: 'claude_code',
      template_type: 'personalCoding',
      status: 'ACTIVE',
    }).item;

    renderTable(bot);

    expect(screen.getByText('个人 Coding Bot')).toBeInTheDocument();
    expect(screen.queryByText('claude_code')).not.toBeInTheDocument();
  });

  test('普通 Claude Code 仍展示引擎标签', () => {
    const bot = mapBotDto({
      bot_id: 'normal-row',
      bot_name: '普通 CC',
      engine: 'claude_code',
      template_type: 'normalCC',
      status: 'ACTIVE',
    }).item;

    renderTable(bot);

    expect(screen.getByText('claude_code')).toBeInTheDocument();
  });
});

describe('BotTable bot 标签列', () => {
  test('服务 Bot 展示「服务化」chip', () => {
    const bot = mapBotDto({
      bot_id: 'service-tag',
      bot_name: '发布管理 Bot',
      kind: 'service',
      display_state: 'service_online',
    }).item;

    renderTable(bot);

    expect(screen.getByText('服务化')).toBeInTheDocument();
  });

  test('非服务 Bot 不显示服务化 chip,标签列不补占位符', () => {
    const bot = mapBotDto({
      bot_id: 'non-service-tag',
      bot_name: '个人 Bot 标签',
      kind: 'personal',
      status: 'ACTIVE',
    }).item;

    renderTable(bot);

    expect(screen.queryByText('服务化')).not.toBeInTheDocument();
    // 标签列不再贡献占位符；第二个 `—` 来自独立的容器信息列。
    expect(screen.getAllByText('—')).toHaveLength(2);
    expect(within(versionCell()).getByText('—')).toBeInTheDocument();
    expect(within(tagsCell()).queryByText('—')).not.toBeInTheDocument();
    expect(within(containersCell()).getByText('—')).toBeInTheDocument();
  });

  test('标签列统一中性底色,不再按引擎/部署/服务化涂装彩色', () => {
    const bot = mapBotDto({
      bot_id: 'tone-tag',
      bot_name: '配色 Bot',
      engine: 'openclaw',
      kind: 'service',
      display_state: 'service_online',
    }).item;

    renderTable(bot);

    expect(within(tagsCell()).getByText('openclaw')).toHaveClass('bg-muted', 'text-muted-foreground');
    expect(within(tagsCell()).getByText('云端')).toHaveClass('bg-muted', 'text-muted-foreground');
    expect(within(tagsCell()).getByText('服务化')).toHaveClass('bg-muted', 'text-muted-foreground');
  });
});

describe('Agent Coding Bot row actions', () => {
  test('Coding Bot 即使没有 chat action 也固定展示去使用', () => {
    const bot = mapBotDto({
      bot_id: 'general-service-draft-without-chat',
      bot_name: 'GeneralCC 草稿 Bot',
      engine: 'claude_code',
      template_type: 'generalCC',
      bot_type: 'service',
      kind: 'service',
      display_state: 'service_draft',
      actions: ['restart', 'delete'],
    }).item;
    const onConversation = jest.fn();

    renderTable(bot, {
      onConversation,
      onAction: jest.fn().mockResolvedValue(undefined),
    });

    fireEvent.click(screen.getByRole('button', { name: '去使用 GeneralCC 草稿 Bot' }));
    expect(onConversation).toHaveBeenCalledWith(bot);
  });

  test('generalCC 服务 Bot 草稿态展示发布与阶段推进', () => {
    const bot = mapBotDto({
      bot_id: 'general-service-draft',
      bot_name: 'GeneralCC 服务 Bot',
      engine: 'claude_code',
      template_type: 'generalCC',
      bot_type: 'service',
      kind: 'service',
      display_state: 'service_draft',
      actions: ['chat', 'restart', 'delete'],
    }).item;

    renderTable(bot, {
      onConversation: noop,
      onAction: jest.fn().mockResolvedValue(undefined),
      onManagePublication: noop,
      getInventoryActions: () => ({ chat: { action: 'chat', visible: true, enabled: true } }),
    });

    fireEvent.click(screen.getByRole('button', { name: '管理 GeneralCC 服务 Bot' }));
    expect(screen.getByText('发布与阶段推进')).toBeInTheDocument();
  });

  test('Bot 成员点击 Coding Bot 去使用时进入 Coding Chat', () => {
    const bot = mapBotDto({
      bot_id: 'collaborator-coding-bot',
      bot_name: '协作者 Coding Bot',
      owner_id: 'owner-1',
      active_engine: 'claude_code',
      template_type: 'myTemplate',
      bot_type: 'personal',
      display_state: 'running',
      actions: ['view', 'chat', 'edit'],
    }).item;
    const onConversation = jest.fn();

    renderTable(bot, {
      onConversation,
      onAction: jest.fn().mockResolvedValue(undefined),
      canOpenConversation: (item) => item.actions.includes('edit'),
    });

    const useButton = screen.getByRole('button', { name: '去使用 协作者 Coding Bot' });
    expect(useButton).toBeEnabled();
    fireEvent.click(useButton);
    expect(onConversation).toHaveBeenCalledWith(bot);
  });

  test('普通空间成员不能使用非协作的 Coding Bot', () => {
    const bot = mapBotDto({
      bot_id: 'space-member-coding-bot',
      bot_name: '空间成员 Coding Bot',
      owner_id: 'owner-1',
      active_engine: 'claude_code',
      template_type: 'myTemplate',
      bot_type: 'personal',
      display_state: 'running',
      actions: ['view', 'chat'],
    }).item;
    const onConversation = jest.fn();
    renderTable(bot, {
      onConversation,
      onAction: jest.fn().mockResolvedValue(undefined),
      canOpenConversation: (item) => item.actions.includes('edit'),
    });

    const useButton = screen.getByRole('button', { name: '去使用 空间成员 Coding Bot' });
    expect(useButton).toBeDisabled();
    expect(onConversation).not.toHaveBeenCalled();
  });

  test('Coding Bot 行内仅固定展示去使用，任务实例与更多操作收口在气泡菜单', () => {
    const bot = mapBotDto({
      bot_id: 'agent-template',
      bot_name: 'Agent Coding 模版 Bot',
      active_engine: 'claude_code',
      template_type: 'myTemplate',
      engine_properties: { template_config: { capabilities: { upgrade_service_bot: true } } },
      bot_type: 'personal',
      display_state: 'running',
      actions: ['chat', 'view', 'edit', 'restart', 'delete'],
    }).item;

    const onAuthorize = jest.fn();
    const onOpenTaskInstances = jest.fn();

    renderTable(bot, {
      onConversation: noop,
      onEdit: noop,
      onHealthCheck: noop,
      getHealthCheckAvailability: () => ({ action: 'health-check', visible: false, enabled: false }),
      getLogAction: () => ({ action: 'logs', visible: true, enabled: true }),
      onOpenLogs: noop,
      onOpenTaskInstances,
      canOpenTaskInstances: () => false,
      onChangeSpace: noop,
      onAuthorize,
      getCollaborationMode: () => 'authorize',
      onAction: jest.fn().mockResolvedValue(undefined),
      getInventoryActions: () => ({
        chat: { action: 'chat', visible: true, enabled: true },
        view: { action: 'view', visible: true, enabled: true },
        edit: { action: 'edit', visible: true, enabled: true },
      }),
    });

    expect(screen.getByRole('button', { name: '去使用 Agent Coding 模版 Bot' })).toBeInTheDocument();
    const primaryActions = within(screen.getByRole('group', { name: 'Agent Coding 模版 Bot 操作' }));
    // 行内只剩「去使用」一个入口；任务实例/健康检查/管理操作在三个点气泡内。
    expect(primaryActions.getAllByRole('button')).toHaveLength(1);
    expect(screen.queryByRole('button', { name: '查看 Agent Coding 模版 Bot 详情' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑 Agent Coding 模版 Bot' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Agent Coding 模版 Bot 健康检查' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看 Agent Coding 模版 Bot 日志' })).not.toBeInTheDocument();

    expect(screen.getByRole('button', { name: '管理 Agent Coding 模版 Bot' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '管理 Agent Coding 模版 Bot' }));
    expect(screen.getByText('开启服务化')).toBeInTheDocument();
    expect(screen.getByText('重启 Bot')).toBeInTheDocument();
    expect(screen.getByLabelText('重启 Bot说明')).toBeInTheDocument();
    expect(screen.getByText('变更归属空间')).toBeInTheDocument();
    expect(screen.getByText('删除')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重启引擎/ })).not.toBeInTheDocument();
    const authorizeButton = screen.getByRole('button', { name: '授权' });
    expect(authorizeButton).toBeInTheDocument();
    fireEvent.click(authorizeButton);
    expect(onAuthorize).toHaveBeenCalledWith(bot);

    // 授权点击关闭气泡；重新打开后验证 Coding Bot 固定的任务实例入口可达。
    fireEvent.click(screen.getByRole('button', { name: '管理 Agent Coding 模版 Bot' }));
    const taskInstancesButton = screen.getByRole('button', {
      name: '查看 Agent Coding 模版 Bot 任务实例',
    });
    expect(taskInstancesButton).toBeInTheDocument();
    fireEvent.click(taskInstancesButton);
    expect(onOpenTaskInstances).toHaveBeenCalledWith(bot);
  });

  test('服务化 Coding Bot 暂不展示重启引擎入口', () => {
    const bot = mapBotDto({
      bot_id: 'coding-service-engine-restart',
      bot_name: '服务化 Coding Bot',
      engine: 'claude_code',
      template_type: 'generalCC',
      bot_type: 'service',
      kind: 'service',
      display_state: 'service_draft',
      actions: ['chat', 'delete'],
    }).item;

    renderTable(bot, {
      onAction: jest.fn().mockResolvedValue(undefined),
    });

    fireEvent.click(screen.getByRole('button', { name: '管理 服务化 Coding Bot' }));

    expect(screen.queryByRole('button', { name: /重启引擎/ })).not.toBeInTheDocument();
  });

  test('Coding Bot 点击整行进入对话而非详情', () => {
    const bot = mapBotDto({
      bot_id: 'general-row-click',
      bot_name: 'GeneralCC 行点击 Bot',
      engine: 'claude_code',
      template_type: 'generalCC',
      bot_type: 'personal',
      display_state: 'running',
      actions: ['chat'],
    }).item;
    const onConversation = jest.fn();
    const onView = jest.fn();

    render(<BotTable bots={[bot]} onView={onView} onConversation={onConversation} />);

    fireEvent.click(dataRow());

    expect(onConversation).toHaveBeenCalledWith(bot);
    expect(onView).not.toHaveBeenCalled();
  });
});

describe('BotTable 管理菜单重启词表（服务卡三动词分裂）', () => {
  const servicePrestableBot = () =>
    mapBotDto({
      bot_id: 'service-prestable-card',
      bot_name: '预发服务 Bot',
      engine: 'openclaw',
      kind: 'service',
      bot_type: 'service',
      display_state: 'service_prestable',
      actions: ['view', 'publish_online', 'restart_publish', 'cancel_staging'],
    }).item;

  test('服务预发行仅提供重启发布入口，不再提供容器/引擎重启', () => {
    const bot = servicePrestableBot();
    renderTable(bot, { onAction: jest.fn().mockResolvedValue(undefined) });

    fireEvent.click(screen.getByRole('button', { name: '管理 预发服务 Bot' }));

    expect(screen.getByRole('button', { name: /重启发布/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /重启 Bot/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重启引擎/ })).not.toBeInTheDocument();
  });

  test('服务预发行点击重启发布后经确认弹窗分发 restart_publish 动作', async () => {
    const bot = servicePrestableBot();
    const onAction = jest.fn().mockResolvedValue(undefined);
    renderTable(bot, { onAction });

    fireEvent.click(screen.getByRole('button', { name: '管理 预发服务 Bot' }));
    fireEvent.click(screen.getByRole('button', { name: /重启发布/ }));

    const dialog = within(screen.getByRole('alertdialog'));
    expect(dialog.getByText('重启发布')).toBeInTheDocument();
    expect(dialog.getByText(/草稿机器与草稿数据不受影响/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '确认' }));

    await waitFor(() => expect(onAction).toHaveBeenCalledWith('restart_publish', bot));
  });

  test('服务上线行提供重启发布并经确认分发', async () => {
    const bot = mapBotDto({
      bot_id: 'service-online-card',
      bot_name: '上线服务 Bot',
      engine: 'openclaw',
      kind: 'service',
      bot_type: 'service',
      display_state: 'service_online',
      actions: ['view', 'chat', 'restart_publish', 'upgrade', 'offline'],
    }).item;
    const onAction = jest.fn().mockResolvedValue(undefined);
    renderTable(bot, { onAction });

    fireEvent.click(screen.getByRole('button', { name: '管理 上线服务 Bot' }));
    expect(screen.getByRole('button', { name: /重启发布/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /重启发布/ }));
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '确认' }));

    await waitFor(() => expect(onAction).toHaveBeenCalledWith('restart_publish', bot));
  });

  test('服务草稿行保留重启 Bot，不提供重启发布与重启引擎', () => {
    const bot = mapBotDto({
      bot_id: 'service-draft-card',
      bot_name: '草稿服务 Bot',
      engine: 'openclaw',
      kind: 'service',
      bot_type: 'service',
      display_state: 'service_draft',
      actions: ['view', 'edit', 'publish_staging', 'restart', 'delete'],
    }).item;
    renderTable(bot, { onAction: jest.fn().mockResolvedValue(undefined) });

    fireEvent.click(screen.getByRole('button', { name: '管理 草稿服务 Bot' }));

    expect(screen.getByRole('button', { name: /重启 Bot/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /重启发布/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重启引擎/ })).not.toBeInTheDocument();
  });

  test('创建失败的服务 Bot 可重启，但删除会明确说明后端生命周期限制', () => {
    const bot = mapBotDto({
      bot_id: 'service-creation-failed',
      bot_name: '创建失败服务 Bot',
      engine: 'openclaw',
      kind: 'service',
      bot_type: 'service',
      status: 'FAILED',
      actions: ['view'],
    }).item;
    renderTable(bot, { onAction: jest.fn().mockResolvedValue(undefined) });

    fireEvent.click(screen.getByRole('button', { name: '管理 创建失败服务 Bot' }));

    expect(screen.getByRole('button', { name: /重启 Bot/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /删除/ })).toBeDisabled();
    expect(screen.getByLabelText('删除说明')).toBeInTheDocument();
  });

  test('部署中的服务 Bot 不展示任何操作入口或行点击', () => {
    const bot = mapBotDto({
      bot_id: 'service-deploying',
      bot_name: '部署中服务 Bot',
      engine: 'openclaw',
      kind: 'service',
      bot_type: 'service',
      status: 'PROVISIONING',
      publish: { id: 1, status: 'DRAFT', version: 1 },
      actions: ['view', 'publish_staging'],
    }).item;
    const onView = jest.fn();
    render(
      <BotTable
        bots={[bot]}
        onView={onView}
        onManagePublication={jest.fn()}
        onAction={jest.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.queryByRole('group', { name: '部署中服务 Bot 操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '管理 部署中服务 Bot' })).not.toBeInTheDocument();
    expect(dataRow()).not.toHaveAttribute('tabindex');
    fireEvent.click(dataRow());
    expect(onView).not.toHaveBeenCalled();
  });

  test('服务行仅 disabled_actions.restart 声明时保留禁用入口（死灰按钮可展示服务端原因）', () => {
    const bot = mapBotDto({
      bot_id: 'service-restart-reason-card',
      bot_name: '禁用原因服务 Bot',
      engine: 'openclaw',
      kind: 'service',
      bot_type: 'service',
      display_state: 'service_draft',
      actions: ['view'],
      disabled_actions: { restart: '草稿机未初始化，无法重启' },
    }).item;
    renderTable(bot, { onAction: jest.fn().mockResolvedValue(undefined) });

    fireEvent.click(screen.getByRole('button', { name: '管理 禁用原因服务 Bot' }));

    expect(screen.getByRole('button', { name: /重启 Bot/ })).toBeDisabled();
  });

  test('仅 disabled_actions 声明重启发布时按钮禁用', () => {
    const bot = mapBotDto({
      bot_id: 'service-restart-publish-blocked',
      bot_name: '审批中服务 Bot',
      engine: 'openclaw',
      kind: 'service',
      bot_type: 'service',
      display_state: 'service_online',
      actions: ['view'],
      disabled_actions: { restart_publish: '等待发布人授权' },
    }).item;
    renderTable(bot, { onAction: jest.fn().mockResolvedValue(undefined) });

    fireEvent.click(screen.getByRole('button', { name: '管理 审批中服务 Bot' }));

    expect(screen.getByRole('button', { name: /重启发布/ })).toBeDisabled();
  });

  test('编辑锁被他人持有时重启发布禁用', () => {
    const bot = {
      ...servicePrestableBot(),
      lock: { status: 'other' as const, holderName: '王五', lockedAt: '2026-09-07 09:30' },
    };
    renderTable(bot, { onAction: jest.fn().mockResolvedValue(undefined) });

    fireEvent.click(screen.getByRole('button', { name: '管理 预发服务 Bot' }));

    expect(screen.getByRole('button', { name: /重启发布/ })).toBeDisabled();
  });

  test('个人云行的重启 Bot 与重启引擎不受词表分裂影响', () => {
    const bot = mapBotDto({
      bot_id: 'personal-cloud-card',
      bot_name: '个人云 Bot',
      engine: 'openclaw',
      bot_type: 'personal',
      display_state: 'running',
      actions: ['chat', 'view', 'restart', 'engine_restart'],
    }).item;
    renderTable(bot, { onAction: jest.fn().mockResolvedValue(undefined) });

    fireEvent.click(screen.getByRole('button', { name: '管理 个人云 Bot' }));

    expect(screen.getByRole('button', { name: /重启 Bot/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /重启引擎/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /重启发布/ })).not.toBeInTheDocument();
  });
});
