/** @jest-environment jsdom */

import BotCard from '@/components/BotWorkshop/BotCard';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

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

describe('BotCard health check action', () => {
  test('renders health check when availability is visible', () => {
    const bot = mapBotDto({ bot_id: 'b1', bot_name: 'Openclaw Bot', engine: 'openclaw', status: 'ACTIVE' }).item;
    render(
      <BotCard
        bot={bot}
        onView={noop}
        onHealthCheck={noop}
        healthCheckAvailability={{ action: 'health-check', visible: true, enabled: true }}
      />,
    );

    expect(screen.getByRole('button', { name: '健康检查' })).toBeInTheDocument();
  });

  test('hides health check when availability is invisible', () => {
    const bot = mapBotDto({ bot_id: 'b2', bot_name: 'TEClaw Bot', engine: 'teclaw', status: 'ACTIVE' }).item;
    render(
      <BotCard
        bot={bot}
        onView={noop}
        onHealthCheck={noop}
        healthCheckAvailability={{ action: 'health-check', visible: false, enabled: false }}
      />,
    );

    expect(screen.queryByRole('button', { name: '健康检查' })).not.toBeInTheDocument();
  });
});

describe('BotCard conversation action', () => {
  test('renders the conversation entry and delegates navigation', () => {
    const bot = mapBotDto({
      bot_id: 'b3',
      bot_name: 'Chat Bot',
      engine: 'openclaw',
      status: 'ACTIVE',
      actions: ['chat', 'view'],
    }).item;
    const onConversation = jest.fn();

    render(
      <BotCard
        bot={bot}
        onView={noop}
        onConversation={onConversation}
        inventoryActions={{ chat: { action: 'chat', visible: true, enabled: true } }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '对话' }));

    expect(onConversation).toHaveBeenCalledWith(bot);
  });
});

describe('BotCard backend action contract', () => {
  test('does not render chat or edit when backend only allows view', () => {
    const bot = mapBotDto({
      bot_id: 'offline-service',
      bot_name: 'Offline Bot',
      engine: 'openclaw',
      kind: 'service',
      display_state: 'service_offline',
      actions: ['view'],
    }).item;

    render(
      <BotCard
        bot={bot}
        onView={noop}
        onConversation={noop}
        onEdit={noop}
        inventoryActions={{ view: { action: 'view', visible: true, enabled: true } }}
      />,
    );

    expect(screen.getByRole('button', { name: '查看' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '对话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
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

    render(
      <BotCard
        bot={bot}
        onView={noop}
        onEdit={noop}
        inventoryActions={{
          edit: { action: 'edit', visible: true, enabled: false, disabledReason: 'device offline' },
        }}
      />,
    );

    expect(screen.getByRole('button', { name: '编辑' })).toBeDisabled();
  });
});

describe('BotCard management actions', () => {
  test('closes the management menu before opening delete confirmation', () => {
    const bot = mapBotDto({
      bot_id: 'b4',
      bot_name: 'Delete Bot',
      engine: 'openclaw',
      status: 'ACTIVE',
      actions: ['delete'],
    }).item;

    render(<BotCard bot={bot} onView={noop} onAction={jest.fn()} />);
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

    render(<BotCard bot={bot} onView={noop} onClaimLock={onClaimLock} />);
    fireEvent.click(screen.getByRole('button', { name: '抢占 Locked Service Bot 的编辑锁' }));

    const dialog = within(screen.getByRole('alertdialog'));
    expect(dialog.getByText('该 Bot 正在被编辑')).toBeInTheDocument();
    expect(dialog.getByText(/李四/)).toBeInTheDocument();
    expect(dialog.getByText(/2026-08-26 10:00/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '抢锁并编辑' }));

    expect(onClaimLock).toHaveBeenCalledWith(bot);
  });
});

describe('BotCard runtime labels', () => {
  test('服务 Bot 展示发布版本', () => {
    const bot = mapBotDto({
      bot_id: 'service-version-card',
      card_id: 'service-version-card:3',
      bot_name: '版本 Bot',
      kind: 'service',
      publication_version: 3,
      display_state: 'service_online',
    }).item;

    render(<BotCard bot={bot} onView={noop} />);

    expect(screen.getByText('V3')).toBeInTheDocument();
  });

  test('创建失败展示准确状态', () => {
    const bot = mapBotDto({
      bot_id: 'failed-card',
      bot_name: '失败 Bot',
      display_state: 'failed',
      status: 'FAILED',
      disabled_actions: { restart: 'bot provisioning failed' },
    }).item;

    render(<BotCard bot={bot} onView={noop} />);

    expect(screen.getByText('创建失败')).toBeInTheDocument();
  });

  test('Coding Bot 展示模板名称而不是 claude_code', () => {
    const bot = mapBotDto({
      bot_id: 'architect-card',
      bot_name: '架构 Bot 实例',
      engine: 'claude_code',
      template_type: 'generalCC',
      engine_properties: { template_config: { template_name: '架构 Bot' } },
      status: 'ACTIVE',
    }).item;

    render(<BotCard bot={bot} onView={noop} />);

    expect(screen.getByText('架构 Bot')).toBeInTheDocument();
    expect(screen.queryByText('claude_code')).not.toBeInTheDocument();
  });

  test('历史个人 Coding Bot 缺少模板名称时展示个人 Coding Bot', () => {
    const bot = mapBotDto({
      bot_id: 'personal-coding-card',
      bot_name: '个人 Coding Bot 实例',
      engine: 'claude_code',
      template_type: 'personalCoding',
      status: 'ACTIVE',
    }).item;

    render(<BotCard bot={bot} onView={noop} />);

    expect(screen.getByText('个人 Coding Bot')).toBeInTheDocument();
    expect(screen.queryByText('claude_code')).not.toBeInTheDocument();
  });

  test('普通 Claude Code 仍展示引擎标签', () => {
    const bot = mapBotDto({
      bot_id: 'normal-card',
      bot_name: '普通 CC',
      engine: 'claude_code',
      template_type: 'normalCC',
      status: 'ACTIVE',
    }).item;

    render(<BotCard bot={bot} onView={noop} />);

    expect(screen.getByText('claude_code')).toBeInTheDocument();
  });
});

describe('Agent Coding Bot card actions', () => {
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

    render(
      <BotCard
        bot={bot}
        onView={noop}
        onConversation={onConversation}
        onAction={jest.fn().mockResolvedValue(undefined)}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '去使用' }));
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

    render(
      <BotCard
        bot={bot}
        onView={noop}
        onConversation={noop}
        onAction={jest.fn().mockResolvedValue(undefined)}
        onManagePublication={noop}
        inventoryActions={{ chat: { action: 'chat', visible: true, enabled: true } }}
      />,
    );

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

    render(
      <BotCard
        bot={bot}
        onView={noop}
        onConversation={noop}
        onEdit={noop}
        onHealthCheck={noop}
        healthCheckAvailability={{ action: 'health-check', visible: true, enabled: true }}
        logAction={{ action: 'logs', visible: true, enabled: true }}
        onOpenLogs={noop}
        onChangeSpace={noop}
        onAuthorize={noop}
        collaborationMode="authorize"
        onAction={jest.fn().mockResolvedValue(undefined)}
        inventoryActions={{
          chat: { action: 'chat', visible: true, enabled: true },
          view: { action: 'view', visible: true, enabled: true },
          edit: { action: 'edit', visible: true, enabled: true },
        }}
      />,
    );

    expect(screen.getByRole('button', { name: '去使用' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '健康检查' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '日志' })).not.toBeInTheDocument();

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
