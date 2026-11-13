/** @jest-environment jsdom */
import { BotManagementRuntimeItems } from '@/components/BotWorkshop/BotCard/BotManagementRuntimeItems';
import { BotStatusPublicationMenu } from '@/components/BotWorkshop/BotCard/BotStatusPublicationMenu';
import type { ServicePublication } from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { serviceBotEvalService } from '@/services/botWorkshop/serviceBotEvalService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: {
    listLifecycle: jest.fn(),
    advanceLifecycle: jest.fn(),
    offlineLifecycle: jest.fn(),
    upgradeLifecycle: jest.fn(),
    cancelStaging: jest.fn(),
    retryLifecycle: jest.fn(),
  },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock('@/services/botWorkshop/serviceBotEvalService', () => ({
  serviceBotEvalService: { precheck: jest.fn() },
}));
const bot = mapBotDto({
  bot_id: 'bot-1',
  card_id: 'bot-1:17',
  publication_version: 2,
  bot_name: '服务 Bot',
  bot_type: 'service',
  kind: 'service',
  engine: 'openclaw',
  display_state: 'service_online',
}).item;
const item: ServicePublication = {
  publicationId: 17,
  cardId: 'bot-1:17',
  version: 2,
  status: 'running',
  internalStatus: 'success',
  availableActions: ['upgrade', 'offline', 'restart_publish', 'delete'],
  createdAt: '',
  updatedAt: '',
};
const load = botEditorService.listLifecycle as jest.Mock;
const precheck = serviceBotEvalService.precheck as jest.Mock;
beforeEach(() => {
  jest.clearAllMocks();
  load.mockResolvedValue([item]);
  precheck.mockResolvedValue({ gate_status: 'SKIPPED', gate_result: 'PASS', allowed: true });
});
function setup(currentBot = bot) {
  const refresh = jest.fn().mockResolvedValue(undefined);
  const rowClick = jest.fn();
  render(
    <div onClick={rowClick}>
      <BotStatusPublicationMenu bot={currentBot} onPublicationChanged={refresh}>
        运行中
      </BotStatusPublicationMenu>
    </div>,
  );
  return { refresh, rowClick };
}
async function openMenu() {
  fireEvent.click(screen.getByRole('button', { name: '服务 Bot 发布状态' }));
  await screen.findByRole('button', { name: '下线' });
}

test('按需读取状态，直接选择操作并确认升级，刷新列表且不触发行导航', async () => {
  const { refresh, rowClick } = setup();
  expect(load).not.toHaveBeenCalled();
  await openMenu();
  expect(screen.queryByText('发布与阶段推进')).not.toBeInTheDocument();
  expect(screen.queryByText('V2 · 线上')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '重启发布' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '删除草稿' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '创建升级草稿' }));
  expect(botEditorService.upgradeLifecycle).not.toHaveBeenCalled();
  const dialog = await screen.findByRole('alertdialog');
  expect(within(dialog).getByText(/V2（线上）/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: '创建升级草稿' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(botEditorService.upgradeLifecycle).toHaveBeenCalledWith(bot.id, 17, bot.ownerId);
  expect(load).toHaveBeenCalledTimes(2);
  expect(rowClick).not.toHaveBeenCalled();
});

test('加载失败允许重试，空操作显示原因', async () => {
  load.mockRejectedValueOnce(new Error('network'));
  setup();
  fireEvent.click(screen.getByRole('button', { name: '服务 Bot 发布状态' }));
  fireEvent.click(await screen.findByRole('button', { name: '重试' }));
  await screen.findByRole('button', { name: '下线' });
  load.mockResolvedValue([]);
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: '服务 Bot 发布状态' }));
  expect(await screen.findByText('当前暂无可执行操作')).toBeInTheDocument();
});

test('确认前状态变化则阻止提交', async () => {
  setup();
  await openMenu();
  fireEvent.click(screen.getByRole('button', { name: '下线' }));
  load.mockResolvedValue([{ ...item, availableActions: [] }]);
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '下线' }));
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith('发布状态已更新，请重新打开状态菜单后操作。'));
  expect(botEditorService.offlineLifecycle).not.toHaveBeenCalled();
});

