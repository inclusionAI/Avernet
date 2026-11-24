import { Button } from '@/components/ui/Button';
import type { StandardEvaluationFlowResult } from '@/hooks/useStandardEvaluationFlow';
import { AlertTriangle, CheckCircle2, Clock3, ExternalLink, XCircle } from 'lucide-react';

interface StandardEvaluationFlowResultBannerProps {
  result: StandardEvaluationFlowResult;
  reportUrl?: string;
  onViewHistory?: () => void;
}

export function StandardEvaluationFlowResultBanner({
  result,
  reportUrl,
  onViewHistory,
}: StandardEvaluationFlowResultBannerProps) {
  const success = result.kind === 'success' || result.kind === 'step_retry_succeeded';
  const completedNotPassed = result.kind === 'completed_but_not_passed';
  const interrupted = result.kind === 'interrupted';

  const className = success
    ? 'border-success/40 bg-success/5'
    : completedNotPassed
    ? 'border-warning/40 bg-warning/5'
    : interrupted
    ? 'border-destructive/40 bg-destructive/5'
    : 'border-primary/30 bg-primary/5';

  const Icon = success ? CheckCircle2 : completedNotPassed ? AlertTriangle : interrupted ? XCircle : Clock3;

  let title = '';
  let description = result.message || '';

  if (result.kind === 'success') {
    title = '评测流程已完成';
    description = result.message || '评测结果已通过，本次评测记录已保存。';
  } else if (completedNotPassed) {
    title = '评测流程已完成，但结果未通过';
    description = result.message || '已按 BLOCKED 保存门禁结果，可查看评测报告定位问题。';
  } else if (interrupted) {
    title = '评测流程中断';
    description = result.stepTitle
      ? `${result.stepTitle}执行失败。${result.message || ''}`
      : result.message || '评测流程已中断，可从失败节点重试。';
  } else if (result.kind === 'running_in_background') {
    title = '评测仍在后台执行';
    description = result.message || '当前页面已停止跟踪，可稍后在评测记录中查看结果。';
  } else {
    title = '节点重试成功';
    description = result.stepTitle
      ? `${result.stepTitle}已重试成功。${result.message || ''}`
      : result.message || '节点已重试成功，请继续执行后续节点。';
  }

  const showReport = Boolean(reportUrl) && (result.kind === 'success' || completedNotPassed);

  return (
    <div
      className={`flex flex-col gap-3 rounded-lg border px-4 py-3 text-xs sm:flex-row sm:items-start ${className}`}
      role="status"
      aria-live="polite"
    >
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <Icon
          aria-hidden
          className={`mt-0.5 size-4 shrink-0 ${
            success
              ? 'text-success'
              : completedNotPassed
              ? 'text-warning'
              : interrupted
              ? 'text-destructive'
              : 'text-primary'
          }`}
        />
        <div className="min-w-0">
          <p className="m-0 text-sm font-semibold text-foreground">{title}</p>
          {description ? <p className="m-0 mt-1 leading-5 text-muted-foreground">{description}</p> : null}
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {showReport ? (
          <a
            className="inline-flex items-center gap-1 text-primary hover:underline"
            href={reportUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLink className="size-3.5" />
            查看评测报告
          </a>
        ) : null}
        {result.kind !== 'interrupted' && onViewHistory ? (
          <Button variant="secondary" size="sm" onClick={onViewHistory}>
            查看评测记录
          </Button>
        ) : null}
      </div>
    </div>
  );
}
