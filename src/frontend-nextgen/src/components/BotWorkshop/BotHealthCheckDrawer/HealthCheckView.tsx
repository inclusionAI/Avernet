import { CheckDetailDrawer } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/CheckDetailDrawer';
import { DimensionTabs } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/DimensionTabs';
import { EnvironmentPanel } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/EnvironmentPanel';
import { OverviewCard } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/OverviewCard';
import { RadarChart } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/RadarChart';
import { formatDateTime } from '@/components/BotWorkshop/BotHealthCheckDrawer/utils';
import { Card } from '@/components/ui/Card';
import type { BotHealthCheckSummary, BotHealthDimensionKey, BotHealthHistoryItem } from '@/domain/botHealthCheck';
import { useMemo, useState } from 'react';

interface HealthCheckViewProps {
  summary: BotHealthCheckSummary;
  botName?: string;
  loading?: boolean;
  checking?: boolean;
  onReDiagnose: () => void;
}

export function HealthCheckView({ summary, botName, loading, checking, onReDiagnose }: HealthCheckViewProps) {
  const firstDimension = summary.dimensions[0]?.key ?? 'configuration';
  const [activeDimensionKey, setActiveDimensionKey] = useState<BotHealthDimensionKey>(firstDimension);
  const [selectedHistory, setSelectedHistory] = useState<BotHealthHistoryItem | null>(null);

  const currentDimension = useMemo(
    () => summary.dimensions.find((dim) => dim.key === activeDimensionKey) ?? summary.dimensions[0],
    [activeDimensionKey, summary.dimensions],
  );

  const dimensionHistory = useMemo(
    () => summary.history.filter((item) => item.key === activeDimensionKey),
    [activeDimensionKey, summary.history],
  );

  const overallStatusText = useMemo(() => {
    switch (summary.overallStatus) {
      case 'healthy':
        return '健康';
      case 'warning':
        return '需关注';
      case 'critical':
        return '异常';
      case 'scanning':
        return '检测中';
      default:
        return '暂无结果';
    }
  }, [summary.overallStatus]);

  return (
    <div className="space-y-5">
      <Card className="grid rounded-xl lg:grid-cols-[1fr_320px]">
        <OverviewCard
          botName={botName}
          healthScore={summary.healthScore}
          overallStatus={overallStatusText}
          latestAt={formatDateTime(summary.latestAt)}
        />
        <div className="flex items-center justify-center border-t border-[var(--color-border)] p-5 lg:border-t-0 lg:border-l">
          <RadarChart dimensions={summary.dimensions} />
        </div>
      </Card>

      <Card className="rounded-xl">
        <DimensionTabs activeKey={activeDimensionKey} onChange={setActiveDimensionKey} />
        {currentDimension ? (
          <EnvironmentPanel
            dimension={currentDimension}
            loading={loading}
            checking={checking}
            history={dimensionHistory}
            onViewHistoryDetail={setSelectedHistory}
            onReDiagnose={onReDiagnose}
          />
        ) : null}
      </Card>

      <CheckDetailDrawer item={selectedHistory} onOpenChange={(open) => !open && setSelectedHistory(null)} />
    </div>
  );
}
