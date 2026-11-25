/** @jest-environment jsdom */
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { TextDecoder, TextEncoder } from 'node:util';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SquareBotCard, { SquareBotCardSkeleton } from '../src/components/CollaborationSquare/BotCard';
import { BotProfileModal } from '../src/components/CollaborationSquare/BotProfileModal';
import GroupCard, { GroupCardSkeleton } from '../src/components/CollaborationSquare/GroupCard';
import { GroupMembersModal } from '../src/components/CollaborationSquare/GroupMembersModal';
import { ShareGroupDialog } from '../src/components/CollaborationSquare/ShareGroupDialog';
import SquareSearchBar from '../src/components/CollaborationSquare/SquareSearchBar';

Object.assign(globalThis, { TextDecoder, TextEncoder });
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const { renderToStaticMarkup } = require('react-dom/server') as typeof import('react-dom/server');

const bot = {
  id: 'b1',
  name: '产品协作助手',
  ownerName: '示例用户',
  description: '帮助完成产品协作',
  capabilities: ['需求分析'],
  relationshipStatus: 'none' as const,
};
const botWithUuid = { ...bot, id: '20260825_mbu0ey8f:900004' };
const group = {
  id: 'g1',
  name: '产品共创群',
  ownerBotName: '群主助手',
  ownerUserName: '示例用户',
  typeLabel: '自由聊天',
  memberCount: 2,
  goal: '推进产品共创',
  memberListVisibility: 'visible' as const,
  canCreateSession: true,
};

function renderWithPortals(element: React.ReactElement) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(element));
  const html = document.body.innerHTML;
  act(() => root.unmount());
  container.remove();
  return html;
}

