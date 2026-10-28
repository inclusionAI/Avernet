/** @jest-environment jsdom */
import { CommunityTopicDetail } from '@/components/Community/CommunityTopicDetail';
import type { CommunityReply, CommunityTopic } from '@/domain/community/types';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '社区主题',
  body: '楼主正文',
  status: 'open',
  topicType: 'discussion',
  author: { type: 'human', id: 'human-1', displayName: '顾客' },
  replyCount: 1,
  latestActivityAt: '',
  createdAt: '',
  isMine: true,
  canClose: true,
};
const replies: CommunityReply[] = [
  {
    id: 'reply-1',
    topicId: topic.id,
    body: 'Bot 回复',
    author: { type: 'bot', id: 'bot-1', displayName: '协作助手' },
    createdAt: '2026-10-06T06:20:00Z',
    helpful: false,
  },
];

type DetailProps = React.ComponentProps<typeof CommunityTopicDetail>;

function renderDetail(overrides: Partial<DetailProps> = {}) {
  const props: DetailProps = {
    topic,
    replies,
    loading: false,
    error: null,
    closing: false,
    helpfulPostId: null,
    onBack: jest.fn(),
    onCloseTopic: jest.fn(),
    onSetPostHelpful: jest.fn(),
    replyLoading: false,
    replyError: null,
    replyPage: 1,
    replyPageCount: 1,
    replyPageSize: 10,
    onGoToReplyPage: jest.fn(),
    ...overrides,
  };
  return { props, ...render(<CommunityTopicDetail {...props} />) };
}

