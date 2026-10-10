/** @jest-environment jsdom */
import GroupCard from '@/components/CollaborationSquare/GroupCard';
import { GroupMembersModal } from '@/components/CollaborationSquare/GroupMembersModal';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const group: PublicGroup = {
  id: 'g1',
  name: '公开群',
  ownerBotName: '服务助手',
  driverBotUuid: 'bot-1:owner',
  ownerUserName: '不能使用列表旧 Owner',
  typeLabel: '自由聊天',
  memberCount: 0,
  goal: '协作目标',
  memberListVisibility: 'visible',
  canCreateSession: true,
};
const actions = { busy: false, onOpenMembers: jest.fn(), onShare: jest.fn(), onCreateSession: jest.fn() };

test('群主行展示名称 (UUID)，无 title 属性，悬停可查看完整文本', async () => {
  render(<GroupCard group={group} {...actions} />);
  const label = screen.getByText('服务助手 (bot-1:owner)');
  expect(label).toHaveClass('truncate');
  expect(label).not.toHaveAttribute('title');
  await userEvent.hover(label);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('服务助手 (bot-1:owner)');
});

test.each([
  ['bot-1:owner', 'bot-1:owner', 'bot-1:owner'],
  ['服务助手', undefined, '服务助手'],
  ['未公开', undefined, '未公开'],
])('群主名称 %s / UUID %s 展示为 %s', (ownerBotName, driverBotUuid, expected) => {
  render(<GroupCard group={{ ...group, ownerBotName, driverBotUuid }} {...actions} />);
  expect(screen.getByText(expected)).toBeInTheDocument();
  expect(screen.queryByText('bot-1:owner (bot-1:owner)')).not.toBeInTheDocument();
});

test.each(['群主用户', 'human_owner', '未公开'])('弹窗即使没有成员仍显示详情 Owner：%s', (ownerUserName) => {
  render(
    <GroupMembersModal
      open
      group={group}
      members={[]}
      ownerUserName={ownerUserName}
      loading={false}
      onClose={jest.fn()}
    />,
  );
  expect(screen.getByText('Owner 用户：')).toBeInTheDocument();
  expect(screen.getByText(ownerUserName)).toBeInTheDocument();
  expect(screen.getByText('暂无成员信息。')).toBeInTheDocument();
  expect(screen.queryByText(group.ownerUserName)).not.toBeInTheDocument();
});

test('加载中不显示前一个群的 Owner', () => {
  render(<GroupMembersModal open group={group} members={[]} ownerUserName="旧 Owner" loading onClose={jest.fn()} />);
  expect(screen.getByLabelText('正在加载成员')).toBeInTheDocument();
  expect(screen.queryByText('旧 Owner')).not.toBeInTheDocument();
});

test('Owner 行与上方说明使用相同字号', () => {
  render(
    <GroupMembersModal open group={group} members={[]} ownerUserName="群主用户" loading={false} onClose={jest.fn()} />,
  );
  const description = screen.getByText('成员详情仅展示名称、身份类型和角色。');
  const ownerRow = screen.getByText('Owner 用户：').parentElement;
  expect(description).toHaveClass('text-xs');
  expect(ownerRow).toHaveClass('text-xs');
  expect(ownerRow).not.toHaveClass('text-sm');
});

test.each([2, 120])('成员入口 %s 位位于标题栏群类型左侧，点击与键盘仍打开当前群', async (memberCount) => {
  const onOpenMembers = jest.fn();
  const currentGroup = { ...group, memberCount };
  render(<GroupCard group={currentGroup} {...actions} onOpenMembers={onOpenMembers} />);
  const button = screen.getByRole('button', { name: `${memberCount} 位成员` });
  const header = screen.getByRole('heading', { name: group.name }).parentElement?.parentElement;
  const type = screen.getByText(group.typeLabel);
  expect(header).toContainElement(button);
  expect(button.parentElement).toContainElement(type);
  expect(button.compareDocumentPosition(type) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByRole('button', { name: `${memberCount} 位成员` })).toHaveLength(1);
  await userEvent.click(button);
  expect(onOpenMembers).toHaveBeenLastCalledWith(currentGroup);
  button.focus();
  await userEvent.keyboard('{Enter}');
  expect(onOpenMembers).toHaveBeenCalledTimes(2);
});

test('成员入口复用群类型的 Badge 字号、颜色、圆角和间距，图标缩小到 12px', () => {
  render(<GroupCard group={{ ...group, memberCount: 2 }} {...actions} />);
  const members = screen.getByText('2 位成员');
  const type = screen.getByText(group.typeLabel);
  for (const token of [
    'text-[11px]',
    'font-medium',
    'bg-muted',
    'text-muted-foreground',
    'rounded-full',
    'px-2',
    'py-0.5',
  ]) {
    expect(type).toHaveClass(token);
    expect(members).toHaveClass(token);
  }
  expect(members.querySelector('svg')).toHaveClass('size-3');
  const button = screen.getByRole('button', { name: '2 位成员' });
  expect(button).toHaveClass('h-auto', 'border-0', 'p-0', 'leading-normal');
});

test('Owner 与说明位于同一无额外间距区块，按正常行距连续展示', () => {
  render(
    <GroupMembersModal open group={group} members={[]} ownerUserName="群主用户" loading={false} onClose={jest.fn()} />,
  );
  const description = screen.getByText('成员详情仅展示名称、身份类型和角色。');
  const ownerRow = screen.getByText('Owner 用户：').parentElement;
  expect(description.nextElementSibling).toBe(ownerRow);
  expect(description.parentElement).not.toHaveClass('space-y-1.5', 'gap-4');
  expect(ownerRow).toHaveClass('m-0', 'text-xs');
  expect(ownerRow).not.toHaveClass('leading-6');
});

test('Owner 标签和用户名统一继承与说明相同的文字颜色', () => {
  render(
    <GroupMembersModal open group={group} members={[]} ownerUserName="群主用户" loading={false} onClose={jest.fn()} />,
  );
  const label = screen.getByText('Owner 用户：');
  const name = screen.getByText('群主用户');
  expect(label.parentElement).toBe(name.parentElement);
  expect(name.parentElement).toHaveClass('text-muted-foreground');
  expect(name.parentElement).not.toHaveClass('text-foreground');
  expect(label).not.toHaveAttribute('class');
  expect(name).not.toHaveAttribute('class');
});
