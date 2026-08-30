import { Badge } from '@/components/ui/Badge';
import { Bot, HeartPulse } from 'lucide-react';

interface OverviewCardProps {
  botName?: string;
  healthScore?: number | null;
  overallStatus?: string;
  latestAt?: string | null;
}

function healthBadgeLabel(score: number | null | undefined): string {
  if (score === null || score === undefined) return '暂无评分';
  if (score >= 90) return '优秀';
  if (score >= 70) return '良好';
  if (score >= 50) return '警告';
  return '严重';
}

function healthBadgeTone(score: number | null | undefined): 'success' | 'warning' | 'error' | 'neutral' {
  if (score === null || score === undefined) return 'neutral';
  if (score >= 70) return 'success';
  if (score >= 50) return 'warning';
  return 'error';
}

export function OverviewCard({ botName, healthScore, overallStatus, latestAt }: OverviewCardProps) {
  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-muted)]">
        <Bot className="size-4" aria-hidden />
        Bot 基本信息
      </div>
      <div className="flex flex-1 flex-col justify-center gap-4">
        <div className="flex items-center gap-4">
          <div className="flex size-14 items-center justify-center rounded-full bg-[var(--color-primary-soft)] text-[var(--color-primary)]">
            <Bot className="size-7" aria-hidden />
          </div>
          <div className="min-w-2 flex-1">
            <div className="text-lg font-semibold text-[var(--color-fg)]">{botName ?? '未知 Bot'}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Badge tone={healthBadgeTone(healthScore)}>
                <span className="inline-flex items-center gap-1">
                  <HeartPulse className="size-3.5" aria-hidden />
                  健康分 {healthScore ?? '-'} {healthBadgeLabel(healthScore)}
                </span>
              </Badge>
              {overallStatus ? <Badge tone="neutral">状态: {overallStatus}</Badge> : null}
            </div>
          </div>
        </div>
        <div className="text-sm text-[var(--color-muted)]">最近体检：{latestAt ?? '无'}</div>
      </div>
    </div>
  );
}
