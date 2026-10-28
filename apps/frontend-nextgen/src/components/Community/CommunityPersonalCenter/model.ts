import type { PersonalCenterActivityType } from '@/domain/community/types';

export const personalCenterScopeOptions = [
  { value: 'ALL', label: '全部' },
  { value: 'HUMAN', label: '个人身份' },
  { value: 'BOT', label: '名下 Bot' },
] as const;

export const personalCenterActivityOptions: Array<{
  value: PersonalCenterActivityType;
  label: string;
}> = [
  { value: 'AUTHORED', label: '我的发布' },
  { value: 'FAVORITED', label: '我的收藏' },
  { value: 'VIEWED', label: '我的浏览' },
  { value: 'LIKED', label: '我的点赞' },
  { value: 'REPLIED', label: '我的回复' },
];

export const personalCenterTargetOptions = [
  { value: 'ALL', label: '全部' },
  { value: 'TOPIC', label: '主题' },
  { value: 'POST', label: '回复' },
] as const;

export function actorLabel(actorType: 'HUMAN' | 'BOT'): string {
  return actorType === 'BOT' ? 'BOT' : 'HUMAN';
}
