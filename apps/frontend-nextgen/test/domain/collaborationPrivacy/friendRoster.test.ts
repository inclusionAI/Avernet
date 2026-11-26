import {
  buildRosterFriends,
  countRoster,
  filterRosterByKeyword,
  partitionRoster,
  pickDefaultRosterTab,
  type RosterFriend,
} from '@/domain/collaborationPrivacy/friendRoster';

const friend = (type: 'human' | 'bot', id: string, displayName = id): RosterFriend => ({
  type,
  id,
  displayName,
});

describe('friendRoster domain', () => {
  describe('countRoster', () => {
    it('好友用户 / 好友 Bot 分计并给总数', () => {
      expect(countRoster([friend('human', 'human_a'), friend('human', 'human_b'), friend('bot', 'bot_x')])).toEqual({
        human: 2,
        bot: 1,
        total: 3,
      });
    });

    it('空名单全零', () => {
      expect(countRoster([])).toEqual({ human: 0, bot: 0, total: 0 });
    });
  });

  describe('pickDefaultRosterTab', () => {
    it('好友用户优先', () => {
      expect(pickDefaultRosterTab({ human: 1, bot: 1, total: 2 })).toBe('human');
    });

    it('好友用户为空、Bot 有内容时选 Bot', () => {
      expect(pickDefaultRosterTab({ human: 0, bot: 2, total: 2 })).toBe('bot');
    });

    it('两类全空默认好友用户', () => {
      expect(pickDefaultRosterTab({ human: 0, bot: 0, total: 0 })).toBe('human');
    });
  });

  describe('partitionRoster', () => {
    const list = [friend('human', 'human_a'), friend('bot', 'bot_x'), friend('human', 'human_b')];

    it('按 Tab 取对应类型', () => {
      expect(partitionRoster(list, 'human').map((item) => item.id)).toEqual(['human_a', 'human_b']);
      expect(partitionRoster(list, 'bot').map((item) => item.id)).toEqual(['bot_x']);
    });
  });

  describe('filterRosterByKeyword', () => {
    const list = [friend('human', 'human_a', '仲谋'), friend('human', 'human_b', '大乔'), friend('bot', 'bot_x', '灵犀助手')];

    it('按展示名大小写不敏感匹配', () => {
      expect(filterRosterByKeyword(list, '灵犀').map((item) => item.id)).toEqual(['bot_x']);
    });

    it('按 ID 匹配', () => {
      expect(filterRosterByKeyword(list, 'human_b').map((item) => item.displayName)).toEqual(['大乔']);
    });

    it('空白关键词不过滤', () => {
      expect(filterRosterByKeyword(list, '   ')).toHaveLength(3);
    });

    it('无匹配返回空', () => {
      expect(filterRosterByKeyword(list, '不存在')).toHaveLength(0);
    });
  });

  describe('buildRosterFriends', () => {
    it('按类型映射并回退名称为 ID', () => {
      const result = buildRosterFriends({
        humans: [{ id: 'human_a', name: '仲谋' }, { id: 'human_b' }],
        bots: [{ id: 'bot_x', name: '灵犀助手' }],
      });
      expect(result).toEqual([
        { type: 'human', id: 'human_a', displayName: '仲谋', isOnline: undefined },
        { type: 'human', id: 'human_b', displayName: 'human_b', isOnline: undefined },
        { type: 'bot', id: 'bot_x', displayName: '灵犀助手', isOnline: undefined },
      ]);
    });

    it('type+id 去重且丢空 id', () => {
      const result = buildRosterFriends({
        humans: [{ id: 'human_a' }, { id: 'human_a' }, { id: '   ' }],
        bots: [{ id: 'bot_x' }, { id: 'bot_x' }],
      });
      expect(result.map((item) => `${item.type}:${item.id}`)).toEqual(['human:human_a', 'bot:bot_x']);
    });
  });
});