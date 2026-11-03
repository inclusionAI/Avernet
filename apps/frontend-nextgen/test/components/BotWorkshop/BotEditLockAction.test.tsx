/** @jest-environment jsdom */
import { BotEditLockAction } from '@/components/BotWorkshop/BotCard/BotEditLockAction';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const bot = mapBotDto({
  bot_id: 'b1',
  bot_name: '协作机器人',
  engine: 'openclaw',
  bot_type: 'service',
  display_state: 'service_draft',
  edit_lock: { locked: false, need_lock: true },
}).item;
test('未持锁时展示获取锁入口，确认后获取', async () => {
  const claim = jest.fn().mockResolvedValue(undefined);
  render(<BotEditLockAction bot={bot} onClaimLock={claim} />);
  fireEvent.click(screen.getByText('获取锁'));
  expect(claim).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '获取锁并编辑' }));
  await waitFor(() => expect(claim).toHaveBeenCalledWith(bot));
});
test('自己的锁可以释放，点击不触发行跳转', async () => {
  const release = jest.fn().mockResolvedValue(undefined);
  const rowClick = jest.fn();
  const ownBot = { ...bot, lock: { status: 'mine' as const } };
  render(
    <div onClick={rowClick}>
      <BotEditLockAction bot={ownBot} onReleaseLock={release} />
    </div>,
  );
  fireEvent.click(screen.getByText('释放锁'));
  expect(rowClick).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认释放' }));
  await waitFor(() => expect(release).toHaveBeenCalledWith(ownBot));
  expect(rowClick).not.toHaveBeenCalled();
});
test('非服务、部署中、无需锁时没有写入口', () => {
  const { rerender } = render(<BotEditLockAction bot={{ ...bot, serviceMode: 'non-service' }} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  rerender(<BotEditLockAction bot={{ ...bot, lifecycle: 'deploying' }} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  rerender(<BotEditLockAction bot={{ ...bot, needsEditLock: false }} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test.each(['running', 'prestable', 'failed'] as const)('%s 态持锁人可释放锁', async (lifecycle) => {
  const release = jest.fn().mockResolvedValue(undefined);
  const lockedBot = { ...bot, lifecycle, lock: { status: 'mine' as const } };
  render(<BotEditLockAction bot={lockedBot} onReleaseLock={release} />);
  fireEvent.click(screen.getByText('释放锁'));
  fireEvent.click(screen.getByRole('button', { name: '确认释放' }));
  await waitFor(() => expect(release).toHaveBeenCalledWith(lockedBot));
});

test.each(['running', 'prestable', 'failed'] as const)('%s 态抢锁不承诺进入编辑', async (lifecycle) => {
  const claim = jest.fn().mockResolvedValue(undefined);
  const lockedBot = { ...bot, lifecycle, lock: { status: 'other' as const, holderName: '协作者' } };
  render(<BotEditLockAction bot={lockedBot} onClaimLock={claim} />);
  fireEvent.click(screen.getByRole('button', { name: `抢占 ${bot.name} 的编辑锁` }));
  expect(screen.queryByText('抢锁并编辑')).not.toBeInTheDocument();
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '抢锁' }));
  await waitFor(() => expect(claim).toHaveBeenCalledWith(lockedBot));
});

test('运行态无人持锁时可以获取锁，且不承诺进入编辑', async () => {
  const claim = jest.fn().mockResolvedValue(undefined);
  const runningBot = mapBotDto({
    bot_id: 'b1', bot_name: bot.name, bot_type: 'service', engine: 'openclaw',
    status: 'ACTIVE', publish: { status: 'success' },
    edit_lock: { locked: false, need_lock: false, has_collaborators: true },
  }).item;
  expect(runningBot.lifecycle).toBe('running');
  expect(runningBot.needsEditLock).toBe(false);
  expect(runningBot.hasCollaborators).toBe(true);
  render(<BotEditLockAction bot={runningBot} onClaimLock={claim} />);
  fireEvent.click(screen.getByText('获取锁'));
  expect(screen.getByText(`确认获取「${bot.name}」的编辑锁？`)).toBeInTheDocument();
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '获取锁' }));
  await waitFor(() => expect(claim).toHaveBeenCalledWith(runningBot));
});

test.each(['released', 'upgraded'])('%s 历史版本不展示锁操作', (rawPublishStatus) => {
  render(<BotEditLockAction bot={{ ...bot, rawPublishStatus, lock: { status: 'mine' } }} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
