/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation/types';
import { ConversationSidebar } from '@/pages/Workspace/Chat/components/ConversationSidebar';
import { WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY } from '@/pages/Workspace/components/ResizableWorkspaceSidebar';
import { useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import {
  botOf,
  managedBotA,
  friendBot,
  teamBot,
  sessionOf,
  emptyList,
  directoryModel,
  sessionsModel,
  seedStore,
  sidebarProps,
} from './ConversationSidebar.testUtils';

// jsdom 未实现 scrollIntoView:Radix Select 打开/选中时滚动选中项会抛 TypeError 中断选择流程,
// 幂等补齐空实现(项目先例:internal/capability-workshop 各测试同款;创建弹窗目标空间下拉需要)。
Element.prototype.scrollIntoView ??= () => undefined;

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: '327325', displayName: '张三', online: true },
    status: 'ready',
  }),
}));

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  // 整栏收起记忆持久化在 localStorage:用例间清掉,避免收起竖轨用例泄漏到其余用例。
  window.localStorage.removeItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY);
  useConversationStore.getState().reset();
});

it('团队位于管理与好友之间，搜索可命中，展开回调携带 team', () => {
  const team: ConversationBotView = { ...botOf('shared:entity', '团队助手'), section: 'team' };
  const sessions = sessionsModel();
  render(<ConversationSidebar {...sidebarProps({ teamBots: [team], sessions })} />);
  const managed = screen.getByRole('group', { name: '我管理的 Bot' });
  const teamGroup = screen.getByRole('group', { name: '我协作的 Bot' });
  const friend = screen.getByRole('group', { name: '好友 Bot' });
  expect(managed.compareDocumentPosition(teamGroup) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(teamGroup.compareDocumentPosition(friend) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', { name: '搜索 Bot' }), { target: { value: '团队助手' } });
  fireEvent.click(screen.getByRole('button', { name: '团队助手' }));
  expect(sessions.toggleBot).toHaveBeenCalledWith('shared:entity', 'team');
  expect(screen.queryByText('未找到匹配的 Bot')).not.toBeInTheDocument();
});

it('团队加载结束后空列表保留分组，加载失败仍可单独重试', () => {
  const retry = jest.fn();
  const view = render(
    <ConversationSidebar {...sidebarProps({ directory: directoryModel({ teamLoading: true }) })} />,
  );
  expect(screen.getByRole('group', { name: '我协作的 Bot' })).toBeInTheDocument();
  expect(screen.queryByText('暂无协作的 Bot')).not.toBeInTheDocument();
  view.rerender(<ConversationSidebar {...sidebarProps({ teamBots: [] })} />);
  expect(screen.getByRole('group', { name: '我协作的 Bot' })).toBeInTheDocument();
  expect(screen.getByText('暂无协作的 Bot')).toBeInTheDocument();
  // 团队目录独立失败:分组出现并给错误态+重试,不阻塞管理/好友分组。
  view.rerender(
    <ConversationSidebar
      {...sidebarProps({ teamBots: [], directory: directoryModel({ teamError: '团队加载失败', retryTeam: retry }) })}
    />,
  );
  const teamGroup = screen.getByRole('group', { name: '我协作的 Bot' });
  expect(within(teamGroup).getByText('团队加载失败')).toBeInTheDocument();
  fireEvent.click(within(teamGroup).getByRole('button', { name: '重试' }));
  expect(retry).toHaveBeenCalledTimes(1);
});

it('搜索中团队组无匹配时保留分类标题，不展示目录空态', () => {
  render(
    <ConversationSidebar
      {...sidebarProps({ teamBots: [teamBot], directory: directoryModel({ teamBots: [teamBot] }) })}
    />,
  );
  expect(screen.getByRole('group', { name: '我协作的 Bot' })).toBeInTheDocument();
  // 关键字只命中我管理的 Bot，团队组仍保留标题，与其他分类保持一致。
  fireEvent.change(screen.getByRole('textbox', { name: '搜索 Bot' }), { target: { value: '管理' } });
  expect(screen.getByRole('group', { name: '我协作的 Bot' })).toBeInTheDocument();
  expect(screen.queryByText('暂无协作的 Bot')).not.toBeInTheDocument();
});

it('团队与管理 Bot 都能选择他人来源，并且只更新各自的筛选状态', () => {
  render(<ConversationSidebar {...sidebarProps({ teamBots: [teamBot] })} />);
  const chooseOthers = (groupName: string) => {
    fireEvent.click(
      within(screen.getByRole('group', { name: groupName })).getByRole('button', { name: '发起人与会话筛选' }),
    );
    const dialog = document.querySelector('[data-state="open"][role="dialog"]');
    expect(dialog).not.toBeNull();
    const others = within(dialog as HTMLElement).getByRole('button', { name: '他人发起' });
    expect(others).toBeEnabled();
    fireEvent.click(others);
  };
  chooseOthers('我协作的 Bot');
  expect(useConversationStore.getState().originByManagedBotId[teamBot.bot.botId]).toBe('others');
  expect(useConversationStore.getState().originByManagedBotId[managedBotA.bot.botId]).toBeUndefined();
  chooseOthers('我管理的 Bot');
  expect(useConversationStore.getState().originByManagedBotId[managedBotA.bot.botId]).toBe('others');
  expect(useConversationStore.getState().originByManagedBotId[teamBot.bot.botId]).toBe('others');
});

describe('ConversationSidebar #2990 还原接线（菜单传导 / 回复中指示 / infoHint）', () => {
  it('onOpenBotManagement 仅经管理/团队行传导:两行出「跳转至 Bot 管理」,好友行不出(好友无空间上下文)', () => {
    const onOpenBotManagement = jest.fn();
    const spaceManaged = { ...managedBotA, bot: { ...managedBotA.bot, spaceId: '101' } };
    const spaceTeam = { ...teamBot, bot: { ...teamBot.bot, spaceId: '202' } };
    render(
      <ConversationSidebar
        {...sidebarProps({
          managedBots: [spaceManaged],
          teamBots: [spaceTeam],
          friendBots: [friendBot],
          directory: directoryModel({
            managedBots: [spaceManaged],
            teamBots: [spaceTeam],
            friendBots: [friendBot],
          }),
          onOpenBotManagement,
        })}
      />,
    );
    // 「跳转至 Bot 管理」入口仅出现在管理行与团队行;好友行不渲染。
    const entries = screen.getAllByRole('button', { name: '跳转至 Bot 管理' });
    expect(entries).toHaveLength(2);
    // 管理行前置直达:直接点击触发回调,携带所属空间信息(跳转侧带 ?keyword 过滤该 Bot)。
    fireEvent.click(entries[0]);
    expect(onOpenBotManagement).toHaveBeenCalledTimes(1);
    expect(onOpenBotManagement).toHaveBeenCalledWith(
      expect.objectContaining({ botId: 'bot-a:2088', spaceId: '101' }),
    );
  });

  it('replyingSessionId 传导至会话行:命中行渲染「Bot 正在回复」,未命中行不渲染', () => {
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'mine',
        scope: 'all',
        sessions: { ...emptyList(), items: [sessionOf('s-a')] },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
    });
    render(<ConversationSidebar {...sidebarProps({ store, replyingSessionId: 's-a' })} />);
    expect(screen.getAllByRole('status', { name: 'Bot 正在回复' })).toHaveLength(1);
  });

  it('我协作的 Bot 分组标题悬停出来源提示 infoHint(#2990 还原;2026-11-05 触发方式微调:悬停标题替代 i 徽标),切换按钮可访问名保持纯标题', async () => {
    render(
      <ConversationSidebar
        {...sidebarProps({
          teamBots: [teamBot],
          directory: directoryModel({ teamBots: [teamBot] }),
        })}
      />,
    );
    // 既有契约不回归:标题即开关、可访问名纯「我协作的 Bot」;悬停标题即出来源提示。
    const header = screen.getByRole('button', { name: '我协作的 Bot' });
    fireEvent.pointerMove(header, { pointerType: 'mouse' });
    fireEvent.focusIn(header);
    await waitFor(() => expect(screen.getByText('团队空间下其他成员创建并邀请我协作的 Bot')).toBeInTheDocument());
  });

  describe('分组标题行快捷入口（2026-11-05 需求：创建/发现常驻入口）', () => {
    it('我管理的 Bot 分组标题行常驻创建入口,点击弹创建确认弹窗(2026-11-05 三轮:目标空间下拉):选空间不直接触发,确认才带所选空间回调,默认选中当前空间', async () => {
      const createEntrySpaces = [
        {
          spaceId: 1,
          spaceCode: 'personal',
          spaceName: '个人空间',
          spaceType: 'PERSONAL' as const,
          memberCount: 1,
          ownerCount: 1,
          botCount: 0,
          gmtModified: '2026-11-05 00:00:00',
        },
        {
          spaceId: 2,
          spaceCode: 'team-test',
          spaceName: '测试空间',
          spaceType: 'TEAM' as const,
          memberCount: 2,
          ownerCount: 1,
          botCount: 3,
          gmtModified: '2026-11-05 00:00:00',
        },
      ];
      const props = sidebarProps({ createEntrySpaces, createEntryCurrentSpaceId: 2 });
      render(<ConversationSidebar {...props} />);
      const managedGroup = screen.getByRole('group', { name: '我管理的 Bot' });
      fireEvent.click(within(managedGroup).getByRole('button', { name: '创建新 Bot' }));
      // 弹窗打开:说明落地页为 Bot 管理并要求先选归属空间;「目标空间」下拉默认选中当前空间(测试空间)。
      const dialog = screen.getByRole('dialog');
      expect(within(dialog).getByText('创建云端 Bot')).toBeInTheDocument();
      expect(within(dialog).getByText('将前往「Bot 管理」页面完成创建，请先选择 Bot 归属的空间。')).toBeInTheDocument();
      expect(within(dialog).getByText('目标空间')).toBeInTheDocument();
      expect(within(dialog).getByText('测试空间')).toBeInTheDocument();
      // 展开下拉选择其他空间仅更新选中(不直接触发回调);确认后以选中项回调。
      fireEvent.click(within(dialog).getByRole('combobox', { name: '目标空间' }));
      fireEvent.click(await screen.findByRole('option', { name: /个人空间/ }));
      expect(props.onCreateBotInSpace).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole('button', { name: '前往创建' }));
      expect(props.onCreateBotInSpace).toHaveBeenCalledWith(1);
      // 默认选中当前空间:重开弹窗不改选,直接确认带当前空间(测试空间)回调。
      fireEvent.click(within(managedGroup).getByRole('button', { name: '创建新 Bot' }));
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '前往创建' }));
      expect(props.onCreateBotInSpace).toHaveBeenCalledWith(2);
      // 取消不触发回调(次数维持前两段确认的 2 次)。
      fireEvent.click(within(managedGroup).getByRole('button', { name: '创建新 Bot' }));
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' }));
      expect(props.onCreateBotInSpace).toHaveBeenCalledTimes(2);
      expect(props.onOpenBotWorkshop).not.toHaveBeenCalled();
    });

    it('好友 Bot 分组标题行常驻发现入口,点击触发公开 Bot 跳转回调', () => {
      const props = sidebarProps();
      render(<ConversationSidebar {...props} />);
      const friendGroup = screen.getByRole('group', { name: '好友 Bot' });
      const discoverEntry = within(friendGroup).getByRole('button', { name: '添加 Bot 好友' });
      fireEvent.click(discoverEntry);
      expect(props.onOpenPublicBots).toHaveBeenCalledTimes(1);
    });

    it('我协作的 Bot 分组标题行不设动作入口', () => {
      render(
        <ConversationSidebar
          {...sidebarProps({
            teamBots: [teamBot],
            directory: directoryModel({ teamBots: [teamBot] }),
          })}
        />,
      );
      const teamGroup = screen.getByRole('group', { name: '我协作的 Bot' });
      // 该组 Bot 靠空间申请或 owner 分享获得,非用户单方动作 → 不渲染创建/发现快捷入口。
      expect(within(teamGroup).queryByRole('button', { name: '创建新 Bot' })).toBeNull();
      expect(within(teamGroup).queryByRole('button', { name: '添加 Bot 好友' })).toBeNull();
      expect(within(teamGroup).getByRole('button', { name: '我协作的 Bot' })).toBeInTheDocument();
    });

    it('分组收起时快捷入口保持可见可点(常驻语义)', () => {
      const base = sidebarProps();
      const { rerender } = render(<ConversationSidebar {...base} />);
      const syncStore = () => rerender(<ConversationSidebar {...base} store={useConversationStore.getState()} />);
      fireEvent.click(screen.getByRole('button', { name: '我管理的 Bot' }));
      fireEvent.click(screen.getByRole('button', { name: '好友 Bot' }));
      syncStore();
      // 收起即净只清行列表,标题行快捷入口常驻:创建入口仍可弹确认弹窗,发现入口仍可点。
      fireEvent.click(
        within(screen.getByRole('group', { name: '我管理的 Bot' })).getByRole('button', {
          name: '创建新 Bot',
        }),
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText('创建云端 Bot')).toBeInTheDocument();
      // 弹窗为 modal(遮罩 + body aria-hidden):先取消关闭再验证发现入口常驻可点。
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' }));
      const discoverEntry = within(screen.getByRole('group', { name: '好友 Bot' })).getByRole('button', {
        name: '添加 Bot 好友',
      });
      fireEvent.click(discoverEntry);
      expect(base.onOpenPublicBots).toHaveBeenCalledTimes(1);
    });

    it('空态下常驻入口与空态文字引导并存', () => {
      render(
        <ConversationSidebar
          {...sidebarProps({
            managedBots: [],
            friendBots: [],
            directory: directoryModel({ managedBots: [], friendBots: [], teamBots: [] }),
          })}
        />,
      );
      // 空态文字引导保留(2026-10-14 体验修复不回归)。
      expect(screen.getByText('暂无管理的 Bot')).toBeInTheDocument();
      expect(screen.getByText('暂无好友 Bot')).toBeInTheDocument();
      // 常驻快捷入口与空态引导同屏并存。
      const managedGroup = screen.getByRole('group', { name: '我管理的 Bot' });
      expect(within(managedGroup).getByRole('button', { name: '创建新 Bot' })).toBeInTheDocument();
      const friendGroup = screen.getByRole('group', { name: '好友 Bot' });
      expect(within(friendGroup).getByRole('button', { name: '添加 Bot 好友' })).toBeInTheDocument();
    });
  });
});

it('空团队目录在整栏收起后仍保留分类入口，点击可展开并查看空态', () => {
  window.localStorage.setItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY, 'true');
  render(<ConversationSidebar {...sidebarProps({ teamBots: [] })} />);
  fireEvent.click(screen.getByRole('button', { name: '展开侧栏，添加我协作的 Bot' }));
  expect(screen.getByRole('group', { name: '我协作的 Bot' })).toBeInTheDocument();
  expect(screen.getByText('暂无协作的 Bot')).toBeInTheDocument();
});