describe('CommunityTopicDetail', () => {
  test('展示楼层：botname→楼层→正文→发表时间，含返回箭头；缺失时间安全回退', () => {
    const { props } = renderDetail();
    expect(screen.getByText('楼主正文')).toBeInTheDocument();
    expect(screen.getByText('Bot 回复')).toBeInTheDocument();
    expect(screen.getByText('顾客')).toBeInTheDocument();
    expect(screen.getByText('协作助手')).toBeInTheDocument();
    expect(screen.getByText('楼主')).toHaveClass('bg-foreground', 'text-background');
    expect(screen.getByText('1楼')).toBeInTheDocument();
    expect(screen.getByText('发表于：时间未知')).toBeInTheDocument();
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回社区列表' }));
    expect(props.onBack).toHaveBeenCalledTimes(1);
  });

  test('开放讨论主题作为只读流展示，不提供 Human 回复入口', () => {
    renderDetail();

    expect(screen.getByText('Bot 回复')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '回复正文' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '回复第1楼' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '回复协作助手' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发送回复' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '回复编辑区' })).not.toBeInTheDocument();
  });

  test('投票主题只读展示选项和统计，不提供 Human 投票入口', () => {
    renderDetail({
      topic: {
        ...topic,
        topicType: 'poll',
        topicConfig: {
          poll: {
            selection: 'single',
            options: ['方案 A', '方案 B', '方案 C'],
            deadlineAt: '2999-10-20T12:00:00Z',
          },
        },
        analysis: {
          tags: ['AI治理', '投票'],
          pollStats: {
            totalVotes: 3,
            participantCount: 3,
            options: [
              { label: '方案 A', votes: 2, percent: 67 },
              { label: '方案 B', votes: 1, percent: 33 },
              { label: '方案 C', votes: 0, percent: 0 },
            ],
            updatedAt: '2026-10-19T12:00:00Z',
          },
        },
      },
    });

    expect(screen.getByRole('region', { name: '投票配置' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '投票主题' })).toBeInTheDocument();
    expect(screen.getByText('单选')).toBeInTheDocument();
    expect(screen.getByText('方案 A')).toBeInTheDocument();
    expect(screen.getByText('2票 · 67%')).toBeInTheDocument();
    expect(screen.getByText('1票 · 33%')).toBeInTheDocument();
    expect(screen.getByText('0票 · 0%')).toBeInTheDocument();
    expect(screen.getByText('单选 • 3 位参与者 • 共3票')).toBeInTheDocument();
    expect(screen.getByText('#AI治理')).toBeInTheDocument();
    expect(screen.getByText('#投票')).toBeInTheDocument();
    expect(screen.getByText(/进行中，截止/)).toBeInTheDocument();

    expect(screen.queryByRole('button', { name: /方案 A/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交投票' })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '回复正文' })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '投票理由' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发送回复' })).not.toBeInTheDocument();
  });

  test('分享主题展示分享类型标识', () => {
    renderDetail({ topic: { ...topic, topicType: 'share' } });

    expect(screen.getByText('分享')).toBeInTheDocument();
  });

  test('求助主题展示期望时间，关闭后显示已解决', () => {
    renderDetail({
      topic: {
        ...topic,
        status: 'closed',
        canClose: false,
        topicType: 'help',
        topicConfig: { help: { expectedTime: '希望本周内' } },
      },
    });

    expect(screen.getByText('求助')).toHaveClass('text-warning');
    expect(screen.getByText('已解决')).toHaveClass('text-success');
    expect(screen.getByText('希望本周内')).toBeInTheDocument();
    expect(screen.queryByText('已结帖')).not.toBeInTheDocument();
  });

  test('求助作者可标记一级回复为有帮助，稳定展示后端楼层号', async () => {
    const user = userEvent.setup();
    const onSetPostHelpful = jest.fn().mockResolvedValue(true);
    renderDetail({
      topic: { ...topic, topicType: 'help' },
      replies: [{ ...replies[0], floorNo: 3, helpful: true }],
      onSetPostHelpful,
    });

    expect(screen.getByText('3楼')).toBeInTheDocument();
    expect(screen.getByText('有帮助')).toHaveClass('text-success');
    await user.click(screen.getByRole('button', { name: '取消有帮助' }));
    expect(onSetPostHelpful).toHaveBeenCalledWith('reply-1', false);
  });

  test('求助作者可标记未标记的一级回复，已解决后仍可操作', async () => {
    const user = userEvent.setup();
    const onSetPostHelpful = jest.fn().mockResolvedValue(true);
    renderDetail({
      topic: { ...topic, topicType: 'help', status: 'closed', canClose: false },
      onSetPostHelpful,
    });

    expect(screen.getByText('已解决')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '标记有帮助' }));
    expect(onSetPostHelpful).toHaveBeenCalledWith('reply-1', true);
  });

  test('非求助作者只看到有帮助标签，不显示标记操作', () => {
    renderDetail({
      topic: { ...topic, topicType: 'help', isMine: false, canClose: false },
      replies: [{ ...replies[0], helpful: true }],
    });

    expect(screen.getByText('有帮助')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消有帮助' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '标记有帮助' })).not.toBeInTheDocument();
  });

  test('有帮助操作只属于一级回复，楼主和楼中楼不显示操作', () => {
    const child: CommunityReply = {
      ...replies[0],
      id: 'child-helpful',
      body: '楼中楼正文',
      helpful: true,
      replyToPostId: 'reply-1',
    };
    renderDetail({
      topic: { ...topic, topicType: 'help' },
      replies: [{ ...replies[0], childReplies: [child] }],
    });

    expect(screen.getAllByRole('button', { name: '标记有帮助' })).toHaveLength(1);
    expect(screen.getByText('楼中楼正文')).toBeInTheDocument();
  });

  test('求助主题使用标记已解决文案并调用结帖动作', async () => {
    const user = userEvent.setup();
    const { props } = renderDetail({ topic: { ...topic, topicType: 'help' } });

    await user.click(screen.getByRole('button', { name: '标记已解决' }));
    expect(screen.getByText('确认标记为已解决？')).toBeInTheDocument();
    expect(screen.getByText('标记后该求助将显示为已解决，本期暂不支持重新打开。')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '确认标记' }));
    expect(props.onCloseTopic).toHaveBeenCalledTimes(1);
  });

  test('加载中显示骨架占位、不渲染楼层', () => {
    renderDetail({ loading: true });
    expect(screen.queryByText('楼主正文')).not.toBeInTheDocument();
    expect(screen.queryByText('1 楼')).not.toBeInTheDocument();
  });

  test('加载失败展示错误与返回列表', () => {
    const { props } = renderDetail({ error: '网络异常', topic: null });
    expect(screen.getByText('网络异常')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回列表' }));
    expect(props.onBack).toHaveBeenCalledTimes(1);
  });

  test('回帖分页：多页时显示分页器，首页禁用上一页，下一页调用 onGoToReplyPage', () => {
    const { props } = renderDetail({ replyPage: 1, replyPageCount: 3 });
    expect(screen.getByText('1 / 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(props.onGoToReplyPage).toHaveBeenCalledWith(2);
  });

  test('回帖加载错误时显示重试并调用 onGoToReplyPage(当前页)', () => {
    const { props } = renderDetail({ replyError: '回复加载失败', replyPage: 2, replyPageCount: 2 });
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(props.onGoToReplyPage).toHaveBeenCalledWith(2);
  });

  test('仅本人开放主题展示结帖，确认后调用结帖动作', async () => {
    const user = userEvent.setup();
    const { props } = renderDetail();
    await user.click(screen.getByRole('button', { name: '结帖' }));
    await user.click(screen.getByRole('button', { name: '确认结帖' }));
    expect(props.onCloseTopic).toHaveBeenCalledTimes(1);
  });

  test('已结帖主题不展示结帖操作，显示已结帖徽标', () => {
    renderDetail({ topic: { ...topic, status: 'closed', canClose: false } });
    expect(screen.getByText('已结帖')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '结帖' })).not.toBeInTheDocument();
  });

  test('引用目标不在当前页时保持平铺并显示引用标签', () => {
    const referenced: CommunityReply = {
      ...replies[0],
      replyToPostId: 'post-0',
      replyToFloorNo: 1,
    };
    renderDetail({ replies: [referenced] });

    expect(screen.getByText('回复 1楼')).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(2);
  });

  test('楼中楼渲染在一级楼层下方，回复目标是作者且子回复不占楼层号', () => {
    const firstChild: CommunityReply = {
      ...replies[0],
      id: 'child-1',
      body: '楼中楼回复一',
      author: { ...replies[0].author, id: 'bot-child', displayName: '风控分析 Bot' },
      replyToPostId: 'floor-1',
      replyToFloorNo: 1,
    };
    const secondChild: CommunityReply = {
      ...replies[0],
      id: 'child-2',
      body: '回复楼中楼成员',
      author: { ...replies[0].author, id: 'bot-grandchild', displayName: '桌面助手' },
      replyToPostId: 'child-1',
    };
    const floor: CommunityReply = {
      ...replies[0],
      id: 'floor-1',
      body: '主楼层正文',
      author: { ...replies[0].author, id: 'human-floor', displayName: 'ClawMind' },
      childReplies: [firstChild, secondChild],
    };
    renderDetail({ replies: [floor] });

    const floorArticle = screen.getByText('主楼层正文').closest('article');
    expect(floorArticle).not.toBeNull();
    if (!floorArticle) throw new Error('floor article is missing');

    expect(within(floorArticle).getByText('楼中楼回复一')).toBeInTheDocument();
    expect(within(floorArticle).getByText('回复楼中楼成员')).toBeInTheDocument();
    expect(within(floorArticle).getByRole('button', { name: '定位ClawMind' })).toBeInTheDocument();
    expect(within(floorArticle).getByRole('button', { name: '定位风控分析 Bot' })).toBeInTheDocument();
    expect(within(floorArticle).queryByRole('button', { name: '回复第1楼' })).not.toBeInTheDocument();
    expect(within(floorArticle).queryByRole('button', { name: '回复桌面助手' })).not.toBeInTheDocument();
    expect(screen.queryByText('2楼')).not.toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(4);

    const originalScrollIntoView = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
    jest.useFakeTimers();
    fireEvent.click(within(floorArticle).getByRole('button', { name: '定位ClawMind' }));
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({
      block: 'center',
      behavior: 'smooth',
    });
    expect(floorArticle).toHaveClass('bg-primary/10');
    act(() => {
      jest.advanceTimersByTime(1200);
    });
    expect(floorArticle).not.toHaveClass('bg-primary/10');
    jest.useRealTimers();
    window.HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  });

  test('楼主、一级回复和楼中楼作者都可打开 hover 资料卡', async () => {
    const user = userEvent.setup();
    const getAuthorSummary = jest.fn();
    const loadAuthorSummary = jest.fn();
    const followAuthor = jest.fn().mockResolvedValue(true);
    const authorFollowLoading = jest.fn().mockReturnValue(false);
    const child: CommunityReply = {
      ...replies[0],
      id: 'hover-child',
      body: '楼中楼作者资料',
      author: { type: 'human', id: 'human-child', displayName: '风控分析' },
      replyToPostId: 'hover-floor',
    };
    const floor: CommunityReply = {
      ...replies[0],
      id: 'hover-floor',
      body: '一级回复作者资料',
      author: { type: 'human', id: 'human-floor', displayName: 'ClawMind' },
      childReplies: [child],
    };
    renderDetail({
      currentUserId: 'human-current',
      getAuthorSummary,
      loadAuthorSummary,
      followAuthor,
      authorFollowLoading,
      replies: [floor],
    });

    await user.click(screen.getByRole('button', { name: '查看顾客资料' }));
    await user.click(screen.getByRole('button', { name: '查看ClawMind资料' }));
    await user.click(screen.getByRole('button', { name: '查看风控分析资料' }));

    expect(loadAuthorSummary).toHaveBeenNthCalledWith(1, topic.author);
    expect(loadAuthorSummary).toHaveBeenNthCalledWith(2, floor.author);
    expect(loadAuthorSummary).toHaveBeenNthCalledWith(3, child.author);
    expect(authorFollowLoading).toHaveBeenCalled();
    expect(getAuthorSummary).toHaveBeenCalled();
  });

  test('楼中楼超过两条默认折叠前两条，可展开和收起', () => {
    const floor: CommunityReply = {
      ...replies[0],
      id: 'floor-1',
      body: '主楼层正文',
      childReplies: [1, 2, 3, 4].map((index) => ({
        ...replies[0],
        id: `child-${index}`,
        body: `楼中楼回复${index}`,
        replyToPostId: 'floor-1',
        createdAt: `2026-10-06T06:20:0${index}Z`,
      })),
    };
    renderDetail({ replies: [floor] });

    const floorArticle = screen.getByText('主楼层正文').closest('article');
    expect(floorArticle).not.toBeNull();
    if (!floorArticle) throw new Error('floor article is missing');

    const scope = within(floorArticle);
    expect(scope.getByText('楼中楼回复1')).toBeInTheDocument();
    expect(scope.getByText('楼中楼回复2')).toBeInTheDocument();
    expect(scope.queryByText('楼中楼回复3')).not.toBeInTheDocument();
    expect(scope.queryByText('楼中楼回复4')).not.toBeInTheDocument();

    const expand = screen.getByRole('button', { name: '查看全部 4 条回复 ▾' });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(expand);
    expect(screen.getByText('楼中楼回复3')).toBeInTheDocument();
    expect(screen.getByText('楼中楼回复4')).toBeInTheDocument();

    const collapse = screen.getByRole('button', { name: '收起 ▴' });
    expect(collapse).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(collapse);
    expect(screen.queryByText('楼中楼回复3')).not.toBeInTheDocument();
    expect(screen.queryByText('楼中楼回复4')).not.toBeInTheDocument();
  });

  test('翻页后楼中楼展开状态重置为默认两条', () => {
    const makeFloor = (id: string, page: number): CommunityReply => ({
      ...replies[0],
      id,
      body: `${page}页一级楼层`,
      childReplies: [1, 2, 3].map((index) => ({
        ...replies[0],
        id: `page-${page}-child-${index}`,
        body: `${page}页楼中楼${index}`,
        replyToPostId: id,
      })),
    });
    const firstRender = renderDetail({
      replyPage: 1,
      replyPageCount: 2,
      replies: [makeFloor('floor-page-1', 1)],
    });
    fireEvent.click(screen.getByRole('button', { name: '查看全部 3 条回复 ▾' }));
    expect(screen.getByText('1页楼中楼3')).toBeInTheDocument();

    firstRender.rerender(
      <CommunityTopicDetail
        {...firstRender.props}
        replyPage={2}
        replies={[makeFloor('floor-page-2', 2)]}
      />,
    );
    expect(screen.getByText('2页楼中楼1')).toBeInTheDocument();
    expect(screen.getByText('2页楼中楼2')).toBeInTheDocument();
    expect(screen.queryByText('2页楼中楼3')).not.toBeInTheDocument();
  });


  test('翻页到第 2 页不再重复展示楼主正文（楼主仅首页显示）', () => {
    renderDetail({ replyPage: 2, replyPageCount: 2, replies: [replies[0]] });
    expect(screen.queryByText('楼主正文')).not.toBeInTheDocument();
    expect(screen.queryByText('楼主')).not.toBeInTheDocument();
    expect(screen.getByText('Bot 回复')).toBeInTheDocument();
  });
});
