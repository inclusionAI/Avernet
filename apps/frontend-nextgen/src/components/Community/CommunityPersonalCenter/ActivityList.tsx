import { Badge } from '@/components/ui/Badge';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { PersonalCenterActivity } from '@/domain/community/types';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { formatDateTime } from '../CommunityTopicDetail/model';
import { actorLabel } from './model';

export interface ActivityListProps {
  items: PersonalCenterActivity[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenTopic: (topicId: string) => void;
}

export function ActivityList({ items, loading, error, onRetry, onOpenTopic }: ActivityListProps) {
  if (loading) {
    return (
      <div className="overflow-hidden rounded-xl border border-border bg-card" aria-label="个人中心活动加载中">
        {[1, 2, 3].map((item) => (
          <Skeleton.ListItem key={item} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-border bg-card">
        <Empty
          title="活动加载失败"
          description={error}
          action={
            <Button variant="outline" leftIcon={<RefreshCw className="size-4" />} onClick={onRetry}>
              重试
            </Button>
          }
        />
      </div>
    );
  }
  if (!items.length) {
    return (
      <div className="rounded-xl border border-border bg-card">
        <Empty title="暂无活动" description="当前身份还没有对应的社区活动。" />
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      {items.map((item) => (
        <Button
          key={`${item.actor.actorType}:${item.actor.id}:${item.activityType}:${item.target.targetType}:${item.target.id}`}
          variant="ghost"
          size="md"
          className="grid w-full grid-cols-[80px_1fr_160px_170px] items-center gap-3 rounded-none border-b border-border/60 px-4 py-3 text-left transition-colors last:border-b-0 hover:bg-muted"
          onClick={() => onOpenTopic(item.topicId)}
        >
          <Badge tone={item.actor.actorType === 'BOT' ? 'primary' : 'neutral'}>
            {actorLabel(item.actor.actorType)}
          </Badge>
          <span className="min-w-0">
            <span className="block truncate text-sm text-foreground">{item.bodyPreview || '社区内容'}</span>
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">{item.actor.id}</span>
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {item.activityType} · {item.target.targetType}
          </span>
          <span className="text-right text-xs text-muted-foreground">{formatDateTime(item.happenedAt)}</span>
        </Button>
      ))}
    </div>
  );
}
