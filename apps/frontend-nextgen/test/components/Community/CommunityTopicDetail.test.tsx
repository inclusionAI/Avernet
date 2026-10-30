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
  canEdit: true,
  canDelete: true,
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
    deleting: false,
    helpfulPostId: null,
    onBack: jest.fn(),
    onCloseTopic: jest.fn(),
    onEditTopic: jest.fn(),
    onDeleteTopic: jest.fn(),
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

  test('回复展示汇总互动量，并用红色图标表示当前用户状态', () => {
    const child: CommunityReply = {
      ...replies[0],
      id: 'child-reply',
      body: '楼中楼正文',
      author: { type: 'human', id: 'human-child', displayName: '桌面助手' },
      replyToPostId: 'floor-reply',
    };
    const floor: CommunityReply = {
      ...replies[0],
      id: 'floor-reply',
      body: '一级回复正文',
      author: { type: 'human', id: 'human-floor', displayName: 'ClawMind' },
      childReplies: [child],
    };
    const targetSummary = (postId: string, likeCount: number, favoriteCount: number) => ({
      scope: 'TARGET' as const,
      target: { targetType: 'POST' as const, id: postId },
      viewCount: 0,
      likeCount,
      favoriteCount,
      followCount: 0,
      acceptCount: 0,
      topicCount: 0,
      replyCount: 0,
      acceptedReplyCount: 0,
      replyParticipantCount: 0,
      helpfulReplyCount: 0,
    });
    const viewerSummary = (postId: string, likeCount: number, favoriteCount: number) => ({
      scope: 'ACTOR_TARGET' as const,
      actor: { actorType: 'HUMAN' as const, id: 'human-current' },
      target: { targetType: 'POST' as const, id: postId },
      viewCount: 0,
      likeCount,
      favoriteCount,
      followCount: 0,
      acceptCount: 0,
      topicCount: 0,
      replyCount: 0,
      acceptedReplyCount: 0,
      replyParticipantCount: 0,
      helpfulReplyCount: 0,
    });
    const onLikeReply = jest.fn();
    renderDetail({
      currentUserId: 'human-current',
      replies: [floor],
      getReplySummary: (postId) =>
        postId === 'child-reply'
          ? targetSummary(postId, 5, 2)
          : targetSummary(postId, 3, 4),
      getReplyViewerSummary: (postId) =>
        postId === 'child-reply'
          ? viewerSummary(postId, 0, 1)
          : viewerSummary(postId, 1, 0),
      replyLikePending: (postId) => postId === 'child-reply',
      onLikeReply,
    });

    const floorArticle = screen.getByText('一级回复正文').closest('article');
    expect(floorArticle).not.toBeNull();
    const floorLikeButton = within(floorArticle as HTMLElement).getAllByRole('button', {
      name: '点赞回复',
    })[0];
    expect(floorLikeButton).toHaveTextContent('3');
    expect(floorLikeButton).toHaveClass('text-destructive', 'hover:text-destructive');
    expect(floorLikeButton).toHaveAttribute('aria-pressed', 'true');
    expect(floorLikeButton.querySelector('svg')).toHaveClass('fill-current');

    expect(
      within(floorArticle as HTMLElement).queryByRole('button', { name: '收藏回复' }),
    ).not.toBeInTheDocument();

    const childArticle = screen.getByText('楼中楼正文').closest('article');
    expect(childArticle).not.toBeNull();
    const childLikeButton = within(childArticle as HTMLElement).getByRole('button', {
      name: '点赞回复',
    });
    expect(childLikeButton).toBeDisabled();
    expect(childLikeButton).toHaveTextContent('5');
    expect(childLikeButton).toHaveClass('text-muted-foreground');

    expect(
      within(childArticle as HTMLElement).queryByRole('button', { name: '收藏回复' }),
    ).not.toBeInTheDocument();
    expect(onLikeReply).not.toHaveBeenCalled();
  });

  test('顶栏回复入口排在结帖右侧并定位底部回复输入框', async () => {
    const originalScrollIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = jest.fn();
    try {
      const user = userEvent.setup();
      renderDetail({ currentUserId: 'human-current' });

      const header = screen.getByRole('banner');
      const closeButton = within(header).getByRole('button', { name: '结帖' });
      const replyButton = within(header).getByRole('button', { name: '回复' });
      expect(closeButton.compareDocumentPosition(replyButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

      const composer = screen.getByRole('region', { name: '回复主题' });
      const composerBody = within(composer).getByRole('textbox', { name: '回复正文' });
      const focusSpy = jest.spyOn(composerBody, 'focus');
      await user.click(replyButton);

      expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
      expect(focusSpy).toHaveBeenCalledTimes(1);
    } finally {
      Element.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  test('主帖互动操作与发表时间同排且无横向分隔线', () => {
    const onLike = jest.fn();
    const onFavorite = jest.fn();
    const onEditTopic = jest.fn();
    const baseStats = {
      viewCount: 0,
      likeCount: 0,
      favoriteCount: 0,
      followCount: 0,
      acceptCount: 0,
      topicCount: 0,
      replyCount: 0,
      acceptedReplyCount: 0,
      replyParticipantCount: 0,
      helpfulReplyCount: 0,
    };
    const targetSummary = {
      ...baseStats,
      scope: 'TARGET' as const,
      target: { targetType: 'TOPIC' as const, id: topic.id },
      viewCount: 12,
      likeCount: 10,
      favoriteCount: 6,
    };
    const viewerSummary = {
      ...baseStats,
      scope: 'ACTOR_TARGET' as const,
      actor: { actorType: 'HUMAN' as const, id: 'human-current' },
      target: { targetType: 'TOPIC' as const, id: topic.id },
      likeCount: 1,
      favoriteCount: 1,
    };
    renderDetail({
      currentUserId: 'human-current',
      targetSummary,
      viewerTopicSummary: viewerSummary,
      likePending: false,
      favoritePending: false,
      onLike,
      onFavorite,
      onEditTopic,
    });

    const interactionBar = screen.getByLabelText('主题互动');
    expect(interactionBar).toHaveClass('mt-2', 'flex', 'items-center', 'justify-between', 'gap-3');
    expect(interactionBar).not.toHaveClass('border-b', 'border-border/60');
    expect(interactionBar).not.toHaveClass('rounded-xl', 'bg-card');
    expect(within(interactionBar).getByText('发表于：时间未知')).toBeInTheDocument();

    const actionButtons = within(interactionBar).getAllByRole('button');
    expect(actionButtons.map((button) => button.textContent)).toEqual(['10', '已收藏']);
    expect(within(interactionBar).queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
    const likeButton = actionButtons[0];
    expect(likeButton).toHaveClass(
      'inline-flex',
      'items-center',
      'gap-1.5',
      'text-sm',
      'text-destructive',
      'hover:text-destructive',
    );
    expect(likeButton).toHaveTextContent('10');
    expect(likeButton.querySelector('svg')).toHaveClass('fill-current');

    const favoriteButton = within(interactionBar).getByRole('button', { name: '收藏主题' });
    expect(favoriteButton).toHaveClass('inline-flex', 'items-center', 'gap-1.5', 'text-sm', 'text-warning');
    expect(favoriteButton).toHaveTextContent('已收藏');
    expect(favoriteButton).not.toHaveTextContent('6');
    expect(favoriteButton).toHaveAttribute('aria-pressed', 'true');
    expect(favoriteButton.querySelector('svg')).toHaveClass('lucide-bookmark', 'fill-current');
    expect(within(interactionBar).getByLabelText('浏览 12 次')).toHaveTextContent('12');

    const editButton = screen.getByRole('button', { name: '编辑' });
    expect(editButton).toBeEnabled();
    fireEvent.click(editButton);
    expect(onEditTopic).toHaveBeenCalledTimes(1);
  });

  test('非发布人或已结帖主题不展示编辑入口', () => {
    const onEditTopic = jest.fn();
    renderDetail({
      topic: { ...topic, canEdit: false },
      onEditTopic,
    });

    expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
    expect(onEditTopic).not.toHaveBeenCalled();
  });

  test('开放讨论展示 Human 主题回复入口并提交直接回复', async () => {
    const user = userEvent.setup();
    const onSubmitReply = jest.fn().mockResolvedValue(true);
    renderDetail({ currentUserId: 'human-current', onSubmitReply });

    const composer = screen.getByRole('region', { name: '回复主题' });
    await user.type(within(composer).getByRole('textbox', { name: '回复正文' }), '主题回复');
    await user.click(within(composer).getByRole('button', { name: '回复主题' }));

    expect(onSubmitReply).toHaveBeenCalledTimes(1);
    expect(onSubmitReply).toHaveBeenCalledWith(expect.objectContaining({ body: '主题回复' }));
    expect(onSubmitReply.mock.calls[0][0].replyToPostId).toBeUndefined();
    expect(onSubmitReply.mock.calls[0][0].pollSelection).toBeUndefined();
  });

  test.each(['discussion', 'share'] as const)(
    '开放 %s 主题每个楼层都有回复入口，且回复按钮排在首位',
    async (topicType) => {
      const user = userEvent.setup();
      const onSubmitReply = jest.fn().mockResolvedValue(true);
      const child: CommunityReply = {
        ...replies[0],
        id: 'child-1',
        body: '楼中楼正文',
        author: { type: 'human', id: 'human-child', displayName: '桌面助手' },
        replyToPostId: 'floor-1',
      };
      const floor: CommunityReply = {
        ...replies[0],
        id: 'floor-1',
        body: '主楼层正文',
        author: { type: 'human', id: 'human-floor', displayName: 'ClawMind' },
        childReplies: [child],
      };

      renderDetail({
        topic: { ...topic, topicType },
        replies: [floor],
        currentUserId: 'human-current',
        onSubmitReply,
      });

      const floorArticle = screen.getByText('主楼层正文').closest('article');
      expect(floorArticle).not.toBeNull();
      if (!floorArticle) throw new Error('floor article is missing');

      const floorReplyButton = within(floorArticle).getAllByRole('button', { name: '回复' })[0];
      const floorActionRow = floorReplyButton.closest('div');
      expect(floorActionRow).not.toBeNull();
      if (!floorActionRow) throw new Error('floor action row is missing');
      expect(within(floorActionRow).getAllByRole('button')[0]).toBe(floorReplyButton);

      await user.click(floorReplyButton);
      const inlineComposer = within(floorArticle).getByRole('region', { name: '回复 ClawMind' });
      expect(within(inlineComposer).getByText('@ClawMind')).toBeInTheDocument();
      await user.type(within(inlineComposer).getByRole('textbox', { name: '回复 ClawMind' }), '回复主楼层');
      await user.click(within(inlineComposer).getByRole('button', { name: '回复楼层' }));
      expect(onSubmitReply).toHaveBeenLastCalledWith({ body: '回复主楼层', replyToPostId: 'floor-1' });

      const childArticle = screen.getByText('楼中楼正文').closest('article');
      expect(childArticle).not.toBeNull();
      if (!childArticle) throw new Error('child article is missing');
      const childReplyButton = within(childArticle).getByRole('button', { name: '回复' });
      const childActionRow = childReplyButton.closest('div');
      expect(childActionRow).not.toBeNull();
      if (!childActionRow) throw new Error('child action row is missing');
      expect(within(childActionRow).getAllByRole('button')[0]).toBe(childReplyButton);

      await user.click(childReplyButton);
      const childComposer = within(floorArticle).getByRole('region', { name: '回复 桌面助手' });
      expect(within(childComposer).getByText('@桌面助手')).toBeInTheDocument();
      await user.type(within(childComposer).getByRole('textbox', { name: '回复 桌面助手' }), '回复楼中楼');
      await user.click(within(childComposer).getByRole('button', { name: '回复楼层' }));
      expect(onSubmitReply).toHaveBeenLastCalledWith({ body: '回复楼中楼', replyToPostId: 'child-1' });
    },
  );

  test('投票主题支持 Human 主题投票，一级楼层不提供楼中楼回复', async () => {
    const user = userEvent.setup();
    const onSubmitReply = jest.fn().mockResolvedValue(true);
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
      currentUserId: 'human-current',
      onSubmitReply,
    });

    expect(screen.getByRole('region', { name: '投票配置' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '投票主题' })).toBeInTheDocument();
    expect(screen.getByText('单选 • 3 位参与者 • 共3票')).toBeInTheDocument();

    const floorArticle = screen.getByText('Bot 回复').closest('article');
    expect(floorArticle).not.toBeNull();
    if (!floorArticle) throw new Error('floor article is missing');
    expect(within(floorArticle).queryByRole('button', { name: '回复' })).not.toBeInTheDocument();

    const composer = screen.getByRole('region', { name: '回复主题' });
    const optionA = within(composer).getByRole('button', { name: '方案 A' });
    await user.click(optionA);
    expect(optionA).toHaveAttribute('aria-pressed', 'true');
    await user.type(within(composer).getByRole('textbox', { name: '回复正文' }), '我选方案 A');
    await user.click(within(composer).getByRole('button', { name: '回复主题' }));

    expect(onSubmitReply).toHaveBeenCalledTimes(1);
    expect(onSubmitReply).toHaveBeenCalledWith(expect.objectContaining({ body: '我选方案 A', pollSelection: [0] }));
    expect(onSubmitReply.mock.calls[0][0].replyToPostId).toBeUndefined();
  });

  test('求助主题只支持 Human 主题回复，一级楼层不提供楼中楼回复', async () => {
    const user = userEvent.setup();
    const onSubmitReply = jest.fn().mockResolvedValue(true);
    renderDetail({
      topic: { ...topic, topicType: 'help' },
      currentUserId: 'human-current',
      onSubmitReply,
    });

    const floorArticle = screen.getByText('Bot 回复').closest('article');
    expect(floorArticle).not.toBeNull();
    if (!floorArticle) throw new Error('floor article is missing');
    expect(within(floorArticle).queryByRole('button', { name: '回复' })).not.toBeInTheDocument();

    const composer = screen.getByRole('region', { name: '回复主题' });
    await user.type(within(composer).getByRole('textbox', { name: '回复正文' }), '求助补充');
    await user.click(within(composer).getByRole('button', { name: '回复主题' }));
    expect(onSubmitReply).toHaveBeenCalledWith(expect.objectContaining({ body: '求助补充' }));
    expect(onSubmitReply.mock.calls[0][0].replyToPostId).toBeUndefined();
  });

  test('分享主题详情页不再重复展示分享类型图标', () => {
    renderDetail({ topic: { ...topic, topicType: 'share' } });

    expect(screen.queryByText('分享')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('分享')).not.toBeInTheDocument();
  });

  test('求助主题不再展示期望解决时间，关闭后显示已解决', () => {
    renderDetail({
      topic: {
        ...topic,
        status: 'closed',
        canClose: false,
        canEdit: false,
        canDelete: false,
        topicType: 'help',
      },
    });

    expect(screen.queryByText('求助')).not.toBeInTheDocument();
    expect(screen.getByText('已解决')).toHaveClass('text-success');
    expect(screen.queryByRole('region', { name: '求助设置' })).not.toBeInTheDocument();
    expect(screen.queryByText('期望解决时间')).not.toBeInTheDocument();
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
      <CommunityTopicDetail {...firstRender.props} replyPage={2} replies={[makeFloor('floor-page-2', 2)]} />,
    );
    expect(screen.getByText('2页楼中楼1')).toBeInTheDocument();
    expect(screen.getByText('2页楼中楼2')).toBeInTheDocument();
    expect(screen.queryByText('2页楼中楼3')).not.toBeInTheDocument();
  });

  test('主帖作者已关注时作者卡片展示已关注', async () => {
    const user = userEvent.setup();
    const baseStats = {
      viewCount: 0,
      likeCount: 0,
      favoriteCount: 0,
      followCount: 1,
      acceptCount: 0,
      topicCount: 0,
      replyCount: 0,
      acceptedReplyCount: 0,
      replyParticipantCount: 0,
      helpfulReplyCount: 0,
    };
    renderDetail({
      currentUserId: 'human-current',
      authorFollowed: () => true,
      getAuthorSummary: () => ({
        ...baseStats,
        scope: 'ACTOR' as const,
        actor: { actorType: 'HUMAN' as const, id: 'human-1' },
      }),
      loadAuthorSummary: () => undefined,
    });

    await user.hover(screen.getByRole('button', { name: '查看顾客资料' }));
    expect(await screen.findByRole('button', { name: '已关注' })).toBeInTheDocument();
  });

  test('翻页到第 2 页不再重复展示楼主正文（楼主仅首页显示）', () => {
    renderDetail({ replyPage: 2, replyPageCount: 2, replies: [replies[0]] });
    expect(screen.queryByText('楼主正文')).not.toBeInTheDocument();
    expect(screen.queryByText('楼主')).not.toBeInTheDocument();
    expect(screen.getByText('Bot 回复')).toBeInTheDocument();
  });
});
