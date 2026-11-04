/** @jest-environment jsdom */
import SquareBotCard from '@/components/CollaborationSquare/BotCard';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const longDescription =
  '这是一个用于验证公开 Bot 卡片超长描述展示效果的说明文本，需要固定为两行，并在悬停后展示完整内容，避免卡片被持续撑高或撑宽。';

const bot = {
  id: 'bot-long-description:900004',
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
        activeActor={{ type: 'human', id: '900003' }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );

    const description = screen.getByText(longDescription, { selector: 'p' });
    // dmore：12px/18px 两行截断，行高 36px。
    expect(description).toHaveClass('line-clamp-2', 'min-h-9', 'break-words', 'text-xs', 'leading-[18px]');

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

    expect(screen.getByText('好友Bot')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '申请好友权限' })).not.toBeInTheDocument();
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

    expect(screen.getByText('当前 Bot')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '申请好友权限' })).not.toBeInTheDocument();
  });
  test('紧凑卡片保留加好友和分享操作及可访问名称', async () => {
    const user = userEvent.setup();
    const onPrimaryAction = jest.fn();
    const onShare = jest.fn();
    render(
      <SquareBotCard
        bot={bot}
        activeActor={{ type: 'human', id: 'viewer' }}
        busy={false}
        onShare={onShare}
        onPrimaryAction={onPrimaryAction}
      />,
    );
    expect(screen.getByText(bot.ownerName)).toBeInTheDocument();
    expect(screen.getByText(bot.id)).toBeInTheDocument();
    const shareButton = screen.getByRole('button', { name: `分享 ${bot.name}` });
    expect(shareButton).toHaveClass('text-foreground');
    const primaryButton = screen.getByRole('button', { name: '申请好友权限' });
    expect(shareButton.parentElement).toBe(primaryButton.parentElement);
    // dmore Frame 9：操作组贴卡片右上角，而非底部操作条。
    expect(shareButton.parentElement).toHaveClass('ml-auto', 'justify-end');
    expect(shareButton.parentElement?.parentElement?.lastElementChild).toBe(shareButton.parentElement);
    expect(shareButton).not.toHaveClass('absolute', 'md:opacity-0');
    expect(shareButton).toHaveClass('h-8', 'w-8', 'rounded-lg');
    // 主操作默认无背景（不 hover 不出底），hover 才出灰底（ghost 原生 hover:bg-accent）。
    expect(primaryButton).not.toHaveAttribute('style');
    expect(primaryButton).toHaveClass('hover:bg-accent');
    await user.click(primaryButton);
    expect(onPrimaryAction).toHaveBeenCalledWith(bot);
    await user.click(screen.getByRole('button', { name: `分享 ${bot.name}` }));
    expect(onShare).toHaveBeenCalledWith(bot);
  });

  test('好友显示对话图标入口；申请中和忙碌状态禁止重复操作', async () => {
    const user = userEvent.setup();
    const onPrimaryAction = jest.fn();
    const props = {
      activeActor: { type: 'human' as const, id: 'viewer' },
      busy: false,
      onShare: jest.fn(),
      onPrimaryAction,
    };
    const { rerender } = render(<SquareBotCard {...props} bot={{ ...bot, relationshipStatus: 'friend' }} />);
    expect(screen.getByText('好友Bot')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '立即开始对话' }));
    expect(onPrimaryAction).toHaveBeenCalledTimes(1);
    rerender(<SquareBotCard {...props} bot={{ ...bot, relationshipStatus: 'applying' }} />);
    expect(screen.getByText('申请中')).toBeInTheDocument();
    // dmore「申请中」chip：右上角 32px 高、6px 圆角、警示橙软底。
    expect(screen.getByText('申请中')).toHaveClass('bg-warning/10', 'text-warning', 'rounded-md');
    expect(screen.queryByRole('button', { name: '申请好友权限' })).not.toBeInTheDocument();
    rerender(<SquareBotCard {...props} bot={bot} busy />);
    expect(screen.getByRole('button', { name: '申请好友权限' })).toBeDisabled();
  });
});


test.each(['name', 'ownerName', 'id', 'shortProfile'] as const)('%s 悬浮展示完整内容', async (field) => {
  const user = userEvent.setup();
  const text = `${field}非常长的完整内容用于验证省略文字展示1234567890`;
  const cardBot = { ...bot, [field]: text };
  const { unmount } = render(
    <SquareBotCard bot={cardBot} activeActor={{ type: 'human', id: 'viewer' }} busy={false}
      onShare={jest.fn()} onPrimaryAction={jest.fn()} />,
  );
  await user.hover(screen.getByText(text, { exact: field !== 'shortProfile' }));
  expect(await screen.findByRole('tooltip')).toHaveTextContent(text);
  unmount();
});
