import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Empty } from '@/components/ui/Empty';
import { Download, FileText, History } from 'lucide-react';
import { evaluationStatusLabel, evaluationStatusTone } from './evaluationStatus';

interface EvaluationHistoryRecord {
  id?: string;
  record_id?: string;
  release_id?: string;
  publish_id?: string;
  bot_version?: string;
  mode?: string;
  status?: string;
  gate_status?: string;
  cert_id?: string;
  cert_result?: string;
  summary?: string;
  decision_reason?: string;
  report_url?: string;
  archive_download_url?: string;
  evaluated_at?: string;
  decided_at?: string;
}

interface EvaluationHistoryCardProps {
  currentRelease?: EvaluationHistoryRecord | null;
  records?: EvaluationHistoryRecord[];
}

const gateStatusTone = (record: EvaluationHistoryRecord): BadgeTone => {
  const gateStatus = record.gate_status?.toUpperCase();
  if (gateStatus === 'PASSED') return 'success';
  if (gateStatus === 'BLOCKED') return 'error';
  if (gateStatus === 'SKIPPED') return 'warning';
  return evaluationStatusTone(record.gate_status || record.status);
};

const gateStatusLabel = (record: EvaluationHistoryRecord) => {
  const gateStatus = record.gate_status?.toUpperCase();
  if (gateStatus === 'PASSED') return '门禁通过';
  if (gateStatus === 'BLOCKED') return '门禁未通过';
  if (gateStatus === 'SKIPPED') return '门禁未拦截';
  if (!gateStatus) return evaluationStatusLabel(record.status);
  return record.gate_status as string;
};

const evaluatedAt = (record: EvaluationHistoryRecord) =>
  record.evaluated_at ?? record.decided_at ?? '时间未知';

const releaseTitle = (record: EvaluationHistoryRecord, isCurrent: boolean) =>
  isCurrent
    ? `当前发布单 · ${record.bot_version ?? '当前版本'}`
    : `版本 ${record.bot_version ?? '未知'}`;

const releaseMeta = (record: EvaluationHistoryRecord) => {
  const publishId = record.publish_id || record.release_id || record.record_id;
  const meta: string[] = [];
  if (publishId) meta.push(`Publish ID：${publishId}`);
  if (record.cert_id) meta.push(`认证编号：${record.cert_id}`);
  return meta.join(' · ');
};

const releaseSummary = (record: EvaluationHistoryRecord) =>
  record.decision_reason || record.summary || '暂无评测摘要';

const HistoryRecordRow = ({
  record,
  isCurrent = false,
}: {
  record: EvaluationHistoryRecord;
  isCurrent?: boolean;
}) => (
  <div className="flex flex-col gap-3 border-border px-4 py-3 text-xs odd:bg-background even:bg-muted/20 sm:flex-row sm:items-center sm:justify-between">
    <div className="min-w-0 flex-1">
      <p className="m-0 font-medium text-foreground">
        {releaseTitle(record, isCurrent)}
        <span className="ml-2 text-muted-foreground">{record.mode ?? '极简统一评测'}</span>
      </p>
      <p className="m-0 mt-1 break-words text-muted-foreground">{evaluatedAt(record)}</p>
      <p className="m-0 mt-1 break-words text-muted-foreground">{releaseMeta(record) || '—'}</p>
      <p className="m-0 mt-1 break-words text-muted-foreground">{releaseSummary(record)}</p>
    </div>
    <div className="flex shrink-0 flex-wrap items-center justify-start gap-2 sm:justify-end">
      <Badge tone={gateStatusTone(record)}>{gateStatusLabel(record)}</Badge>
      {record.report_url ? (
        <a
          className="inline-flex items-center gap-1 text-primary hover:underline"
          href={record.report_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          <History className="size-3.5" />
          评测报告
        </a>
      ) : null}
      {record.archive_download_url ? (
        <a
          className="inline-flex items-center gap-1 text-primary hover:underline"
          href={record.archive_download_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          <Download className="size-3.5" />
          评测数据
        </a>
      ) : null}
    </div>
  </div>
);

export const EvaluationHistoryCard = ({ currentRelease, records }: EvaluationHistoryCardProps) => (
  <div className="space-y-5">
    <section>
      <div className="mb-2 flex items-center gap-2">
        <FileText className="size-4 text-primary" />
        <h3 className="m-0 text-sm font-medium text-foreground">当前 Publish ID 最终门禁结果</h3>
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        {currentRelease ? (
          <HistoryRecordRow record={currentRelease} isCurrent />
        ) : (
          <div className="px-4">
            <Empty
              title="当前发布单暂无最终结论"
              description="发布门禁或评测认证完成后，这里会展示当前发布单的最新结论。"
            />
          </div>
        )}
      </div>
    </section>

    <section>
      <div className="mb-2 flex items-center gap-2">
        <History className="size-4 text-primary" />
        <h3 className="m-0 text-sm font-medium text-foreground">历史版本最终门禁结果</h3>
      </div>
      {records?.length ? (
        <div className="overflow-hidden rounded-lg border border-border">
          {records.map((item, index) => (
            <HistoryRecordRow key={item.id ?? item.release_id ?? String(index)} record={item} />
          ))}
        </div>
      ) : (
        <Empty
          title="暂无历史版本结论"
          description="当前 Bot 的前序版本完成评测认证后，会在这里展示各版本最终门禁结果。"
        />
      )}
    </section>
  </div>
);
