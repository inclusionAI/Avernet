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
  replyPage: 1,
  replyPageCount: 1,
  hasMore: false,
  loadingMore: false,
  loadMoreError: null,
  publishing: false,
  openTopic: jest.fn(),
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
jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

/**
 * 滚动结构回归：列表滚动依赖「h-full flex flex-col 外层 + main(min-h-0 flex-1 overflow-y-auto)」
 * 的 CSS 链。AppShell 内容区是块级 overflow-hidden 容器——若 main 直接作为其子项（无 flex 外层
 * 锚定高度），flex-1/min-h-0 全部失效，内容被截断且无滚动条（详见社区滚动 bug 修复）。
 */
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

  test('主题类型筛选后移除范围按钮，保留可点击关注、当前用户头像和发帖入口', () => {
    render(<CommunityPage />);

    const topicTypeGroup = screen.getByRole('group', { name: '主题类型' });
    const searchInput = screen.getByRole('textbox', { name: '搜索社区主题' });
    const personalCenterButton = screen.getByRole('button', { name: '个人中心' });
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
    expect(topicTypeGroup).toHaveClass('bg-background', 'sm:w-2/5', 'sm:min-w-[240px]', 'sm:max-w-[50%]');
    expect(topicTypeGroup.compareDocumentPosition(searchInput) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(personalCenterButton).getByAltText('张三').getAttribute('src')).toBe(
      'https://example.com/avatar.png',
    );
    expect(searchInput.compareDocumentPosition(personalCenterButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(personalCenterButton).toBeEnabled();
    expect(personalCenterButton.compareDocumentPosition(publishButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(within(topicTypeGroup).getByRole('button', { name: '分享' }));
    expect(mockCommunityState.setTopicType).toHaveBeenCalledWith('share');
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
