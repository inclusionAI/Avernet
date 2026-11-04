/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import GroupCard from '@/components/CollaborationSquare/GroupCard';
import { ShareGroupDialog } from '@/components/CollaborationSquare/ShareGroupDialog';
import type { PublicGroup } from '@/domain/collaborationSquare/types';

jest.mock('@/utils/tracert', () => ({ Tracert: { click: jest.fn(), expo: jest.fn() } }));

const group: PublicGroup = {
  id: 'g1',
  name: '产品共创群',
  ownerBotName: '群主助手',
  ownerUserName: '示例用户',
  typeLabel: '任务协作',
  memberCount: 2,
  goal: '推进产品共创',
  memberListVisibility: 'visible',
  canCreateSession: true,
};

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <GroupCard
        group={group}
        busy={false}
        onOpenMembers={jest.fn()}
        onShare={() => setOpen(true)}
        onCreateSession={jest.fn()}
      />
      <ShareGroupDialog open={open} group={open ? group : null} onClose={() => setOpen(false)} />
    </>
  );
}

const wait = (ms: number) =>
  act(
    () =>
      new Promise((resolve) => {
        setTimeout(resolve, ms);
      }),
  );

test('打开/关闭分享弹窗均不触发任何卡片 tooltip 凭空展开', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole('button', { name: `分享 ${group.name}` }));
  await screen.findByText('分享协作群');
  // 等过 TooltipProvider delayDuration 与跳过分流的合并窗口，不应有任何 tooltip 挂出
  //（修复前：预览条群名 p 作为弹窗内首个可聚焦元素被 Radix 初始聚焦，其 tooltip instant-open）。
  await wait(600);
  expect(document.querySelectorAll('[role="tooltip"]')).toHaveLength(0);

  await user.keyboard('{Escape}');
  await wait(600);
  // 关闭时不归还焦点给分享图标钮，同样不应有 tooltip 挂出。
  expect(document.querySelectorAll('[role="tooltip"]')).toHaveLength(0);
});
