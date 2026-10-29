/** @jest-environment jsdom */
import { CommunityTopicList } from '@/components/Community/CommunityTopicList';
import type { CommunityTopic } from '@/domain/community/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import type { RefObject } from 'react';

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '让 Bot 自然参与讨论',
  body: '讨论何时介入以及如何总结共识。',
  status: 'closed',
  topicType: 'discussion',
  author: { type: 'bot', id: 'bot-1', displayName: '协作助手' },
  replyCount: 3,
  likeCount: 7,
  latestActivityAt: '2026-10-06T06:20:00Z',
  createdAt: '2026-10-05T08:30:00Z',
  isHot: false,
  isMine: false,
  canClose: false,
  canEdit: false,
  canDelete: false,
};

type ListProps = React.ComponentProps<typeof CommunityTopicList>;

// 列表下拉加载监听滚动容器：jsdom 无真实布局，给 root 桩尺寸（scrollHeight/clientHeight）
// 与可设 scrollTop，用于验证「进页面未滚动不加载 / 滚到接近底部触发 loadMore」。
function renderList(overrides: Partial<ListProps> = {}, sizing?: { scrollHeight?: number; clientHeight?: number }) {
  const el = document.createElement('main');
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => sizing?.scrollHeight ?? 1000 });
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => sizing?.clientHeight ?? 500 });
  const scrollRootRef = { current: el } as RefObject<HTMLElement | null>;
  const props: ListProps = {
    topics: [topic],
    loading: false,
    error: null,
    hasFilter: false,
    onOpen: jest.fn(),
    onRetry: jest.fn(),
    onClearFilter: jest.fn(),
    onPublish: jest.fn(),
    hasMore: false,
    loadingMore: false,
    loadMoreError: null,
    loadMore: jest.fn(),
    scrollRootRef,
    ...overrides,
  };
  return { props, el, scrollRootRef, ...render(<CommunityTopicList {...props} />) };
}

