import { HealthCheckView } from '@/components/BotWorkshop/BotHealthCheckDrawer/HealthCheckView';
import { Button } from '@/components/ui/Button';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import type { BotHealthCapability, BotHealthCheckSummary, BotHealthOverallStatus } from '@/domain/botHealthCheck';
import { HeartPulse, RefreshCw } from 'lucide-react';

interface BotHealthCheckDrawerProps {
  open: boolean;
  capability: BotHealthCapability;
  botName?: string;
  summary?: BotHealthCheckSummary;
  loading: boolean;
  checking: boolean;
  error?: string;
  onOpenChange: (open: boolean) => void;
  onRefresh: () => void | Promise<void>;
  onRunDiagnose: () => void | Promise<void>;
}

const overallMeta: Record<
  BotHealthOverallStatus,
  { label: string; tone: 'neutral' | 'primary' | 'success' | 'warning' | 'error' }
> = {
  healthy: { label: '健康', tone: 'success' },
  warning: { label: '需关注', tone: 'warning' },
  critical: { label: '异常', tone: 'error' },
  scanning: { label: '检测中', tone: 'primary' },
  unknown: { label: '暂无结果', tone: 'neutral' },
};

export default function BotHealthCheckDrawer({
  open,
  capability,
  botName,
  summary,
  loading,
  error,
  onOpenChange,
  onRefresh,
}: BotHealthCheckDrawerProps) {
  const meta = summary ? overallMeta[summary.overallStatus] : undefined;

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent size="full" aria-describedby="bot-health-check-description">
        <DrawerHeader>
          <DrawerTitle className="flex items-center gap-2 text-lg font-semibold text-[var(--color-fg)]">
            <HeartPulse className="size-5 text-[var(--color-primary)]" aria-hidden />
            {meta ? `${meta.label} - ` : ''}Bot 健康检查
          </DrawerTitle>
          <DrawerDescription id="bot-health-check-description" className="text-sm text-[var(--color-muted)]">
            {botName ? `${botName} 的健康检查结果` : '查看 Bot 健康检查结果'}
          </DrawerDescription>
        </DrawerHeader>

        {loading && !summary ? (
          <div className="flex flex-col items-center justify-center py-20">
            <Spin tip="正在加载健康检查结果" />
          </div>
        ) : null}

        {!loading && error ? (
          <Empty
            title="健康检查结果加载失败"
            description={error}
            action={
              <Button variant="secondary" onClick={() => void onRefresh()}>
                重试
              </Button>
            }
          />
        ) : null}

        {!loading && !error && !summary ? (
          <Empty
            title="暂无健康检查结果"
            description="可以点击重新检测，结果生成后刷新查看。"
            action={<Button onClick={() => void onRefresh()}>重新检测</Button>}
          />
        ) : null}

        {summary ? (
          <HealthCheckView
            summary={summary}
            capability={capability}
            botName={botName}
            loading={loading}
            onReDiagnose={onRefresh}
          />
        ) : null}

        <DrawerFooter>
          <Button
            variant="secondary"
            leftIcon={<RefreshCw className="size-4" />}
            disabled={loading}
            onClick={() => void onRefresh()}
          >
            刷新结果
          </Button>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}
