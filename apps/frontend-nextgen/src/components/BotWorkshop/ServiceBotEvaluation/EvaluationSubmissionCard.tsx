import { Download } from 'lucide-react';
import { evaluationStatusLabel } from './evaluationStatus';

interface EvaluationSubmission {
  submission_id: string;
  task_status: string;
  message?: string;
  archive_download_url?: string;
}

interface EvaluationSubmissionCardProps {
  submission?: EvaluationSubmission;
}

export const EvaluationSubmissionCard = ({ submission }: EvaluationSubmissionCardProps) => {
  if (!submission) return null;
  return (
    <div className="mt-4 grid gap-2 rounded-lg border border-border bg-muted/30 p-4 text-xs md:grid-cols-3">
      <div>
        <p className="font-medium text-muted-foreground">提交编号</p>
        <p className="mt-1 truncate font-medium">{submission.submission_id || '—'}</p>
      </div>
      <div>
        <p className="font-medium text-muted-foreground">状态</p>
        <p className="mt-1 font-medium">{evaluationStatusLabel(submission.task_status)}</p>
      </div>
      <div>
        <p className="font-medium text-muted-foreground">说明</p>
        <p className="mt-1">{submission.message ?? '等待认证结果'}</p>
      </div>
      {submission.archive_download_url ? (
        <div className="md:col-span-3">
          <a
            className="inline-flex items-center gap-1 text-primary hover:underline"
            href={submission.archive_download_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Download className="size-3.5" />
            下载本次评测材料包
          </a>
        </div>
      ) : null}
    </div>
  );
};
