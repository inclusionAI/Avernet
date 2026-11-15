/** @jest-environment jsdom */
import BotInfoCell from '@/components/BotWorkshop/BotCard/BotInfoCell';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

test.each(['mine', 'other'] as const)('%s lock stays below the name and does not navigate the row', async (status) => {
  const user = userEvent.setup();
  const bot = {
    ...mapBotDto({
      bot_id: 'team-service-bot',
      bot_name: '团队空间里需要完整查看名称的服务机器人',
      bot_type: 'service',
      engine: 'openclaw',
      display_state: 'service_draft',
    }).item,
    spaceKind: 'team' as const,
    lock: { status, holderName: '协作者' },
  };
  const onRowClick = jest.fn();
  render(
    <div onClick={onRowClick}>
      <BotInfoCell bot={bot} onClaimLock={jest.fn()} onReleaseLock={jest.fn()} />
    </div>,
  );
  const name = screen.getByText(bot.name);
  expect(name).not.toHaveAttribute('title');
  await user.hover(name);
  expect(await screen.findByRole('tooltip')).toHaveTextContent(bot.name);
  await user.unhover(name);
  expect(within(name.parentElement!).queryByRole('button')).not.toBeInTheDocument();
  const entityKey = screen.getByText(bot.entityKey);
  expect(entityKey).not.toHaveAttribute('title');
  const metadataRow = entityKey.parentElement!;
  const lockButton = within(metadataRow).getByRole('button', {
    name: status === 'mine' ? `释放锁：${bot.name}` : `抢占 ${bot.name} 的编辑锁`,
  });
  expect(metadataRow.parentElement).toBe(name.parentElement!.parentElement);
  expect(metadataRow.parentElement).toHaveClass('min-w-0', 'flex-1', 'flex-col');
  fireEvent.click(lockButton);
  expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  expect(onRowClick).not.toHaveBeenCalled();
});
