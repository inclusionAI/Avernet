/** @jest-environment jsdom */
import { CommunityPage } from '@/components/Community/CommunityPage';
import type { CommunityTopic } from '@/domain/community/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '回归主题',
  body: '滚动结构断言用正文。',
  status: 'open',
  topicType: 'discussion',
  author: { type: 'human', id: 'human-1', displayName: '作者' },
  createdAt: '2026-10-06T06:20:00Z',
  isMine: false,
  canClose: false,
  canEdit: false,
  canDelete: false,
};

const mockCommunityState = {
  query: '',
  scope: 'all' as const,
  topicType: 'all' as const,
  currentUser: {
    type: 'human' as const,
    id: 'human-1',
    displayName: '张三',
    avatarUrl: 'https://example.com/avatar.png',
  },
  selectedTopic: null as CommunityTopic | null,
  topics: [topic],
  loading: false,
  error: null,
  detailLoading: false,
  detailError: null,
  closing: false,
  helpfulPostId: null,
  replies: [],
  replyLoading: false,
  replyError: null,
  firstReplyPage: 1,
  lastReplyPage: 1,
  replyPageCount: 1,
  hasMore: false,
  loadingMore: false,
  loadMoreError: null,
  publishing: false,
  openTopic: jest.fn(),
  openTopicById: jest.fn(),
  closeDetail: jest.fn(),
  closeTopic: jest.fn(),
  setPostHelpful: jest.fn(),
  goToReplyPage: jest.fn(),
  setQuery: jest.fn(),
  setScope: jest.fn(),
  setTopicType: jest.fn(),
  load: jest.fn(),
  loadMore: jest.fn(),
  publishTopic: jest.fn(),
};

jest.mock('@/hooks/useCommunity', () => ({
  useCommunity: () => mockCommunityState,
  COMMUNITY_REPLY_PAGE_SIZE: 10,
}));
let mockSearch = '';
jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useSearchParams: () => [new URLSearchParams(mockSearch)],
}));

/**
 * 滚动结构回归：列表滚动依赖「h-full flex flex-col 外层 + main(min-h-0 flex-1 overflow-y-auto)」
 * 的 CSS 链。AppShell 内容区是块级 overflow-hidden 容器——若 main 直接作为其子项（无 flex 外层
 * 锚定高度），flex-1/min-h-0 全部失效，内容被截断且无滚动条（详见社区滚动 bug 修复）。
 */
describe('CommunityPage 通知深链', () => {
  test('携带 topicId/postId 时打开对应主题并定位楼层', () => {
    mockSearch = '?topicId=topic-1&postId=post-1';
    try {
      render(<CommunityPage />);
      expect(mockCommunityState.openTopicById).toHaveBeenCalledWith({
        topicId: 'topic-1',
        postId: 'post-1',
      });
    } finally {
      mockSearch = '';
    }
  });
});

