export const personalCenterActivityOptions = [
  { value: 'AUTHORED' as const, label: '我的帖子' },
  { value: 'REPLIED' as const, label: '我的回贴' },
  { value: 'FAVORITED' as const, label: '我的收藏' },
  { value: 'LIKED' as const, label: '我的点赞' },
  { value: 'FOLLOWED' as const, label: '我的关注' },
  { value: 'VIEWED' as const, label: '浏览历史' },
];

export function actorLabel(actorType: 'HUMAN' | 'BOT'): string {
  return actorType === 'BOT' ? 'Bot' : '本人';
}
