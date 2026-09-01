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

describe('SquareBotCard description layout', () => {
  test('描述固定两行并在 hover 后展示完整 Tooltip', async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <SquareBotCard bot={bot} busy={false} onShare={jest.fn()} onPrimaryAction={jest.fn()} />,
    );

    const description = screen.getByText(longDescription, { selector: 'p' });
    expect(description).toHaveClass('line-clamp-2', 'min-h-10', 'break-words');

    await user.hover(description);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(longDescription);
    unmount();
  });
});
