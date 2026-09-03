import { ExpandableCheckTable } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/ExpandableCheckTable';
import { HistoryTable } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/HistoryTable';
import { OptimizationPanel } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/OptimizationPanel';
import { StatusIcon } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/StatusIcon';
import { DIM_STATUS_STYLES } from '@/components/BotWorkshop/BotHealthCheckDrawer/constants';
import { formatDateTime } from '@/components/BotWorkshop/BotHealthCheckDrawer/utils';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { Spin } from '@/components/ui/Spin';
import type { BotHealthDimension, BotHealthHistoryItem } from '@/domain/botHealthCheck';
import { ShieldCheck } from 'lucide-react';

interface EnvironmentPanelProps {
  dimension: BotHealthDimension;
  loading?: boolean;
  checking?: boolean;
  history: BotHealthHistoryItem[];
  onViewHistoryDetail: (item: BotHealthHistoryItem) => void;
  onReDiagnose: () => void;
}

export function EnvironmentPanel({
  dimension,
  loading,
  checking,
  history,
  onViewHistoryDetail,
  onReDiagnose,
}: EnvironmentPanelProps) {
  const scanFailed = ['failed', 'error'].includes((dimension.scanStatus ?? '').toLowerCase());
  const dimStyle =
    DIM_STATUS_STYLES[scanFailed ? 'failed' : dimension.status] ?? { label: '未知', tone: 'neutral' as const };
  const hasCheckItems = (dimension.checkItems?.length ?? 0) > 0;
  const showBadge = dimension.key === 'configuration' || hasCheckItems;

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <Spin tip="正在加载体检数据..." />
      </div>
    );
  }

  return (
    <div className="space-y-5 px-6 py-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-5 text-[var(--color-primary)]" aria-hidden />
          <h4 className="m-0 text-base font-semibold text-[var(--color-fg)]">{dimension.label}体检</h4>
          {showBadge ? (
            <Badge tone={dimStyle.tone}>
              <span className="inline-flex items-center gap-1">
                <StatusIcon status={dimension.status} />
                {dimStyle.label}
              </span>
            </Badge>
          ) : null}
        </div>
        {dimension.key === 'configuration' ? (
          <Button size="sm" loading={checking} onClick={() => void onReDiagnose()}>
            重新检测
          </Button>
        ) : null}
      </div>

      <p className="text-sm text-[var(--color-muted)]">体检时间：{formatDateTime(dimension.updatedAt)}</p>

      <div className="grid gap-3 md:grid-cols-4">
        <Card className="rounded-xl">
          <CardContent className="py-5 text-center">
            <div className="text-2xl font-semibold text-[var(--color-fg)]">{dimension.checkedCount ?? 0}</div>
            <div className="mt-1 text-xs text-[var(--color-muted)]">已检项目</div>
          </CardContent>
        </Card>
        <Card className="rounded-xl">
          <CardContent className="py-5 text-center">
            <div className="text-2xl font-semibold text-[var(--color-success)]">{dimension.passedCount ?? 0}</div>
            <div className="mt-1 text-xs text-[var(--color-muted)]">通过</div>
          </CardContent>
        </Card>
        <Card className="rounded-xl">
          <CardContent className="py-5 text-center">
            <div className="text-2xl font-semibold text-[var(--color-warning)]">{dimension.warningCount ?? 0}</div>
            <div className="mt-1 text-xs text-[var(--color-muted)]">警告</div>
          </CardContent>
        </Card>
        <Card className="rounded-xl">
          <CardContent className="py-5 text-center">
            <div className="text-2xl font-semibold text-[var(--color-error)]">{dimension.errorCount ?? 0}</div>
            <div className="mt-1 text-xs text-[var(--color-muted)]">错误</div>
          </CardContent>
        </Card>
      </div>

      <ExpandableCheckTable dimension={dimension} />

      <OptimizationPanel dimension={dimension} />

      <HistoryTable history={history} onViewDetail={onViewHistoryDetail} />
    </div>
  );
}
