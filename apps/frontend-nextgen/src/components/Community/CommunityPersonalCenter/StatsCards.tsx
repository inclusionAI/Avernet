import { Skeleton } from '@/components/ui/Skeleton';
import type { PersonalCenterIdentityStats } from '@/hooks/usePersonalCenter';

export interface StatsCardsProps {
  stats: {
    topicCount: number;
    replyCount: number;
    acceptedReplyCount: number;
    followCount: number;
  };
  identities: PersonalCenterIdentityStats[];
  loading: boolean;
}

export function StatsCards({ stats, identities, loading }: StatsCardsProps) {
  if (loading) return <Skeleton.Card aria-label="个人中心统计加载中" />;
  const cards = [
    { label: '关注者', value: stats.followCount },
    { label: '主题', value: stats.topicCount },
    { label: '回复', value: stats.replyCount },
    { label: '被采纳回复', value: stats.acceptedReplyCount },
  ];
  return (
    <section aria-label="个人中心统计" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {cards.map((item) => (
        <div key={item.label} className="rounded-xl border border-border bg-card p-4">
          <p className="m-0 text-xs text-muted-foreground">{item.label}</p>
          <p className="m-0 mt-1 text-2xl font-semibold tabular-nums text-foreground">{item.value}</p>
        </div>
      ))}
      {identities.length > 1 ? (
        <div className="rounded-xl border border-border bg-card p-4 sm:col-span-2 xl:col-span-4">
          <p className="m-0 text-xs font-medium text-muted-foreground">身份明细</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {identities.map((identity) => (
              <span
                key={`${identity.actor.actorType}:${identity.actor.id}`}
                className="rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground"
              >
                {identity.actor.actorType} · {identity.actor.id} · {identity.followCount} 关注者 / {identity.topicCount} 主题 / {identity.replyCount} 回复
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
