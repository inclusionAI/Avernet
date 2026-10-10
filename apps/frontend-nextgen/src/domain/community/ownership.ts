import type { CommunityTopic } from './types';

/**
 * 用当前登录工号推导主题所有权：isMine = 主题作者=本人工号；canClose = 本人且开放。
 * openapi §4.1 不返 is_mine/can_close，前端按 author_id 与登录态对比即可（产品确认的伪命题原则）。
 */
export function enrichOwnership(topic: CommunityTopic, currentUserId: string): CommunityTopic {
  const isMine = currentUserId.trim().length > 0 && topic.author.id === currentUserId.trim();
  const canClose = isMine && topic.status === 'open';
  return { ...topic, isMine, canClose };
}