test('提交中禁止重复操作，下线请求完成后刷新', async () => {
  let finish!: () => void;
  (botEditorService.offlineLifecycle as jest.Mock).mockImplementationOnce(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const { refresh } = setup();
  await openMenu();
  fireEvent.click(screen.getByRole('button', { name: '下线' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '下线' }));
  await waitFor(() => expect(botEditorService.offlineLifecycle).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('button', { name: '处理中…' })).toBeDisabled();
  await act(async () => finish());
  expect(refresh).toHaveBeenCalledTimes(1);
});

test('锁定时不可执行发布操作', async () => {
  render(
    <BotStatusPublicationMenu
      bot={{ ...bot, lock: { ...bot.lock, status: 'other' } } as typeof bot}
      onPublicationChanged={jest.fn()}
    >
      运行中
    </BotStatusPublicationMenu>,
  );
  fireEvent.click(screen.getByRole('button', { name: '服务 Bot 发布状态' }));
  expect(screen.getByText('该 Bot 正被他人编辑，暂时无法执行发布操作。')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '下线' })).not.toBeInTheDocument();
});

test('历史评测记录入口对无编辑权限用户置灰并展示说明', async () => {
  const openHistory = jest.fn();
  const botWithoutPermission = { ...bot, canOpenEvalHistory: false, actions: ['view'] };

  render(
    <BotManagementRuntimeItems
      bot={botWithoutPermission}
      lockedByOther={false}
      onClose={jest.fn()}
      onConfirm={jest.fn()}
      onOpenEvalHistory={openHistory}
    />,
  );

  const entry = screen.getByRole('button', { name: /历史评测记录/ });
  expect(entry).toBeDisabled();
  fireEvent.mouseMove(entry);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

  const help = screen.getByLabelText('历史评测记录说明');
  fireEvent.mouseMove(help);
  fireEvent.pointerMove(help, { pointerType: 'mouse' });
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    '仅具有当前服务 Bot 编辑权限的人可查看历史评测记录',
  );
  fireEvent.click(entry);
  expect(openHistory).not.toHaveBeenCalled();

  const botWithPermission = {
    ...bot,
    canOpenEvalHistory: true,
    actions: ['view', 'edit', 'delete'],
  };
  render(
    <BotManagementRuntimeItems
      bot={botWithPermission}
      lockedByOther={false}
      onClose={jest.fn()}
      onConfirm={jest.fn()}
      onOpenEvalHistory={openHistory}
    />,
  );

  const historyEntries = screen.getAllByRole('button', { name: /历史评测记录/ });
  expect(historyEntries).toHaveLength(2);
  const enabledEntry = historyEntries.find((item) => !item.hasAttribute('disabled'));
  expect(enabledEntry).toBeDefined();
  fireEvent.click(enabledEntry!);
  expect(openHistory).toHaveBeenCalledTimes(1);
});

test('禁用删除支持整行悬停与键盘聚焦提示，不能触发删除', async () => {
  const confirm = jest.fn();
  render(
    <BotManagementRuntimeItems
      bot={{ ...bot, actions: [], disabledActions: { delete: '你暂无权限删除该 Bot，请联系 Bot 所有者。' } }}
      lockedByOther={false}
      onClose={jest.fn()}
      onConfirm={confirm}
    />,
  );
  expect(screen.getByRole('button', { name: '删除' })).toBeDisabled();
  const target = screen.getByLabelText('删除说明');
  fireEvent.mouseMove(target);
  fireEvent.pointerMove(target, { pointerType: 'mouse' });
  expect(await screen.findByRole('tooltip')).toHaveTextContent('你暂无权限删除该 Bot，请联系 Bot 所有者。');
  fireEvent.click(screen.getByRole('button', { name: '删除' }));
  expect(confirm).not.toHaveBeenCalled();
  expect(target).toHaveAttribute('tabindex', '0');
});

