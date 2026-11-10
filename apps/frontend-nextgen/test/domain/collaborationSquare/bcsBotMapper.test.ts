/** @jest-environment jsdom */
// BCS 候选 DTO → PublicBot 安全映射。Task 3 的旧分享链接守卫将复用这里的
// bcsStatus=online|hidden 映射结果（不透传 Backend Catalog 生命周期 status、不伪造 ACTIVE）。
import type { BcsCandidateItem } from '@/domain/collaborationSquare/bcsBotMapper';
import { mapBcsBotCandidate } from '@/domain/collaborationSquare/bcsBotMapper';
import { getPublicBotTargetId } from '@/domain/collaborationSquare/types';

const CANDIDATE: BcsCandidateItem = {
  bot: {
    bot_id: 'assistant:owner-8',
    name: '规划助手',
    avatar_url: null,
    created_by: '8',
    descriptor: { summary: '擅长任务规划与分解' },
    visibility: 'protected',
    user_visibility: 'public',
    status: 'online',
  },
  is_friend: false,
};

describe('mapBcsBotCandidate', () => {
  it('把 BCS bot.bot_id 作为唯一申请目标映射（getPublicBotTargetId 命中同一身份）', () => {
    const bot = mapBcsBotCandidate(CANDIDATE, '7');

    expect(bot).not.toBeNull();
    if (!bot) return;
    expect(bot.id).toBe('assistant:owner-8');
    expect(bot.friendRequestBotId).toBeUndefined();
    expect(getPublicBotTargetId(bot)).toBe('assistant:owner-8');
    expect(bot.name).toBe('规划助手');
    expect(bot.description).toBe('擅长任务规划与分解');
    expect(bot.relationshipStatus).toBe('none');
  });

  it('is_friend=true 映射为已建立好友关系', () => {
    expect(mapBcsBotCandidate({ ...CANDIDATE, is_friend: true }, '7')?.relationshipStatus).toBe('friend');
  });

  it('头像缺失回退既有 dicebear 约定，提供 avatar_url 时原样使用', () => {
    expect(mapBcsBotCandidate(CANDIDATE, '7')?.avatarUrl).toMatch(/^data:image\//);
    const withAvatar = mapBcsBotCandidate(
      { ...CANDIDATE, bot: { ...CANDIDATE.bot, avatar_url: 'https://cdn.example/avatar.png' } },
      '7',
    );
    expect(withAvatar?.avatarUrl).toBe('https://cdn.example/avatar.png');
  });

  it('名称/摘要缺失时正常回退（未命名 Bot / 空摘要），不伪造内容', () => {
    const bot = mapBcsBotCandidate(
      { ...CANDIDATE, bot: { ...CANDIDATE.bot, name: '   ', descriptor: { summary: '' } } },
      '7',
    );
    expect(bot?.name).toBe('未命名 Bot');
    expect(bot?.description).toBe('');
  });

  it('Owner 展示名缺失时明确降级为「未公开」（created_by 是 staff_no，不作展示名）', () => {
    expect(mapBcsBotCandidate(CANDIDATE, '7')?.ownerName).toBe('未公开');
  });

  it('status=online｜hidden 必须映射为独立 bcsStatus，不得透传为 Backend 生命周期 status', () => {
    const online = mapBcsBotCandidate(CANDIDATE, '7');
    const hidden = mapBcsBotCandidate({ ...CANDIDATE, bot: { ...CANDIDATE.bot, status: 'hidden' } }, '7');

    expect(online?.bcsStatus).toBe('online');
    expect(hidden?.bcsStatus).toBe('hidden');
    expect(online?.status).toBeUndefined();
    expect(hidden?.status).toBeUndefined();
  });

  it('semantic 命中的 score/short_profile 原样映射，short_profile 去除首尾空白', () => {
    const bot = mapBcsBotCandidate({ ...CANDIDATE, score: 0.92, short_profile: '  擅长语义推荐的短画像  ' }, '7');

    expect(bot?.score).toBe(0.92);
    expect(bot?.shortProfile).toBe('擅长语义推荐的短画像');
  });

  it('name_fallback/未评分候选不携带 score/short_profile 字段', () => {
    const bot = mapBcsBotCandidate(CANDIDATE, '7');

    expect(bot?.score).toBeUndefined();
    expect(bot?.shortProfile).toBeUndefined();
  });

  it('short_profile 为纯空白时按缺失处理，不透出空白短画像', () => {
    const bot = mapBcsBotCandidate({ ...CANDIDATE, short_profile: '   ' }, '7');

    expect(bot?.shortProfile).toBeUndefined();
  });

  it('score 与 short_profile 同时存在时不改写其余映射（id/bcsStatus 原样保留）', () => {
    const bot = mapBcsBotCandidate({ ...CANDIDATE, score: 0.75, short_profile: '推荐语' }, '7');

    expect(bot?.id).toBe('assistant:owner-8');
    expect(bot?.bcsStatus).toBe('online');
    expect(bot?.name).toBe('规划助手');
    expect(bot?.description).toBe('擅长任务规划与分解');
    expect(bot?.score).toBe(0.75);
    expect(bot?.shortProfile).toBe('推荐语');
  });

  it('不透出 descriptor 未审核技能与 friend_ext（capabilities 恒为空，序列化不含内部字段）', () => {
    const richCandidate = {
      bot: {
        ...CANDIDATE.bot,
        descriptor: { summary: '摘要', skills: [{ name: '未审核技能' }] },
        friend_ext: { department_scope: 'GTM' },
      },
      is_friend: false,
    } as unknown as BcsCandidateItem;

    const bot = mapBcsBotCandidate(richCandidate, '7');

    expect(bot?.capabilities).toEqual([]);
    const serialized = JSON.stringify(bot);
    expect(serialized).not.toContain('未审核技能');
    expect(serialized).not.toContain('friend_ext');
  });

  it('created_by 与登录 userId 一致才标记本人管理', () => {
    expect(mapBcsBotCandidate(CANDIDATE, '8')?.isOwnedByLoggedInUser).toBe(true);
    expect(mapBcsBotCandidate(CANDIDATE, '7')?.isOwnedByLoggedInUser).toBeUndefined();
    expect(mapBcsBotCandidate(CANDIDATE)?.isOwnedByLoggedInUser).toBeUndefined();
  });

  it('受众类型与 Catalog mapper 同口径：仅单一维度公开时派生', () => {
    // user_visibility=public 且 visibility 非 public → audience=user
    expect(mapBcsBotCandidate(CANDIDATE, '7')?.audience).toBe('user');
    // 两类均公开：不携带 audience
    expect(
      mapBcsBotCandidate(
        { ...CANDIDATE, bot: { ...CANDIDATE.bot, visibility: 'public', user_visibility: 'public' } },
        '7',
      )?.audience,
    ).toBeUndefined();
    // visibility=public 且 user_visibility 非 public → audience=bot
    expect(
      mapBcsBotCandidate(
        { ...CANDIDATE, bot: { ...CANDIDATE.bot, visibility: 'public', user_visibility: 'private' } },
        '7',
      )?.audience,
    ).toBe('bot');
  });

  it.each([
    ['缺 bot 项', { is_friend: false }],
    ['bot 为 null', { bot: null, is_friend: false }],
    ['bot_id 缺失', { bot: { ...CANDIDATE.bot, bot_id: undefined }, is_friend: false }],
    ['bot_id 为空白', { bot: { ...CANDIDATE.bot, bot_id: '  ' }, is_friend: false }],
    ['候选项为 null', null],
  ] as const)('畸形候选项返回 null 而不是映射为伪造身份：%s', (_label, candidate) => {
    expect(mapBcsBotCandidate(candidate as unknown as BcsCandidateItem, '7')).toBeNull();
  });
});
