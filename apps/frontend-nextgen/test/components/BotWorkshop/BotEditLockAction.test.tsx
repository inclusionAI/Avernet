/** @jest-environment jsdom */
import { BotEditLockAction } from '@/components/BotWorkshop/BotCard/BotEditLockAction';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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
test('非服务或非草稿、无需锁时没有写入口', () => {
  const { rerender } = render(<BotEditLockAction bot={{ ...bot, serviceMode: 'non-service' }} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  rerender(<BotEditLockAction bot={{ ...bot, lifecycle: 'running' }} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  rerender(<BotEditLockAction bot={{ ...bot, needsEditLock: false }} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