test('V2 行仅显示自身操作，不混入 V3 草稿操作', async () => {
  load.mockResolvedValue([
    {
      ...item,
      publicationId: 18,
      cardId: 'bot-1:18',
      version: 3,
      status: 'draft',
      availableActions: ['publish_staging'],
    },
    item,
  ]);
  const { refresh } = setup();
  await openMenu();
  expect(screen.queryByText('V3 · 草稿')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '发布到预发' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '下线' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '下线' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(botEditorService.offlineLifecycle).toHaveBeenCalledWith(bot.id, bot.ownerId);
});

test('悬停展开并允许跨越浮层间隙，移出后关闭', async () => {
  setup();
  const trigger = screen.getByRole('button', { name: '服务 Bot 发布状态' });
  fireEvent.mouseEnter(trigger);
  await screen.findByRole('button', { name: '下线' });
  jest.useFakeTimers();
  try {
    fireEvent.mouseLeave(trigger);
    act(() => jest.advanceTimersByTime(75));
    fireEvent.mouseEnter(screen.getByRole('dialog'));
    act(() => jest.advanceTimersByTime(200));
    expect(screen.getByRole('button', { name: '下线' })).toBeInTheDocument();
    fireEvent.mouseLeave(screen.getByRole('dialog'));
    act(() => jest.advanceTimersByTime(200));
    expect(screen.queryByRole('button', { name: '下线' })).not.toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});

test('V3 草稿行仅展示 V3 的预发操作', async () => {
  load.mockResolvedValue([
    item,
    {
      ...item,
      publicationId: 18,
      cardId: 'bot-1:18',
      version: 3,
      status: 'draft',
      availableActions: ['publish_staging'],
    },
  ]);
  const { refresh } = setup({ ...bot, cardId: 'bot-1:18', publicationVersion: 3, lifecycle: 'draft' });
  fireEvent.click(screen.getByRole('button', { name: '服务 Bot 发布状态' }));
  await screen.findByRole('button', { name: '发布到预发' });
  expect(screen.queryByText('V2 · 线上')).not.toBeInTheDocument();
  expect(screen.queryByText('V3 · 草稿')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '下线' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '发布到预发' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '发布到预发' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(precheck).toHaveBeenCalledWith(bot.id, '3', bot.ownerId);
  expect(botEditorService.advanceLifecycle).toHaveBeenCalledWith(bot.id, 'prestable', bot.ownerId);
});

test('评测门禁未通过时拦截预发推进并引导评测', async () => {
  const blocked = jest.fn();
  const refresh = jest.fn().mockResolvedValue(undefined);
  const draftItem = {
    ...item,
    publicationId: 18,
    cardId: 'bot-1:18',
    version: 3,
    status: 'draft',
    availableActions: ['publish_staging'],
  } as ServicePublication;
  load.mockResolvedValue([draftItem]);
  precheck.mockResolvedValue({ gate_status: 'BLOCKED', gate_result: 'FAIL', allowed: false });
  render(
    <BotStatusPublicationMenu
      bot={{ ...bot, cardId: 'bot-1:18', publicationVersion: 3, lifecycle: 'draft' }}
      onPublicationChanged={refresh}
      onPublicationGateBlocked={blocked}
    >
      草稿
    </BotStatusPublicationMenu>,
  );
  fireEvent.click(screen.getByRole('button', { name: '服务 Bot 发布状态' }));
  fireEvent.click(await screen.findByRole('button', { name: '发布到预发' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '发布到预发' }));
  await waitFor(() => expect(precheck).toHaveBeenCalledWith(bot.id, '3', bot.ownerId));
  expect(blocked).toHaveBeenCalledWith(expect.objectContaining({ id: bot.id, publicationVersion: 3 }));
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  expect(botEditorService.advanceLifecycle).not.toHaveBeenCalled();
  expect(refresh).not.toHaveBeenCalled();
});

test('当前版本没有操作时，不展示其他版本的操作', async () => {
  load.mockResolvedValue([
    { ...item, availableActions: [] },
    {
      ...item,
      publicationId: 18,
      cardId: 'bot-1:18',
      version: 3,
      status: 'draft',
      availableActions: ['publish_staging'],
    },
  ]);
  setup();
  fireEvent.click(screen.getByRole('button', { name: '服务 Bot 发布状态' }));
  expect(await screen.findByText('当前暂无可执行操作')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '发布到预发' })).not.toBeInTheDocument();
});
