/** @jest-environment jsdom */
import SquareBotCard from '@/components/CollaborationSquare/BotCard';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const longDescription =
  '这是一个用于验证公开 Bot 卡片超长描述展示效果的说明文本，需要固定为两行，并在悬停后展示完整内容，避免卡片被持续撑高或撑宽。';

const bot = {
  id: 'bot-long-description:447147',
  name: '长描述 Bot',
  ownerName: '示例用户',
  description: longDescription,
  capabilities: [],
  relationshipStatus: 'none' as const,
};

describe('SquareBotCard', () => {
  test('描述固定两行并在 hover 后展示完整 Tooltip', async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <SquareBotCard
        bot={bot}
        activeActor={{ type: 'human', id: '327325' }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );

    const description = screen.getByText(longDescription, { selector: 'p' });
    expect(description).toHaveClass('line-clamp-2', 'min-h-10', 'break-words');

    await user.hover(description);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(longDescription);
    unmount();
  });
  test('Bot 工作身份已是好友时只展示关系结果，不提供用户私聊入口', () => {
    render(
      <SquareBotCard
        bot={{ ...bot, relationshipStatus: 'friend' }}
        activeActor={{ type: 'bot', id: 'bot-viewer' }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '已是好友' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '立即开始对话' })).not.toBeInTheDocument();
  });

  test('Bot 工作身份命中自身时禁用好友申请', () => {
    render(
      <SquareBotCard
        bot={bot}
        activeActor={{ type: 'bot', id: bot.id }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '当前 Bot' })).toBeDisabled();
  });
});
