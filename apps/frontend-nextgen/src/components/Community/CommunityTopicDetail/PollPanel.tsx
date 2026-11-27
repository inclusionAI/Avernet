import './PollPanel.css';
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
  viewer?: { hasVoted: boolean; pollSelection?: number[] };
  onPick?: (index: number) => void;
}

function pollState(status: CommunityTopicStatus, deadlineAt?: string) {
  if (status !== 'open') return '已结帖';
  if (!deadlineAt) return '进行中';
  const deadline = new Date(deadlineAt);
  return Number.isNaN(deadline.getTime()) || deadline.getTime() <= Date.now()
    ? '已截止'
    : `进行中，截止：${formatDateTime(deadlineAt)}`;
}

export function PollPanel({ topicType, status, poll, pollStats, viewer, onPick }: PollPanelProps) {
  if (topicType !== 'poll') return null;
  const state = pollState(status, poll?.deadlineAt);
  const ended = state === '已截止' || state === '已结帖';
  const results = ended || viewer?.hasVoted === true;
  return (
    <section aria-label="投票配置" className="community-poll mt-3">
      <header className="flex items-center gap-2">
        <strong className="text-sm">投票选项</strong>
        {poll && <Badge tone="neutral">{poll.selection === 'multi' ? '多选' : '单选'}</Badge>}
        {state !== '进行中' && <span className="ml-auto text-xs text-muted-foreground">{state}</span>}
      </header>
      {!poll ? (
        <p>该投票主题未返回配置，暂无法展示选项。</p>
      ) : (
        <>
          <ul className="m-0 grid list-none gap-2 px-0 pt-3">
            {poll.options.map((label, index) => {
              const selected = viewer?.hasVoted && viewer.pollSelection?.includes(index);
              const stat = pollStats?.options.find((option) => option.label === label);
              const Row = results || !onPick ? 'div' : 'button';
              return (
                <li key={index}>
                  <Row
                    onClick={Row === 'button' ? () => onPick?.(index) : undefined}
                    className="community-poll-row text-sm"
                    data-state={ended ? 'closed' : selected ? 'selected' : results ? 'voted' : 'initial'}
                  >
                    {results && stat && (
                      <span
                        aria-hidden
                        className="community-poll-fill"
                        style={{ width: `${Math.max(0, Math.min(100, stat.percent))}%` }}
                      />
                    )}
                    <span className="community-poll-letter relative inline-flex h-5 shrink-0 items-center rounded-full px-2 text-xs">
                      {String.fromCharCode(65 + index)}
                    </span>
                    <span className="relative min-w-0 flex-1 break-words">{label}</span>
                    {results && (
                      <span className="community-poll-result relative shrink-0 text-xs text-muted-foreground">
                        {stat ? `${stat.votes}票 · ${stat.percent}%` : '—'}
                      </span>
                    )}
                  </Row>
                </li>
              );
            })}
          </ul>
          <p className="mb-0 mt-3 text-xs text-muted-foreground">
            {pollStats ? `${pollStats.participantCount} 位参与者` : '参与人数待统计'}
          </p>
        </>
      )}
    </section>
  );
}
