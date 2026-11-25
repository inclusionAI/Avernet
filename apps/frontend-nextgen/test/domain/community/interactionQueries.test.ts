import {
  buildTopicListSummaryQueries,
  forumAuthorRef,
  interactionTargetRef,
  summaryTargetRef,
} from '@/domain/community/interactionQueries';
import type { CommunityTopic } from '@/domain/community/types';

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '社区主题',
  body: '主题正文',
  status: 'open',
  topicType: 'discussion',
  author: { type: 'bot', id: 'bot-1', botUuid: 'bot-1:149844', botId: 'bot-1', displayName: '协作助手' },
  createdAt: '2026-10-01T00:00:00Z',
  isMine: false,
  canClose: false,
  canEdit: false,
  canDelete: false,
};

const viewer = { actorType: 'HUMAN' as const, id: 'human-1' };

describe('buildTopicListSummaryQueries', () => {
  it('不传 viewer 时只查询主题聚合数据', () => {
    expect(buildTopicListSummaryQueries([topic])).toEqual([{ target: { targetType: 'TOPIC', id: 'topic-1' } }]);
  });

  it('传 viewer 时同时预取作者资料和当前用户关注状态', () => {
    expect(buildTopicListSummaryQueries([topic], viewer)).toEqual([
      { target: { targetType: 'TOPIC', id: 'topic-1' } },
      { actor: { actorType: 'BOT', id: 'bot-1:149844', idAlias: 'bot-1' } },
      {
        actor: viewer,
        target: { targetType: 'BOT', id: 'bot-1:149844', idAlias: 'bot-1' },
      },
    ]);
  });

  it('通过 bot_uuid 与裸 bot_id alias 一次查询非 default Bot 互动', () => {
    const scopedAuthor = { ...topic.author, botUuid: 'bot-1:149844', botId: 'bot-1' };
    const queries = buildTopicListSummaryQueries([{ ...topic, author: scopedAuthor }], viewer);

    expect(queries).toEqual([
      { target: { targetType: 'TOPIC', id: 'topic-1' } },
      { actor: { actorType: 'BOT', id: 'bot-1:149844', idAlias: 'bot-1' } },
      {
        actor: viewer,
        target: { targetType: 'BOT', id: 'bot-1:149844', idAlias: 'bot-1' },
      },
    ]);
  });

  it('author_bot_uuid 或 botId 缺失时从其他身份兜底', () => {
    const uuidOnly = {
      type: 'bot' as const,
      id: 'bot-1',
      botUuid: 'bot-1:149844',
      displayName: '协作助手',
    };
    const scopedIdOnly = {
      type: 'bot' as const,
      id: 'bot-1:149844',
      displayName: '协作助手',
    };

    expect(forumAuthorRef(uuidOnly)).toEqual({
      actorType: 'BOT',
      id: 'bot-1:149844',
      idAlias: 'bot-1',
    });
    expect(forumAuthorRef(scopedIdOnly)).toEqual({
      actorType: 'BOT',
      id: 'bot-1:149844',
      idAlias: 'bot-1',
    });
  });

  it('历史 BOT 只有裸身份时跳过作者互动统计，不阻断列表统计', () => {
    const legacyBot = {
      type: 'bot' as const,
      id: 'bbs-smoke-bot-001',
      botId: 'bbs-smoke-bot-001',
      displayName: 'BBS smoke bot',
    };

    expect(forumAuthorRef(legacyBot)).toBeUndefined();
    expect(summaryTargetRef(legacyBot)).toBeUndefined();
    expect(buildTopicListSummaryQueries([{ ...topic, author: legacyBot }], viewer)).toEqual([
      { target: { targetType: 'TOPIC', id: 'topic-1' } },
    ]);
  });

  it('default Bot 摘要查询不回退裸 default，写入要求完整 bot_uuid', () => {
    const defaultAuthor = {
      ...topic.author,
      id: 'default:149844',
      botUuid: 'default:149844',
      botId: 'default',
    };

    expect(forumAuthorRef(defaultAuthor)).toEqual({
      actorType: 'BOT',
      id: 'default:149844',
    });
    expect(summaryTargetRef(defaultAuthor)).toEqual({
      targetType: 'BOT',
      id: 'default:149844',
    });
    expect(() =>
      interactionTargetRef({
        type: 'bot',
        id: 'default',
        botId: 'default',
        displayName: 'default',
      }),
    ).toThrow('BOT 缺少带工号后缀的 bot_uuid，无法记录互动');
  });
});
