/**
 * 公开设置「好友名单」台账（Bot 好友名单 PRD v1.3 §3.1）纯领域逻辑：
 * 只做 roster 的分计 / 分 Tab / 搜索过滤 / 默认 Tab 选择 / DTO → 视图映射，不含任何 I/O。
 * 数据来源为已建好友连接（Human 好友用户 / Bot 好友 Bot），由 Hook 经
 * collaborationFriendConnectionController.listFriendConnections 拉取后传入。
 */

/** 好友类型：与协作好友连接的 actor 类型对齐（Human 用户 / Bot）。 */
export type RosterActorType = 'human' | 'bot';

/** 名单项：仅承载展示所需的头像名称 + 类型；PRD R4 不展示在线/加入时间等。 */
export interface RosterFriend {
  type: RosterActorType;
  id: string;
  displayName: string;
  /** 预留：当前 PRD R4 不展示在线状态，保留字段以免 DTO 丢信息。 */
  isOnline?: boolean;
}

/** 名单分计：好友用户 / 好友 Bot 分计（PRD R2）。 */
export interface RosterCounts {
  human: number;
  bot: number;
  total: number;
}

/** 后端已建好友连接项的最小视图（只取展示需要的 actor + 可选展示名）。 */
export interface RosterSeed {
  id: string;
  name?: string;
  isOnline?: boolean;
}

/** 分计（好友用户 / 好友 Bot）。 */
export function countRoster(friends: RosterFriend[]): RosterCounts {
  let human = 0;
  let bot = 0;
  for (const friend of friends) {
    if (friend.type === 'human') human += 1;
    else bot += 1;
  }
  return { human, bot, total: human + bot };
}

/**
 * 默认选中 Tab：好友用户优先；好友用户为空且好友 Bot 有内容则选好友 Bot；
 * 两类全空默认好友用户（PRD R2「默认选中首个有内容的 Tab（好友用户优先）」）。
 */
export function pickDefaultRosterTab(counts: RosterCounts): RosterActorType {
  if (counts.human > 0) return 'human';
  if (counts.bot > 0) return 'bot';
  return 'human';
}

/** 按 Tab 取对应类型的好友。 */
export function partitionRoster(friends: RosterFriend[], tab: RosterActorType): RosterFriend[] {
  return friends.filter((friend) => friend.type === tab);
}

/** 在当前 Tab 内按名称/ID 搜索（大小写不敏感，空白关键词不过滤）。 */
export function filterRosterByKeyword(friends: RosterFriend[], keyword: string): RosterFriend[] {
  const kw = keyword.trim().toLowerCase();
  if (!kw) return friends;
  return friends.filter(
    (friend) => friend.displayName.toLowerCase().includes(kw) || friend.id.toLowerCase().includes(kw),
  );
}

/**
 * DTO → 名单项映射：按类型去重（type+id），展示名缺失回退 ID，
 * 空 id 直接丢弃（防御后端异常返回，不把脏数据渲染成空行）。
 */
export function buildRosterFriends(input: { humans: RosterSeed[]; bots: RosterSeed[] }): RosterFriend[] {
  const seen = new Set<string>();
  const friends: RosterFriend[] = [];
  const push = (seed: RosterSeed, type: RosterActorType) => {
    const id = typeof seed?.id === 'string' ? seed.id.trim() : '';
    if (!id) return;
    const key = `${type}:${id}`;
    if (seen.has(key)) return;
    seen.add(key);
    friends.push({
      type,
      id,
      displayName: seed.name?.trim() || id,
      isOnline: seed.isOnline,
    });
  };
  input.humans.forEach((seed) => push(seed, 'human'));
  input.bots.forEach((seed) => push(seed, 'bot'));
  return friends;
}