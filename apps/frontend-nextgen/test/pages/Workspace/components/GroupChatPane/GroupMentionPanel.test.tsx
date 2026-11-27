/** @jest-environment jsdom */
import { generateBotAvatar } from '@/domain/botAvatar';
import type { ParticipantView } from '@/domain/collaboration';
import { buildGroupMentionConfig } from '@/pages/Workspace/components/GroupChatPane/mentionHelpers';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MentionPanel } from '@tc-chat/ui/es/Sender/components/MentionPanel';
import { useMention } from '@tc-chat/ui/es/Sender/hooks/useMention';
import { useRef, useState } from 'react';

const participants: ParticipantView[] = [
  { actorId: 'human_1', kind: 'human', name: '勿扰成员', avatarUrl: '/absent.png', role: 'member', mode: 'absent' },
  { actorId: 'human_2', kind: 'human', name: '在线成员', role: 'member', mode: 'present' },
  { actorId: 'bot-a', kind: 'bot', name: '助手', role: 'member', mode: 'auto' },
];

function renderMentionPanel(config = buildGroupMentionConfig(participants)) {
  const { categories } = config;
  const activeItems = categories[0].items ?? [];
  const onSelect = jest.fn();
  render(
    <MentionPanel
      categories={categories}
      panel={{
        open: true, query: '', activeCategory: 'members', setActiveCategory: jest.fn(),
        resolvedCategories: [{ key: 'members', label: '会话成员', items: activeItems, loading: false }],
        activeItems, activeIndex: -1, navDepth: 0, currentRawCategories: categories,
      }}
      onClose={jest.fn()}
      onSelect={onSelect}
    />,
  );
  return onSelect;
}

it('SDK @ 面板将勿扰用户置灰并标注，点击不可选；在线用户正常可选', () => {
  const onSelect = renderMentionPanel();
  const absent = screen.getByText('勿扰成员').closest('[aria-disabled="true"]');
  expect(absent).not.toBeNull();
  expect(within(absent as HTMLElement).getByText('勿扰')).toHaveClass('bg-muted', 'text-muted-foreground');
  expect(within(absent as HTMLElement).getByText('勿扰成员')).toHaveClass('text-muted-foreground');
  expect(within(absent as HTMLElement).getByRole('img', { name: '勿扰成员' }).parentElement).toHaveClass('grayscale');
  fireEvent.click(screen.getByText('勿扰成员'));
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('在线成员'));
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'human_2', name: '在线成员' }));
});

it.each([
  ['自由聊天跳过勿扰用户', buildGroupMentionConfig(participants), 'ALL-Bots', 'human_2'],
  ['任务协作跳过从节点', buildGroupMentionConfig([
    { ...participants[2], actorId: 'manager', role: 'manager' },
    { ...participants[2], actorId: 'worker', role: 'worker' },
    participants[1],
  ], { groupKind: 'task_master_slave' }), 'manager', 'human_2'],
] as const)('键盘导航：%s', (_title, config, initial, next) => {
  const onSelect = jest.fn();
  function Harness() {
    const [value, setValue] = useState('');
    const ref = useRef<HTMLInputElement>(null);
    const mention = useMention({ ...config, onSelect }, { value, onChange: setValue, editableRef: ref, isComposing: () => false });
    return (
      <>
        <input
          ref={ref}
          aria-label="输入消息"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            mention.onValueChange(event.target.value, event.target.value.length);
          }}
          onKeyDown={mention.onKeyDown}
        />
        <output>{mention.panel.activeItems[mention.panel.activeIndex]?.id}</output>
      </>
    );
  }
  render(<Harness />);
  const input = screen.getByRole('textbox');
  fireEvent.change(input, { target: { value: '@' } });
  expect(screen.getByText(initial)).toBeInTheDocument();
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  expect(screen.getByText(next)).toBeInTheDocument();
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: next }));
});


it('任务协作标记主从节点、禁用从节点；Bot 头像使用设置图或一致的 ID 默认图', () => {
  const onSelect = renderMentionPanel(buildGroupMentionConfig([
    { actorId: 'default:owner', kind: 'bot', name: '主助手', avatarUrl: '/manager.png', role: 'manager', mode: 'auto' },
    { actorId: 'worker:owner', kind: 'bot', name: '从助手', role: 'worker', mode: 'auto' },
  ], { groupKind: 'task_master_slave' }));

  expect(screen.getByText('主节点')).toBeInTheDocument();
  const worker = screen.getByText('从助手').closest('[aria-disabled="true"]') as HTMLElement;
  expect(worker).not.toBeNull();
  expect(within(worker).getByText('从节点')).toHaveClass('text-muted-foreground');
  expect(within(worker).queryByText('勿扰')).not.toBeInTheDocument();
  expect(within(worker).getByText('从助手')).toHaveClass('text-muted-foreground');
  fireEvent.click(screen.getByText('从助手'));
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('主助手'));
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'default:owner', name: '主助手' }));

  expect(screen.getByRole('img', { name: '主助手' })).toHaveAttribute('src', '/manager.png');
  fireEvent.error(screen.getByRole('img', { name: '主助手' }));
  expect(screen.getByRole('img', { name: '主助手' })).toHaveAttribute('src', generateBotAvatar('default'));
  expect(screen.getByRole('img', { name: '从助手' })).toHaveAttribute('src', generateBotAvatar('worker'));
});
