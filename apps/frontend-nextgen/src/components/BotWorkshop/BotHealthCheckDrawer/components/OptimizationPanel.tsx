import { isPatchAllApplied } from '@/components/BotWorkshop/BotHealthCheckDrawer/utils';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import type { BotHealthDimension } from '@/domain/botHealthCheck';
import { Lightbulb } from 'lucide-react';
import { AdvicePatchCard } from './AdvicePatchCard';

interface OptimizationPanelProps {
  dimension: BotHealthDimension;
}

export function OptimizationPanel({ dimension }: OptimizationPanelProps) {
  const status = dimension.status;
  const isScanning = status === 'scanning';
  const isFailed = ['failed', 'error'].includes((dimension.scanStatus ?? '').toLowerCase());
  const patches = dimension.patches ?? [];
  const hasDimData = dimension.checkItems && dimension.checkItems.length > 0;
  const showHealthyResult =
    !isScanning &&
    !isFailed &&
    patches.length === 0 &&
    hasDimData &&
    status === 'passed' &&
    (dimension.warningCount ?? 0) === 0 &&
    (dimension.errorCount ?? 0) === 0;

  if (!isScanning && !isFailed && patches.length === 0 && !showHealthyResult) {
    return null;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-fg)]">
        <Lightbulb className="size-4 text-[var(--color-warning)]" aria-hidden />
        优化建议
      </div>

      {isScanning && !hasDimData ? (
        <Card className="rounded-xl border-[var(--color-warning)] bg-[var(--color-warning)]/5">
          <CardContent className="py-6 text-center text-sm text-[var(--color-warning)]">
            健康检测中，等待生成优化建议
          </CardContent>
        </Card>
      ) : null}

      {isScanning && hasDimData ? (
        <Card className="rounded-xl border-[var(--color-warning)] bg-[var(--color-warning)]/5">
          <CardContent className="py-6 text-center text-sm text-[var(--color-warning)]">正在生成优化建议</CardContent>
        </Card>
      ) : null}

      {isFailed ? (
        <Card className="rounded-xl border-[var(--color-error)] bg-[var(--color-error)]/5">
          <CardContent className="py-6 text-center text-sm text-[var(--color-error)]">
            检测失败：{dimension.failedReason ?? '未知原因'}
          </CardContent>
        </Card>
      ) : null}

      {!isScanning && !isFailed && patches.length > 0 ? (
        <div className="space-y-3">
          {patches.map((patch, index) =>
            patch.is_advise ? (
              <AdvicePatchCard key={String(patch.patch_id)} patch={patch} ordinal={index + 1} />
            ) : (
              <Card key={String(patch.patch_id)} className="rounded-xl border-[var(--color-border)]">
                <CardContent className="space-y-3 p-4">
                  <div className="text-sm font-semibold text-[var(--color-fg)]">
                    {index + 1}. {patch.name}
                  </div>
                  {patch.description ? (
                    <div className="text-sm text-[var(--color-muted)]">{patch.description}</div>
                  ) : null}
                  <div className="flex items-center gap-3">
                    {patch.is_applied ? (
                      <>
                        <Badge tone="success">已修复</Badge>
                        <Button variant="ghost" size="sm">
                          查看修复记录
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" onClick={() => {}}>
                        一键修复
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            ),
          )}
          {isPatchAllApplied(patches) ? (
            <Card className="rounded-xl border-[var(--color-success)] bg-[var(--color-success)]/5">
              <CardContent className="py-4 text-sm text-[var(--color-success)]">
                恭喜！您已完成所有优化建议的修复。建议重新检测以确认效果。
              </CardContent>
            </Card>
          ) : null}
        </div>
      ) : null}

      {showHealthyResult ? (
        <Card className="rounded-xl border-[var(--color-border)]">
          <CardContent className="py-6 text-center text-sm text-[var(--color-muted)]">
            <span className="text-[var(--color-success)]">未发现需要优化的问题，Bot 状态良好！</span>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
