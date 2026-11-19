import type { InteractionSummary } from '@/domain/community/types';

const counts = [['关注者', 'followCount'], ['发布主题', 'topicCount'], ['回帖', 'replyCount'], ['被采纳回帖', 'acceptedReplyCount']] as const;
const number = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 });
export function profileStats(summary?: InteractionSummary) {
  return counts.map(([label, key]) => {
    const n = summary?.[key];
    return { label, count: n == null ? '—' : n >= 10000 ? `${number.format(n / 10000)} 万` : number.format(n) };
  });
}
