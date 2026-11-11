import { Badge } from '@/components/ui/Badge';
import { Empty } from '@/components/ui/Empty';
import { Download, History } from 'lucide-react';
import { evaluationStatusLabel, evaluationStatusTone } from './evaluationStatus';

interface EvaluationHistoryRecord {
  id?: string;
  mode?: string;
  status?: string;
  summary?: string;
  report_url?: string;
  archive_download_url?: string;
  evaluated_at?: string;
}

interface EvaluationHistoryCardProps {
  records?: EvaluationHistoryRecord[];
}

export const EvaluationHistoryCard = ({ records }: EvaluationHistoryCardProps) => (
  <div>
    {records?.length ? (
      <div className="overflow-hidden rounded-lg border border-border">
        {records.map((item, index) => (
          <div
            key={item.id ?? String(index)}
            className="flex flex-wrap items-center justify-between gap-3 border-border px-4 py-3 text-xs odd:bg-background even:bg-muted/20"
          >
            <div>
              <p className="m-0 font-medium text-foreground">{item.mode ?? '统一评测'}</p>
              <p className="m-0 mt-1 text-muted-foreground">
                {item.evaluated_at ?? '时间未知'} · {item.summary ?? '暂无摘要'}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={evaluationStatusTone(item.status)}>{evaluationStatusLabel(item.status)}</Badge>
              {item.report_url ? (
                <a
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                  href={item.report_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <History className="size-3.5" />
                  查看报告
                </a>
              ) : null}
              {item.archive_download_url ? (
                <a
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                  href={item.archive_download_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Download className="size-3.5" />
                  下载材料包
                </a>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    ) : (
      <Empty title="暂无历史评测记录" description="完成评测后在查看到历史结论与报告。" />
    )}
  </div>
);
