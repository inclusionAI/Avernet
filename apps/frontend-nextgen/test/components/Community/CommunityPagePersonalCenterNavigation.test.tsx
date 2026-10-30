/** @jest-environment jsdom */
import { CommunityPage } from '@/components/Community/CommunityPage';
import type { CommunityTopic } from '@/domain/community/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '回归主题',
  body: '正文',
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
    id: 'human-1',
    displayName: '张三',
  },
  selectedTopic: null as CommunityTopic | null,
  topics: [],
  loading: false,
  error: null,
  hasMore: false,
  loadingMore: false,
  loadMoreError: null,
  load: jest.fn(),
  loadMore: jest.fn(),
  openTopic: jest.fn(),
  openTopicById: jest.fn(),
  closeDetail: jest.fn(),
};

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

jest.mock('@/hooks/useCommunity', () => ({
  useCommunity: () => mockCommunityState,
  COMMUNITY_REPLY_PAGE_SIZE: 10,
}));

jest.mock('@/components/Community/CommunityPersonalCenter', () => {
  const { useState } = require('react');
  function MockPersonalCenter({
    onOpenTopic,
  }: {
    onBack: () => void;
    onOpenTopic: (topicId: string) => void;
  }) {
    const [view, setView] = useState('我的帖子');
    const [selectedAuthor, setSelectedAuthor] = useState(false);
    return (
      <div>
        <span>{`当前状态：${view}${selectedAuthor ? ':张三的帖子列表' : ''}`}</span>
        <button type="button" onClick={() => setView('我的回帖')}>
          切换到我的回帖
        </button>
        <button
          type="button"
          onClick={() => {
            setView('我的关注');
            setSelectedAuthor(true);
          }}
        >
          选择关注的张三
        </button>
        <button type="button" onClick={() => onOpenTopic('topic-1')}>
          打开个人中心主题
        </button>
      </div>
    );
  }
  return { CommunityPersonalCenter: MockPersonalCenter };
});
jest.mock('@/components/Community/CommunityTopicDetail', () => ({
  CommunityTopicDetail: ({ onBack }: { onBack: () => void }) => (
    <button type="button" onClick={onBack}>
      返回上一级
    </button>
  ),
}));

describe('CommunityPage 个人中心导航', () => {
  test('从个人中心进入详情后，详情返回应回到个人中心', () => {
    const view = render(<CommunityPage />);

    fireEvent.click(screen.getByRole('button', { name: '个人中心' }));
    expect(screen.getByText('打开个人中心主题')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '切换到我的回帖' }));
    fireEvent.click(screen.getByText('打开个人中心主题'));
    expect(mockCommunityState.openTopicById).toHaveBeenCalledWith('topic-1');

    // The real Zustand store re-renders when openTopicById selects the topic.
    mockCommunityState.selectedTopic = topic;
    view.rerender(<CommunityPage />);
    fireEvent.click(screen.getByText('返回上一级'));

    // The real Zustand store re-renders when closeDetail clears the topic.
    mockCommunityState.selectedTopic = null;
    view.rerender(<CommunityPage />);
    expect(screen.getByText('当前状态：我的回帖')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '选择关注的张三' }));
    fireEvent.click(screen.getByText('打开个人中心主题'));

    mockCommunityState.selectedTopic = topic;
    view.rerender(<CommunityPage />);
    fireEvent.click(screen.getByText('返回上一级'));

    mockCommunityState.selectedTopic = null;
    view.rerender(<CommunityPage />);

    expect(mockCommunityState.closeDetail).toHaveBeenCalledTimes(2);
    expect(screen.getByText('打开个人中心主题')).toBeInTheDocument();

    // Returning from detail must preserve both the active activity tab and the
    // selected followed author, rather than remounting Personal Center.
    expect(screen.getByText('当前状态：我的关注:张三的帖子列表')).toBeInTheDocument();
  });
});
