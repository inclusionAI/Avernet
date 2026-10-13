/** @jest-environment jsdom */
import { ChatEmptyState } from '@/components/Workspace/ChatPanel/ChatEmptyState';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { describe, expect, it, jest } from '@jest/globals';
import type { CommandConfig } from '@tc-chat/ui/es/Sender';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// 真空会话空态：Bot 头像标记 + 主文案（原指令句）+ 摘要副文案 + 开场指令 chips
// （按命令装配物取舍，点击写入输入框）。不借用消息几何，避免被误读为已有消息。
const target: ConversationTarget = {
  id: 'bot-1',
  name: '雄兵',
  avatar: '',
  engine: 'OpenClaw',
  group: 'mine',
  status: 'available',
  summary: '排障值日 Bot',
};

const buildCommand = (ids: string[]): CommandConfig =>
  ({
    categories: [{ key: '__bot_commands__', label: '命令', items: ids.map((id) => ({ id, name: id })) }],
    onSelect: () => {},
    format: () => '',
  }) as unknown as CommandConfig;

describe('ChatEmptyState', () => {
  it('主视觉为 Bot 头像标记，主文案保留原指令句，Bot 摘要作副文案', () => {
    render(<ChatEmptyState target={target} />);

    expect(screen.getByText('发送一条消息开始对话')).toBeInTheDocument();
    expect(screen.getByText('排障值日 Bot')).toBeInTheDocument();
    // 头像标记为 alt 文本（Avat… 初始像 / img alt 皆可定位到 Bot 名）。
    expect(document.querySelector('.rounded-full.ring-8')).toBeInTheDocument();
  });

  it('Bot 无摘要时不渲染空副文案行', () => {
    render(<ChatEmptyState target={{ ...target, summary: '' }} />);
    expect(screen.queryByText('排障值日 Bot')).not.toBeInTheDocument();
  });

  it('开场卡按命令装配物取舍：file/skill 命中渲染并带说明，未命中不渲染', () => {
    const onInsertDraft = jest.fn();
    const { rerender } = render(
      <ChatEmptyState
        target={target}
        command={buildCommand(['__file_entry__', '__skill_entry__'])}
        onInsertDraft={onInsertDraft}
      />,
    );

    const fileCard = screen.getByRole('button', { name: /引用文件/ });
    expect(screen.getByRole('button', { name: /调用技能/ })).toBeInTheDocument();
    // 说明行讲真实能力，不写空话。
    expect(screen.getByText('让 Bot 读取你在本会话上传的文件')).toBeInTheDocument();
    expect(screen.getByText('挑一个已激活的技能直接开工')).toBeInTheDocument();
    fireEvent.click(fileCard);
    expect(onInsertDraft).toHaveBeenCalledWith('/file');

    rerender(
      <ChatEmptyState target={target} command={buildCommand(['__file_entry__'])} onInsertDraft={onInsertDraft} />,
    );
    expect(screen.getByRole('button', { name: /引用文件/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /调用技能/ })).not.toBeInTheDocument();
    // 无插入动作（非交互态）不渲染开场卡与说明行。
    rerender(<ChatEmptyState target={target} command={buildCommand(['__file_entry__'])} />);
    expect(screen.queryByRole('button', { name: /引用文件/ })).not.toBeInTheDocument();
    expect(screen.queryByText('试试这样开场')).not.toBeInTheDocument();
    expect(screen.queryByText('也可以在输入框键入 / 查看全部指令')).not.toBeInTheDocument();
  });
});