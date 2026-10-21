import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type {
  CommunityPoll,
  CommunityPollStats,
  CommunityTopicStatus,
  CommunityTopicType,
} from '@/domain/community/types';
import { formatDateTime } from './model';
import { useState } from 'react';

interface PollPanelProps {
  topicType: CommunityTopicType;
  status: CommunityTopicStatus;
  poll?: CommunityPoll;
  pollStats?: CommunityPollStats;
  hasVoted?: boolean;
  publishing?: boolean;
  onVote?: (optionIndices: number[]) => Promise<boolean>;
}

function pollState(status: CommunityTopicStatus, deadlineAt?: string) {
  if (status !== 'open') return '已结帖';
  if (!deadlineAt) return '进行中';
  const deadline = new Date(deadlineAt);
  return Number.isNaN(deadline.getTime()) || deadline.getTime() <= Date.now()
    ? '已截止'
    : `进行中，截止：${formatDateTime(deadlineAt)}`;
}

function isExpired(status: CommunityTopicStatus, deadlineAt?: string) {
  if (status !== 'open') return true;
  if (!deadlineAt) return false;
  const deadline = new Date(deadlineAt);
  return Number.isNaN(deadline.getTime()) || deadline.getTime() <= Date.now();
}

/** Topic-level poll configuration and the only voting entry for POLL Topics. */
export function PollPanel({
  topicType,
  status,
  poll,
  pollStats,
  hasVoted,
  publishing,
  onVote,
}: PollPanelProps) {
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [voteError, setVoteError] = useState<string | null>(null);

  if (topicType !== 'poll') return null;

  if (!poll) {
    return (
      <section aria-label="投票配置" className="rounded-xl border border-border bg-card px-4 py-3">
        <header className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">投票</span>
        </header>
        <p className="m-0 mt-2 text-xs text-muted-foreground">该投票主题未返回配置，暂不能投票。</p>
      </section>
    );
  }

  const statsByLabel = new Map(pollStats?.options.map((option) => [option.label, option]) ?? []);
  const votingDisabled = hasVoted || isExpired(status, poll.deadlineAt);

  const toggleOption = (index: number) => {
    setVoteError(null);
    if (poll?.selection === 'single') {
      setSelectedIndices([index]);
      return;
    }
    setSelectedIndices((previous) =>
      previous.includes(index)
        ? previous.filter((item) => item !== index)
        : [...previous, index].sort((left, right) => left - right),
    );
  };

  const submitVote = async () => {
    if (!selectedIndices.length) {
      setVoteError(poll?.selection === 'single' ? '请选择一个选项' : '请至少选择一个选项');
      return;
    }
    const submitted = await onVote?.(selectedIndices);
    if (submitted) {
      setSelectedIndices([]);
      setVoteError(null);
    } else if (!voteError) {
      setVoteError('投票提交失败，请稍后重试');
    }
  };

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
          const selected = selectedIndices.includes(index);
          return (
            <li key={`${option}-${index}`}>
              <Button
                variant="outline"
                className={`h-auto w-full justify-start rounded-lg px-3 py-2 text-left text-sm ${
                  selected ? 'border-primary bg-primary/10 text-primary' : 'border-border/60 bg-muted/40 text-foreground'
                }`}
                disabled={votingDisabled}
                aria-pressed={selected}
                onClick={() => toggleOption(index)}
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
              </Button>
            </li>
          );
        })}
      </ul>
      <div className="mt-3">
        {!hasVoted && !isExpired(status, poll.deadlineAt) && (
          <Button
            variant="outline"
            size="sm"
            className="mt-3 px-4 text-xs"
            loading={publishing}
            disabled={votingDisabled}
            onClick={() => void submitVote()}
          >
            提交投票
          </Button>
        )}
        {hasVoted ? (
          <p className="m-0 mt-2 text-xs text-muted-foreground">你已投过票，投票主题不允许继续回复。</p>
        ) : null}
        {voteError ? <p className="m-0 mt-2 text-xs text-destructive">{voteError}</p> : null}
      </div>
      {pollStats ? (
        <p className="m-0 mt-3 text-xs text-muted-foreground">
          {`${poll.selection === 'multi' ? '多选' : '单选'} • ${pollStats.participantCount} 位参与者 • 共${pollStats.totalVotes}票`}
        </p>
      ) : (
        <p className="m-0 mt-3 text-xs text-muted-foreground">投票结果由后台任务增量统计。</p>
      )}
    </section>
  );
}