describe('CommunityPage 滚动容器结构', () => {
  test('列表视：滚动 main 必须是 h-full flex 外层的 flex 子项，否则 overflow-y-auto 失效', () => {
    render(<CommunityPage />);
    const publishButton = screen.getByRole('button', { name: '发帖' });
    expect(publishButton).toHaveClass('bg-primary', 'text-primary-foreground');
    expect(publishButton).not.toHaveClass('bg-foreground');
    const scrollMain = screen.getByRole('main');
    expect(scrollMain).toHaveClass('overflow-y-auto', 'flex-1', 'min-h-0');
    expect(scrollMain.parentElement).toHaveClass('h-full', 'flex', 'flex-col');
  });

  test('主题类型筛选后移除范围按钮，保留可点击关注、主视图标签和发帖入口', () => {
    render(<CommunityPage />);

    const topicTypeGroup = screen.getByRole('group', { name: '主题类型' });
    const searchInput = screen.getByRole('textbox', { name: '搜索社区主题' });
    const personalCenterButton = screen.getByRole('tab', { name: '我的' });
    const publishButton = screen.getByRole('button', { name: '发帖' });

    expect(
      within(topicTypeGroup)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['全部', '关注', '讨论', '分享', '投票', '求助']);
    expect(screen.queryByRole('group', { name: '主题范围' })).not.toBeInTheDocument();
    const followingButton = within(topicTypeGroup).getByRole('button', { name: '关注' });
    expect(followingButton).toBeEnabled();
    fireEvent.click(followingButton);
    expect(mockCommunityState.setTopicType).not.toHaveBeenCalled();
    expect(mockCommunityState.setScope).toHaveBeenCalledWith('following');
    expect(screen.getByText(topic.title)).toBeInTheDocument();
    fireEvent.click(within(topicTypeGroup).getByRole('button', { name: '分享' }));
    expect(screen.getByText(topic.title)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '全部帖子', selected: true })).toBeVisible();
    expect(personalCenterButton).toHaveAttribute('aria-selected', 'false');
    expect(searchInput).toBeVisible();
    expect(publishButton).toBeEnabled();

    fireEvent.click(within(topicTypeGroup).getByRole('button', { name: '分享' }));
    expect(mockCommunityState.setTopicType).toHaveBeenCalledWith('share');
  });

  test('社区主 Tab 使用 Bot 管理相同的浅蓝色块，并随选择切换', () => {
    render(<CommunityPage />);
    const tabs = within(screen.getByRole('tablist', { name: '社区视图' }));
    const all = tabs.getByRole('tab', { name: '全部帖子' });
    const mine = tabs.getByRole('tab', { name: '我的' });
    expect(all).toHaveClass('rounded-md', 'bg-primary/10');
    expect(mine).not.toHaveClass('bg-primary/10');
    fireEvent.click(mine);
    expect(mine).toHaveClass('rounded-md', 'bg-primary/10');
    expect(mine).toHaveAttribute('aria-selected', 'true');
    expect(all).not.toHaveClass('bg-primary/10');
  });

  test('全部帖子和我的共用第二排筛选栏样式，保留各自的筛选项', () => {
    render(<CommunityPage />);
    const topics = screen.getByRole('group', { name: '主题类型' });
    const all = within(topics).getByRole('button', { name: '全部', pressed: true });
    const following = within(topics).getByRole('button', { name: '关注', pressed: false });
    expect(screen.getByRole('tab', { name: '全部帖子', selected: true })).toHaveClass(
      'text-[13px]', 'font-medium',
    );
    for (const button of within(topics).getAllByRole('button')) {
      expect(button).toHaveClass('text-[13px]');
      expect(button).toHaveClass(button === all ? 'font-medium' : 'font-normal');
    }

    fireEvent.click(screen.getByRole('tab', { name: '我的' }));

    const mine = screen.getByRole('group', { name: '我的内容' });
    const authored = within(mine).getByRole('button', { name: '我的帖子', pressed: true });
    const replied = within(mine).getByRole('button', { name: '我的回帖', pressed: false });
    expect(mine.parentElement?.className).toBe(topics.parentElement?.className);
    expect(mine.className).toBe(topics.className);
    expect(authored.className).toBe(all.className);
    expect(replied.className).toBe(following.className);
  });

  test('我的视图保留搜索和发帖，确认搜索返回帖子列表', () => {
    render(<CommunityPage />);
    fireEvent.click(screen.getByRole('tab', { name: '我的' }));
    const search = screen.getByRole('textbox', { name: '搜索社区主题' });
    expect(search).toBeVisible();
    expect(screen.getByRole('button', { name: '发帖' })).toBeEnabled();
    fireEvent.change(search, { target: { value: '测试主题' } });
    expect(mockCommunityState.setQuery).toHaveBeenCalledWith('测试主题');
    mockCommunityState.query = '测试主题';
    try {
      fireEvent.keyDown(search, { key: 'Enter' });
      expect(screen.getByRole('tab', { name: '全部帖子', selected: true })).toBeVisible();
    } finally {
      mockCommunityState.query = '';
    }
  });

  test('主题类型筛选 tab 图标与发帖弹窗保持一致', () => {
    render(<CommunityPage />);
    const topicTypeGroup = screen.getByRole('group', { name: '主题类型' });
    const expectedIcons: Record<string, string[]> = {
      关注: ['community-follow-icon', 'lucide-user-round-check'],
      讨论: ['lucide-message-circle'],
      分享: ['lucide-share-2'],
      投票: ['lucide-chart-line'],
      求助: ['lucide-circle-question-mark'],
    };
    for (const [label, iconClasses] of Object.entries(expectedIcons)) {
      const icon = within(topicTypeGroup)
        .getByRole('button', { name: label })
        .querySelector('svg');
      expect(icon).toHaveClass(...iconClasses);
    }
  });

  test('详情视：回帖滚动 main 必须挂在 h-full flex 外层下', () => {
    mockCommunityState.selectedTopic = topic;
    render(<CommunityPage />);
    const scrollMain = screen.getByRole('main');
    expect(scrollMain).toHaveClass('overflow-y-auto', 'flex-1', 'min-h-0');
    expect(scrollMain.parentElement).toHaveClass('h-full', 'flex', 'flex-col');
    mockCommunityState.selectedTopic = null;
  });
});
