import { buildTopicListSummaryQueries } from '@/domain/community/interactionQueries';
import type { CommunityTopic } from '@/domain/community/types';

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '社区主题',
  body: '主题正文',
  status: 'open',
  topicType: 'discussion',
  author: { type: 'bot', id: 'bot-1', displayName: '协作助手' },
  createdAt: '2026-10-01T00:00:00Z',
  isMine: false,
  canClose: false,
  canEdit: false,
  canDelete: false,
};

const viewer = { actorType: 'HUMAN' as const, id: 'human-1' };

describe('buildTopicListSummaryQueries', () => {
  it('不传 viewer 时只查询主题聚合数据', () => {
    expect(buildTopicListSummaryQueries([topic])).toEqual([
      { target: { targetType: 'TOPIC', id: 'topic-1' } },
    ]);
  });

  it('传 viewer 时同时预取作者资料和当前用户关注状态', () => {
    expect(buildTopicListSummaryQueries([topic], viewer)).toEqual([
      { target: { targetType: 'TOPIC', id: 'topic-1' } },
      { actor: { actorType: 'BOT', id: 'bot-1' } },
      { actor: viewer, target: { targetType: 'BOT', id: 'bot-1' } },
    ]);
  });
});
