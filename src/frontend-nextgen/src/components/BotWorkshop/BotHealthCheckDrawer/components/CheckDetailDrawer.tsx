import { ExpandableCheckTable } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/ExpandableCheckTable';
import { DIM_STATUS_STYLES } from '@/components/BotWorkshop/BotHealthCheckDrawer/constants';
import { deriveRepairRecords, formatDateTime } from '@/components/BotWorkshop/BotHealthCheckDrawer/utils';
import { Badge } from '@/components/ui/Badge';
import { Card, CardContent } from '@/components/ui/Card';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import type { BotHealthDimension, BotHealthHistoryItem } from '@/domain/botHealthCheck';
import { ShieldCheck } from 'lucide-react';

interface CheckDetailDrawerProps {
  item: BotHealthHistoryItem | null;
  onOpenChange: (open: boolean) => void;
}

export function CheckDetailDrawer({ item, onOpenChange }: CheckDetailDrawerProps) {
  const dimension: BotHealthDimension | null = item?.dimension ?? null;
  const dimStyle = DIM_STATUS_STYLES[dimension?.status ?? 'completed'] ?? { label: '已完成', tone: 'success' as const };
  const repairs = deriveRepairRecords(dimension);

  return (
    <Drawer open={Boolean(item)} onOpenChange={onOpenChange}>
      <DrawerContent side="right" size="lg" aria-describedby="check-detail-description">
        <DrawerHeader>
          <DrawerTitle className="text-lg font-semibold text-[var(--color-fg)]">体检详情</DrawerTitle>
          <DrawerDescription id="check-detail-description" className="text-sm text-[var(--color-muted)]">
            {dimension ? `${dimension.label} 体检详情` : '查看历史体检详情'}
          </DrawerDescription>
        </DrawerHeader>
        {dimension ? (
          <div className="space-y-5 p-6 pt-0">
            <div className="flex items-center gap-2">
              <ShieldCheck className="size-5 text-[var(--color-primary)]" aria-hidden />
              <span className="text-base font-semibold text-[var(--color-fg)]">{dimension.label}体检</span>
              <Badge tone={dimStyle.tone}>{dimStyle.label}</Badge>
            </div>

            <p className="text-sm text-[var(--color-muted)]">体检时间：{formatDateTime(dimension.updatedAt)}</p>

            <div className="grid gap-3 md:grid-cols-4">
              <Card className="rounded-xl bg-[var(--color-panel-muted)]">
                <CardContent className="py-5 text-center">
                  <div className="text-2xl font-semibold text-[var(--color-fg)]">{dimension.checkedCount ?? 0}</div>
                  <div className="mt-1 text-xs text-[var(--color-muted)]">已检项目</div>
                </CardContent>
              </Card>
              <Card className="rounded-xl bg-[var(--color-success)]/5">
                <CardContent className="py-5 text-center">
                  <div className="text-2xl font-semibold text-[var(--color-success)]">{dimension.passedCount ?? 0}</div>
                  <div className="mt-1 text-xs text-[var(--color-muted)]">通过</div>
                </CardContent>
              </Card>
              <Card className="rounded-xl bg-[var(--color-warning)]/5">
                <CardContent className="py-5 text-center">
                  <div className="text-2xl font-semibold text-[var(--color-warning)]">
                    {dimension.warningCount ?? 0}
                  </div>
                  <div className="mt-1 text-xs text-[var(--color-muted)]">警告</div>
                </CardContent>
              </Card>
              <Card className="rounded-xl bg-[var(--color-error)]/5">
                <CardContent className="py-5 text-center">
                  <div className="text-2xl font-semibold text-[var(--color-error)]">{dimension.errorCount ?? 0}</div>
                  <div className="mt-1 text-xs text-[var(--color-muted)]">未通过</div>
                </CardContent>
              </Card>
            </div>

            <ExpandableCheckTable dimension={dimension} />

            {repairs.length > 0 ? (
              <div className="space-y-3">
                <div className="text-sm font-semibold text-[var(--color-fg)]">修复历史</div>
                <Card className="rounded-xl">
                  <CardContent className="p-0">
                    <div className="overflow-hidden rounded-xl">
                      <table className="w-full border-collapse">
                        <thead>
                          <tr className="border-b border-[var(--color-border)] text-xs font-medium text-[var(--color-muted)]">
                            <th className="px-4 py-3 text-left">修复时间</th>
                            <th className="px-4 py-3 text-left">修复项</th>
                            <th className="px-4 py-3 text-left">状态</th>
                          </tr>
                        </thead>
                        <tbody>
                          {repairs.map((record) => (
                            <tr
                              key={String(record.id)}
                              className="border-b border-[var(--color-border)] text-sm last:border-b-0"
                            >
                              <td className="px-4 py-3 tabular-nums text-[var(--color-fg)]">
                                {formatDateTime(record.gmt_create)}
                              </td>
                              <td className="px-4 py-3 text-[var(--color-fg)]">{record.name ?? '-'}</td>
                              <td className="px-4 py-3">
                                <Badge tone={record.status === 'applied' ? 'success' : 'neutral'}>
                                  {record.status === 'applied' ? '已应用' : '未应用'}
                                </Badge>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>
              </div>
            ) : null}
          </div>
        ) : null}
      </DrawerContent>
    </Drawer>
  );
}