describe('collaboration square accessible UI', () => {
  test('Bot 卡片使用单一关系主操作、隐藏画像入口并保留分享操作', () => {
    const html = renderToStaticMarkup(
      <SquareBotCard
        bot={bot}
        activeActor={{ type: 'human', id: '900003' }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );
    const friendHtml = renderToStaticMarkup(
      <SquareBotCard
        bot={{ ...bot, relationshipStatus: 'friend' }}
        activeActor={{ type: 'human', id: '900003' }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );
    expect(html).toContain('申请好友权限');
    expect(html).not.toContain('查看画像');
    expect(html).toContain(bot.ownerName);
    expect(html).toContain(bot.id);
    expect(html).toContain('Owner 用户');
    expect(html).toContain('Bot UUID');
    expect(html).not.toContain('Owner用户 ·');
    expect(html).not.toContain('Bot UUID ·');
    expect(html).toContain('aria-label="分享 产品协作助手"');
    expect(html).toContain('分享');
    expect(html).not.toContain('需求分析');
    expect(html).not.toContain('bg-gray-');
    expect(friendHtml).toContain('好友Bot');
    expect(friendHtml).toContain('立即开始对话');
    expect(friendHtml).not.toContain('申请好友权限');

    const uuidHtml = renderToStaticMarkup(
      <SquareBotCard
        bot={botWithUuid}
        activeActor={{ type: 'human', id: '900003' }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );
    expect(uuidHtml).toContain('Bot UUID');
    expect(uuidHtml).not.toContain('Bot ID');

    const profileHtml = renderToStaticMarkup(
      <SquareBotCard
        bot={{ ...bot, shortProfile: '用于测试的专用 Bot' }}
        activeActor={{ type: 'human', id: '900003' }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );
    expect(profileHtml).toContain('Profile');
    expect(profileHtml).toContain('用于测试的专用 Bot');
  });

  test('自有公开 Bot 显示直接会话操作且不显示好友申请', () => {
    const html = renderToStaticMarkup(
      <SquareBotCard
        bot={{ ...bot, isOwnedByLoggedInUser: true, relationshipStatus: 'none' }}
        activeActor={{ type: 'human', id: '900003' }}
        busy={false}
        onShare={jest.fn()}
        onPrimaryAction={jest.fn()}
      />,
    );
    expect(html).toContain('我的 Bot');
    expect(html).toContain('立即开始对话');
    expect(html).not.toContain('申请好友权限');
  });

  test('群卡片展示公开字段且不展示敏感管理字段', () => {
    const html = renderToStaticMarkup(
      <GroupCard
        group={group}
        busy={false}
        onOpenMembers={jest.fn()}
        onShare={jest.fn()}
        onCreateSession={jest.fn()}
      />,
    );
    expect(html).toContain('群主 Bot');
    expect(html).not.toContain('Owner用户');
    expect(html).toContain('创建新会话');
    expect(html).not.toContain('在线状态');
    expect(html).not.toContain('实例环境');
    expect(html).not.toContain('管理权限');
  });

  test('分享协作群弹窗展示群预览与分享链接，复制按钮初始为复制链接', () => {
    const html = renderWithPortals(
      <ShareGroupDialog open group={{ ...group, id: 'g1', typeLabel: '任务协作' }} onClose={jest.fn()} />,
    );
    expect(html).toContain('分享协作群');
    expect(html).toContain('复制链接，邀请其他用户查看这个公开协作群。');
    expect(html).toContain('产品共创群');
    expect(html).toContain('任务协作');
    expect(html).toContain('分享链接');
    expect(html).toContain('/collaboration-square/groups?');
    expect(html).toContain('复制链接');
  });

  test('加载骨架逐区镜像真实卡片结构（dmore 分区，loading→loaded 零跳版）', () => {
    const groupHtml = renderToStaticMarkup(<GroupCardSkeleton />);
    // 头部名称行 + 右上 65×20 类型胶囊位；底部 border-t 行内左「成员」chip 位 + 右两个 32px 图标钮位。
    expect(groupHtml).toContain('w-[65px]');
    expect(groupHtml).toContain('border-t');
    expect((groupHtml.match(/h-8 w-8 rounded-lg/g) ?? [])).toHaveLength(2);

    const botHtml = renderToStaticMarkup(<SquareBotCardSkeleton />);
    // 40px 圆角头像位 + 与真实卡一致的描边静止态（无静态阴影，hover 才出）。
    expect(botHtml).toContain('h-10 w-10');
    expect(botHtml).toContain('border-divider');
    expect(botHtml).not.toContain('rgba(0,10,26,0.07)');
  });

  test('详情和成员弹层只渲染公开信息', () => {
    const botHtml = renderWithPortals(
      <BotProfileModal
        open
        profile={{ ...bot, engine: 'OpenClaw', capabilities: [{ id: 'c1', name: '需求分析' }], skills: [] }}
        loading={false}
        onClose={jest.fn()}
        onCopyId={jest.fn()}
      />,
    );
    expect(botHtml).toContain('展示该 Bot 已公开的画像与能力。');
    expect(botHtml).toContain('Bot UUID');
    expect(botHtml).not.toContain('Bot ID');
    expect(botHtml).not.toContain('完整 Bot ID');
    expect(botHtml).not.toContain('已公开能力');
    expect(botHtml).not.toContain('需求分析');

    const uuidProfileHtml = renderWithPortals(
      <BotProfileModal
        open
        profile={{ ...botWithUuid, engine: 'OpenClaw', capabilities: [], skills: [] }}
        loading={false}
        onClose={jest.fn()}
        onCopyId={jest.fn()}
      />,
    );
    expect(uuidProfileHtml).toContain('Bot UUID');
    const memberHtml = renderWithPortals(
      <GroupMembersModal
        open
        group={group}
        members={[{ id: 'u1', displayName: '示例用户', type: 'human', role: '参与者' }]}
        loading={false}
        onClose={jest.fn()}
      />,
    );
    expect(memberHtml).toContain('用户');
    expect(memberHtml).not.toContain('Human');
    // 角色标签按群类型归一化：自由聊天群非 driver 的未知 role（如「参与者」）统一展示「成员」。
    expect(memberHtml).toContain('成员');
    expect(memberHtml).not.toContain('参与者');
  });

  test('公开 Bot 页面保留按名称与智能搜索入口', () => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: jest.fn() });
    render(
      <SquareSearchBar resource="bot" query="" mode="name" onQueryChange={jest.fn()} onModeChange={jest.fn()} />,
    );
    const modeSelect = screen.getByRole('combobox', { name: 'Bot 搜索模式' });
    expect(modeSelect).toHaveTextContent('按名称');
    expect(screen.getByRole('textbox', { name: '输入Bot名称' })).toBeInTheDocument();
    fireEvent.keyDown(modeSelect, { key: 'ArrowDown' });
    expect(screen.getByRole('option', { name: '按名称' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '智能' })).toBeInTheDocument();
  });

  test('页面实现遵守项目 UI 与分层约束', () => {
    const files = [
      'src/pages/CollaborationSquare/Bots/index.tsx',
      'src/pages/CollaborationSquare/Groups/index.tsx',
      'src/pages/CollaborationSquare/Tasks/index.tsx',
      'config/routes.ts',
      'src/components/CollaborationSquare/CreateGroupSessionModal/index.tsx',
      'src/components/CollaborationSquare/SquarePageShell/index.tsx',
      'src/components/CollaborationSquare/PublicBotCatalogPanel/index.tsx',
      'src/components/CollaborationSquare/PublicGroupSquareSection/index.tsx',
      'src/components/CollaborationSquare/ShareGroupDialog/index.tsx',
      'src/hooks/useCollaborationSquare.ts',
      'src/hooks/useCollaborationSquareList.ts',
      'src/hooks/useCreateGroupSessionFlow.ts',
    ];
    const source = files.map((file) => readFileSync(path.join(process.cwd(), file), 'utf8')).join('\n');
    expect(source).not.toContain('antd');
    expect(source).not.toContain('message.');
    expect(source).not.toContain('animate-pulse');
    expect(source).not.toContain('bg-gray-');
    expect(source).not.toContain('src/internal');
    expect(source).not.toContain('queryCollaborationBots');
    expect(source).not.toContain('当前以 Human 身份');
    expect(source).not.toContain('当前用户身份不可用');
    expect(source).not.toContain(
      '可按 Bot 名称或 Owner 用户名称搜索公开 Bot，也可通过能力描述进行智能搜索，为我或我的 Bot 申请加对方 Bot 为好友。',
    );
    expect(source).toContain('输入能力或职责描述后，将智能搜索匹配的公开 Bot。');
    expect(source).toContain('onModeChange={botViewModel.setMode}');
    expect(source).toContain('COLLABORATION_SQUARE_PAGE_SIZE = 24');
    expect(source).toContain('new IntersectionObserver');
    expect(source).toContain('onScroll={handleScroll}');
    expect(source).toContain('rootMargin: `0px 0px ${LOAD_MORE_PRELOAD_DISTANCE}px 0px`');
    expect(source).not.toContain('发现协作群，支持基于公开协作群快速创建新会话。');
    expect(source).toContain('发现公开 BBS 求助任务');
    // 回归锁：卡片图标钮弹窗关闭时不得把焦点还给触发钮，否则其 tooltip 未 hover 凭空展开。
    // 回归锁（3 处）：分享弹窗阻断初始/归还抢焦、创建会话弹窗阻断归还抢焦——
    // 焦点落回带 tooltip 的触发钮/预览条名称段会令 tooltip 未 hover 凭空展开。
    expect(source.match(/\(event\) => event\.preventDefault\(\)\}/g)).toHaveLength(3);
    expect(source).toContain('resource="task"');
    expect(source).toContain('/collaboration-square/tasks');
  });
});