describe('CommunityTopicList', () => {
  test('展示主题类型、互动数据、最新回复时间与结帖状态，支持键盘打开', () => {
    const { props } = renderList();
    expect(screen.getByText(topic.title)).toBeInTheDocument();
    expect(screen.getByText('协作助手')).toBeInTheDocument();
    expect(screen.getByText('讨论')).toBeInTheDocument();
    expect(screen.getByLabelText('回复 3')).toBeInTheDocument();
    expect(screen.getByLabelText('点赞 7')).toBeInTheDocument();
    expect(screen.getByText(/最新回复/)).toBeInTheDocument();
    expect(screen.getByText('已结帖')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('button', { name: /让 Bot 自然参与讨论/ }), { key: 'Enter' });
    expect(props.onOpen).toHaveBeenCalledWith(topic);
  });

  test('热榜主题显示火焰标记，普通主题不显示', () => {
    const { unmount } = renderList({ topics: [{ ...topic, isHot: true }] });
    expect(screen.getByText('🔥')).toBeInTheDocument();
    expect(screen.getByText('热榜主题')).toBeInTheDocument();
    unmount();

    renderList({ topics: [topic] });
    expect(screen.queryByText('🔥')).not.toBeInTheDocument();
    expect(screen.queryByText('热榜主题')).not.toBeInTheDocument();
  });

  test('投票主题列表标记投票类型', () => {
    renderList({ topics: [{ ...topic, topicType: 'poll' }] });
    expect(screen.getByRole('img', { name: '投票主题' })).toBeInTheDocument();
    expect(screen.getByText('投票')).toBeInTheDocument();
  });

  test('分享主题显示附件标识，求助主题标题旁显示求助图标', () => {
    const { unmount } = renderList({ topics: [{ ...topic, status: 'open', topicType: 'share' }] });
    expect(screen.getByRole('img', { name: '分享附件' })).toBeInTheDocument();
    expect(screen.getByText('分享')).toBeInTheDocument();
    unmount();

    renderList({ topics: [{ ...topic, status: 'open', topicType: 'help' }] });
    const helpIcon = screen.getByRole('img', { name: '求助' });
    expect(helpIcon).toHaveClass('text-warning');
    expect(screen.getAllByText('求助')).toHaveLength(1);
  });

  test('已关闭求助显示已解决而不是普通已结帖', () => {
    renderList({ topics: [{ ...topic, topicType: 'help' }] });
    expect(screen.getByText('已解决')).toHaveClass('text-success');
    expect(screen.queryByText('已结帖')).not.toBeInTheDocument();
  });

  test('无回复主题不渲染回复相关行，改为显示发帖时间', () => {
    const realTopic: CommunityTopic = {
      id: 'real-1',
      title: '真实后端主题',
      body: '只有正文预览',
      status: 'open',
      topicType: 'discussion',
      author: { type: 'human', id: '900003', displayName: '900003' },
      createdAt: '2026-10-05T08:00:00Z',
      isMine: false,
      canClose: false,
      canEdit: false,
      canDelete: false,
    };
    renderList({ topics: [realTopic] });
    expect(screen.getByText('900003')).toBeInTheDocument();
    expect(screen.getByLabelText('回复 0')).toBeInTheDocument();
    expect(screen.getByLabelText('点赞 0')).toBeInTheDocument();
    expect(screen.queryByText(/最新回复/)).not.toBeInTheDocument();
    expect(screen.getByText(/发帖时间/)).toBeInTheDocument();
  });

  test('请求失败时展示错误与重试操作', () => {
    const { props } = renderList({ topics: [], error: '网络异常' });
    expect(screen.getByText('网络异常')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
  });

  test('过滤后无结果时提供清空筛选', () => {
    const { props } = renderList({ topics: [], hasFilter: true });
    expect(screen.getByText('没有找到匹配主题')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清空筛选' }));
    expect(props.onClearFilter).toHaveBeenCalledTimes(1);
  });

  test('hasMore=false 时显示「没有更多主题了」且无加载更多', () => {
    renderList({ topics: [topic] });
    expect(screen.getByText('没有更多主题了')).toBeInTheDocument();
  });

  test('loadingMore=true 时显示「正在加载更多」', () => {
    renderList({ hasMore: true, loadingMore: true });
    expect(screen.getByText('正在加载更多...')).toBeInTheDocument();
  });

  test('进页面未滚动时不自动加载下一页（不再静默刷出剩余）', () => {
    const { props } = renderList({ hasMore: true });
    expect(props.loadMore).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /加载更多/ })).toBeInTheDocument();
  });

  test('用户向下滚动接近底部时触发 loadMore（下拉加载）', () => {
    const { props, el } = renderList({ hasMore: true });
    expect(props.loadMore).not.toHaveBeenCalled();
    // scrollTop=600：remaining = 1000 - 600 - 500 = -100 <= 预载距 320，且 scrollTop>0
    el.scrollTop = 600;
    fireEvent.scroll(el);
    expect(props.loadMore).toHaveBeenCalledTimes(1);
  });

  test('静止/滚回顶部（scrollTop=0）不触发 loadMore', () => {
    const { props, el } = renderList({ hasMore: true });
    el.scrollTop = 0;
    fireEvent.scroll(el);
    expect(props.loadMore).not.toHaveBeenCalled();
  });

  test('列表内容不足以撑满容器（不可滚动）时，向下滚轮触发 loadMore', () => {
    // scrollHeight(400) <= clientHeight(500)：容器无滚动条，纯 scroll 事件无法捕获用户「向下滚」意图。
    const { props, el } = renderList({ hasMore: true }, { scrollHeight: 400, clientHeight: 500 });
    fireEvent.wheel(el, { deltaY: 100 });
    expect(props.loadMore).toHaveBeenCalledTimes(1);
  });

  test('向上滚轮不触发 loadMore（即使容器不可滚动）', () => {
    const { props, el } = renderList({ hasMore: true }, { scrollHeight: 400, clientHeight: 500 });
    fireEvent.wheel(el, { deltaY: -100 });
    expect(props.loadMore).not.toHaveBeenCalled();
  });

  test('可滚动但未接近底部时向下滚轮不触发 loadMore，待接近底部再触发', () => {
    const { props, el } = renderList({ hasMore: true }, { scrollHeight: 1000, clientHeight: 500 });
    // 顶部下滚：remaining=1000-0-500=500 > 预载距 320，未接近底部。
    fireEvent.wheel(el, { deltaY: 120 });
    expect(props.loadMore).not.toHaveBeenCalled();
    // 滚到接近底部：remaining=1000-600-500=-100 <= 320。
    el.scrollTop = 600;
    fireEvent.wheel(el, { deltaY: 120 });
    expect(props.loadMore).toHaveBeenCalledTimes(1);
  });

  test('loadMoreError 时展示错误并提供重试', () => {
    const { props } = renderList({ hasMore: true, loadMoreError: '加载更多失败' });
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(props.loadMore).toHaveBeenCalled();
  });

  test('有更多时「加载更多」按钮可点击触发，未操作不自动加载', () => {
    const { props } = renderList({ hasMore: true });
    expect(props.loadMore).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /加载更多/ }));
    expect(props.loadMore).toHaveBeenCalledTimes(1);
  });

  test('「加载更多」按钮 hover 时预取下一页，降低点击等待', () => {
    const { props } = renderList({ hasMore: true });
    fireEvent.mouseEnter(screen.getByRole('button', { name: /加载更多/ }));
    expect(props.loadMore).toHaveBeenCalledTimes(1);
  });

  test('「加载更多」按钮键盘聚焦时预取下一页', () => {
    const { props } = renderList({ hasMore: true });
    fireEvent.focus(screen.getByRole('button', { name: /加载更多/ }));
    expect(props.loadMore).toHaveBeenCalledTimes(1);
  });
});
