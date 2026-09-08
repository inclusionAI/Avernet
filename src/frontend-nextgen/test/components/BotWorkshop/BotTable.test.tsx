/** @jest-environment jsdom */

import BotTable from '@/components/BotWorkshop/BotCard';
import type { BotTableProps } from '@/components/BotWorkshop/BotCard/BotTable';
import type { BotDomain } from '@/services/botWorkshop';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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

/** 版本列是第 4 列(索引 3),标签列是第 5 列(索引 4)。 */
function cellAt(index: number) {
  return dataRow().querySelectorAll('td')[index];
}

function versionCell() {
  return cellAt(3);
}

function tagsCell() {
  return cellAt(4);
}

describe('BotTable 表格结构', () => {
  const bot = mapBotDto({ bot_id: 'b1', bot_name: 'Openclaw Bot', engine: 'openclaw', status: 'ACTIVE' }).item;

  test('按 7 列顺序渲染表头', () => {
    renderTable(bot);

    expect(screen.getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      '机器人信息',
      '描述',
      '状态',
      '版本',
      'bot 标签',
      '主要操作',
      '更多操作',
    ]);
  });

  test('数据行固定 h-16 行高并带 hover 反馈', () => {
    renderTable(bot);

    expect(dataRow().className).toContain('h-16');
    expect(dataRow().className).toContain('hover:bg-muted/40');
    expect(dataRow().className).toContain('transition-colors');
  });

  test('行点击进入详情', () => {
    const onView = jest.fn();
    render(<BotTable bots={[bot]} onView={onView} />);

    fireEvent.click(dataRow());

    expect(onView).toHaveBeenCalledWith(bot);
  });

  test('机器人信息列渲染 20px 圆形首字符头像、名称与 entityKey', () => {
    renderTable(bot);

    const avatar = screen.getByText('O');
    expect(avatar).toBeInTheDocument();
    expect(avatar).toHaveClass('h-5', 'w-5', 'rounded-full');
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
  test('renders health check when availability is visible', () => {
    const bot = mapBotDto({ bot_id: 'b1', bot_name: 'Openclaw Bot', engine: 'openclaw', status: 'ACTIVE' }).item;
    renderTable(bot, {
      onHealthCheck: noop,
      getHealthCheckAvailability: () => ({ action: 'health-check', visible: true, enabled: true }),
    });

    expect(screen.getByRole('button', { name: 'Openclaw Bot 健康检查' })).toBeInTheDocument();
  });

  test('hides health check when availability is invisible', () => {
    const bot = mapBotDto({ bot_id: 'b2', bot_name: 'TEClaw Bot', engine: 'teclaw', status: 'ACTIVE' }).item;
    renderTable(bot, {
      onHealthCheck: noop,
      getHealthCheckAvailability: () => ({ action: 'health-check', visible: false, enabled: false }),
    });

    expect(screen.queryByRole('button', { name: 'TEClaw Bot 健康检查' })).not.toBeInTheDocument();
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

describe('BotTable runtime labels', () => {
  test('服务 Bot 版本列只显示发布版本徽章,不再重复显示主版本文字', () => {
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
    // 整行只剩版本列一个 `—`:标签列不再贡献第二个占位符
    expect(screen.getAllByText('—')).toHaveLength(1);
    expect(within(versionCell()).getByText('—')).toBeInTheDocument();
  });

  test('标签列三个 chip 统一 outline 描边,不占用蓝/绿/灰填充', () => {
    const bot = mapBotDto({
      bot_id: 'tone-tag',
      bot_name: '配色 Bot',
      engine: 'openclaw',
      kind: 'service',
      display_state: 'service_online',
    }).item;

    renderTable(bot);

    for (const label of ['openclaw', '云端', '服务化']) {
      expect(within(tagsCell()).getByText(label)).toHaveClass('border-border', 'text-foreground');
    }
    // 蓝(primary)被信息列与操作按钮占用;绿(success)是状态列语义色;灰(neutral)明确排除
    const html = tagsCell().innerHTML;
    expect(html).not.toContain('bg-primary/10');
    expect(html).not.toContain('bg-success/10');
    expect(html).not.toContain('bg-muted');
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

  test('只展示去使用，管理菜单仅保留指定操作', () => {
    const bot = mapBotDto({
      bot_id: 'agent-template',
      bot_name: 'Agent Coding 模版 Bot',
      active_engine: 'claude_code',
      template_type: 'myTemplate',
      engine_properties: { template_config: { capabilities: { upgrade_service_bot: true } } },
      bot_type: 'personal',
      display_state: 'running',
      actions: ['chat', 'view', 'edit', 'restart', 'engine_restart', 'delete'],
    }).item;

    renderTable(bot, {
      onConversation: noop,
      onEdit: noop,
      onHealthCheck: noop,
      getHealthCheckAvailability: () => ({ action: 'health-check', visible: true, enabled: true }),
      getLogAction: () => ({ action: 'logs', visible: true, enabled: true }),
      onOpenLogs: noop,
      onChangeSpace: noop,
      onAuthorize: noop,
      getCollaborationMode: () => 'authorize',
      onAction: jest.fn().mockResolvedValue(undefined),
      getInventoryActions: () => ({
        chat: { action: 'chat', visible: true, enabled: true },
        view: { action: 'view', visible: true, enabled: true },
        edit: { action: 'edit', visible: true, enabled: true },
      }),
    });

    expect(screen.getByRole('button', { name: '去使用 Agent Coding 模版 Bot' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看 Agent Coding 模版 Bot 详情' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑 Agent Coding 模版 Bot' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Agent Coding 模版 Bot 健康检查' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看 Agent Coding 模版 Bot 日志' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '管理 Agent Coding 模版 Bot' }));
    expect(screen.getByText('开启服务化')).toBeInTheDocument();
    expect(screen.getByText('重启 Bot')).toBeInTheDocument();
    expect(screen.getByLabelText('重启 Bot说明')).toBeInTheDocument();
    expect(screen.getByText('变更归属空间')).toBeInTheDocument();
    expect(screen.getByText('删除')).toBeInTheDocument();
    expect(screen.queryByText('重启引擎')).not.toBeInTheDocument();
    expect(screen.queryByText('发布与阶段推进')).not.toBeInTheDocument();
    expect(screen.queryByText('授权')).not.toBeInTheDocument();
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
