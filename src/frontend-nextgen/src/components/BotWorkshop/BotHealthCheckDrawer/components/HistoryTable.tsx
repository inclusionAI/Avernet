import { REPORT_TYPE_LABELS } from '@/components/BotWorkshop/BotHealthCheckDrawer/constants';
import { formatDate } from '@/components/BotWorkshop/BotHealthCheckDrawer/utils';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import type { BotHealthHistoryItem } from '@/domain/botHealthCheck';
import { History } from 'lucide-react';

interface HistoryTableProps {
  history: BotHealthHistoryItem[];
  showDetails: boolean;
  onViewDetail: (item: BotHealthHistoryItem) => void;
}

export function HistoryTable({ history, showDetails, onViewDetail }: HistoryTableProps) {
  if (history.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-fg)]">
        <History className="size-4 text-[var(--color-muted)]" aria-hidden />
        历史体检记录
      </div>
      <Card className="rounded-xl">
        <CardContent className="p-0">
          <div className="overflow-hidden rounded-xl">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-xs font-medium text-[var(--color-muted)]">
                  <th className="px-4 py-3 text-left">体检时间</th>
                  <th className="px-4 py-3 text-left">类型</th>
                  <th className="px-4 py-3 text-left">发起方</th>
                  <th className="px-4 py-3 text-left">体检项目</th>
                  <th className="px-4 py-3 text-left">警告项</th>
                  <th className="px-4 py-3 text-left">未通过项</th>
                  {showDetails ? <th className="px-4 py-3 text-left">操作</th> : null}
                </tr>
              </thead>
              <tbody>
                {history.map((item) => {
                  return (
                    <tr key={item.id} className="border-b border-[var(--color-border)] text-sm last:border-b-0">
                      <td className="px-4 py-3 tabular-nums text-[var(--color-fg)]">{formatDate(item.checkedAt)}</td>
                      <td className="px-4 py-3 text-[var(--color-fg)]">
                        {REPORT_TYPE_LABELS[item.scanReportType ?? 'normal'] ?? '默认'}
                      </td>
                      <td className="px-4 py-3 text-[var(--color-fg)]">{item.triggerSource ?? '-'}</td>
                      <td className="px-4 py-3 text-[var(--color-fg)]">{item.label}</td>
                      <td className="px-4 py-3 tabular-nums text-[var(--color-warning)]">
                        {item.dimension.warningCount ?? 0}
                      </td>
                      <td className="px-4 py-3 tabular-nums text-[var(--color-error)]">
                        {item.dimension.errorCount ?? 0}
                      </td>
                      {showDetails ? (
                        <td className="px-4 py-3">
                          <Button variant="ghost" size="sm" onClick={() => onViewDetail(item)}>
                            查看详情
                          </Button>
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
