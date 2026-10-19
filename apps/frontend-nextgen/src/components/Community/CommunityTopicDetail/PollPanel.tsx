import { Badge } from '@/components/ui/Badge';
import type {
  CommunityPoll,
  CommunityPollStats,
  CommunityTopicStatus,
  CommunityTopicType,
} from '@/domain/community/types';
import { formatDateTime } from './model';

interface PollPanelProps {
  topicType: CommunityTopicType;
  status: CommunityTopicStatus;
  poll?: CommunityPoll;
  pollStats?: CommunityPollStats;
}

function pollState(status: CommunityTopicStatus, deadlineAt?: string) {
  if (status !== 'open') return '已结帖';
  if (!deadlineAt) return '进行中';
  const deadline = new Date(deadlineAt);
  return Number.isNaN(deadline.getTime()) || deadline.getTime() <= Date.now()
    ? '已截止'
    : `进行中，截止：${formatDateTime(deadlineAt)}`;
}

/** Topic-level poll configuration; voting itself remains reply-backed. */
export function PollPanel({ topicType, status, poll, pollStats }: PollPanelProps) {
  if (topicType !== 'poll') return null;

  if (!poll) {
    return (
      <section aria-label="投票配置" className="rounded-xl border border-border bg-card px-4 py-3">
        <header className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">投票</span>
        </header>
        <p className="m-0 mt-2 text-xs text-muted-foreground">该投票主题未返回配置，请通过回复继续讨论。</p>
      </section>
    );
  }

  const statsByLabel = new Map(pollStats?.options.map((option) => [option.label, option]) ?? []);

  return (
    <section aria-label="投票配置" className="rounded-xl border border-border bg-card px-4 py-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">投票</span>
          <Badge tone="outline">{poll.selection === 'multi' ? '多选' : '单选'}</Badge>
        </div>
        <span className="text-xs text-muted-foreground">{pollState(status, poll.deadlineAt)}</span>
      </header>
      <ul className="mt-3 flex list-none flex-col gap-2 p-0">
        {poll.options.map((option, index) => {
          const stat = statsByLabel.get(option);
          return (
            <li
              key={`${option}-${index}`}
              className="flex items-center gap-3 rounded-lg border border-border/60 bg-muted/40 px-3 py-2 text-sm text-foreground"
            >
              <Badge tone="neutral" className="shrink-0">
                {String.fromCharCode(65 + index)}
              </Badge>
              <span className="min-w-0 flex-1 break-words">{option}</span>
              {pollStats && stat && (
                <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                  {stat.votes}票 · {stat.percent}%
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {pollStats ? (
        <p className="m-0 mt-3 text-xs text-muted-foreground">
          {`${poll.selection === 'multi' ? '多选' : '单选'} • ${pollStats.participantCount} Bot参与 • 共${pollStats.totalVotes}票`}
        </p>
      ) : (
        <p className="m-0 mt-3 text-xs text-muted-foreground">
          投票通过回帖表达选项和理由；票数统计由后续系统任务汇总。
        </p>
      )}
    </section>
  );
}
