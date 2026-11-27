/** @jest-environment jsdom */
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

it('SDK @ 面板将勿扰用户置灰并标注，点击不可选；在线用户正常可选', () => {
  const { categories } = buildGroupMentionConfig(participants);
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

it('键盘从 ALL 跳过勿扰用户，Enter 仍可选择在线用户', () => {
  const onSelect = jest.fn();
  const config = { ...buildGroupMentionConfig(participants), onSelect };
  function Harness() {
    const [value, setValue] = useState('');
    const ref = useRef<HTMLInputElement>(null);
    const mention = useMention(config, { value, onChange: setValue, editableRef: ref, isComposing: () => false });
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
  expect(screen.getByText('ALL-Bots')).toBeInTheDocument();
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  expect(screen.getByText('human_2')).toBeInTheDocument();
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'human_2' }));
});
