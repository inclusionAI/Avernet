import { Button } from '@/components/ui/Button';
import type { JsonObject } from '@/domain/evolution/types';
import { ChevronDown } from 'lucide-react';
import { useState } from 'react';
import { BenchResults } from './BenchResults';
import { DiagnosisResults } from './DiagnosisResults';
import { array, delta, MarkdownReport, object, present, ResultCard, RunLink, text } from './OutputPrimitives';
import { JsonView } from './shared';
export { MarkdownReport } from './OutputPrimitives';

/** Preview the real deliverables, not generated descriptions or inferred scores. */
export function outputHighlights(output: JsonObject): string[] {
  const spec = object(output.spec ?? output.specV0),
    cases = object(output.cases),
    comparison = object(output.scoreComparison);
  return [
    present(object(output.diagnosis)) ? '诊断结论' : '',
    present(cases) ? `Case ${text(cases.total ?? array(cases.items).length)}` : '',
    output.goal ? '进化目标' : '',
    present(spec) ? `Spec ${text(spec.version)}` : '',
    present(object(output.benchCases)) ? '训练 / 测试 Case' : '',
    present(object(output.baseline)) ? 'Train / Test Baseline' : '',
    present(comparison)
      ? `Test ${text(comparison.baseline)} → ${text(comparison.candidate)} · Δ ${delta(comparison.delta)}`
      : '',
    present(object(output.diff)) ? '变更 Diff' : '',
    typeof output.benchRunId === 'string' ? '评测结果' : '',
  ].filter(Boolean);
}

/** ClawWeb result categories, presented inside the PRD detail layout. No execution changes. */
export function StepOutput({
  output,
  showTitle = true,
  stepId,
  onViewDiff,
  diff,
  diffLoading,
  diffError,
}: {
  output: JsonObject;
  showTitle?: boolean;
  stepId?: string;
  onViewDiff?: () => void;
  diff?: string;
  diffLoading?: boolean;
  diffError?: string;
}) {
  const [specExpanded, setSpecExpanded] = useState(false);
  const spec = object(output.spec ?? output.specV0),
    comparison = object(output.scoreComparison);
  const baseline = object(output.baseline);
  const change = object(output.diff),
    round = object(output.roundDecision),
    metrics = array(output.metrics);
  const specContent = typeof spec.content === 'string' ? spec.content : JSON.stringify(spec.content ?? {}, null, 2);
  const specLines = specContent.split('\n').length;
  const specCollapsible = specLines > 12 || specContent.length > 1200;
  const knownRuns = new Set([
    output.benchRunId,
    ...Object.values(baseline).map((v) => object(v).benchRunId),
    ...metrics.map((v) => v.benchRunId),
  ]);
  const runIds = new Set<string>();
  const collectRuns = (value: unknown) => {
    if (Array.isArray(value)) value.forEach(collectRuns);
    else if (value && typeof value === 'object')
      Object.entries(value).forEach(([key, child]) => {
        if (key === 'benchRunId' && typeof child === 'string' && child) runIds.add(child);
        else collectRuns(child);
      });
  };
  collectRuns(output);
  return (
    <div className="space-y-4">
      {showTitle && <h3 className="font-medium">产出物</h3>}
      {typeof output.summary === 'string' && (
        <p className="text-sm leading-relaxed text-muted-foreground">{output.summary}</p>
      )}
      <DiagnosisResults output={output} />
      <BenchResults output={output} stepId={stepId} />
      {present(change) && (
        <ResultCard title="本轮 Diff">
          <p className="text-sm leading-relaxed">{text(change.summary)}</p>
          {array(change.files).length > 0 && (
            <div className="mt-3 divide-y divide-divider rounded-lg bg-background/80 px-3">
              {array(change.files).map((file, i) => (
                <div key={i} className="py-3">
                  <p className="break-all font-mono text-xs">{text(file.path)}</p>
                  {typeof file.change === 'string' && (
                    <p className="mt-1 text-xs text-muted-foreground">{file.change}</p>
                  )}
                </div>
              ))}
            </div>
          )}
          {onViewDiff && (
            <Button variant="outline" size="sm" className="mt-3" loading={diffLoading} onClick={onViewDiff}>
              {diff === undefined ? '查看完整 Diff' : '刷新 Diff'}
            </Button>
          )}
          {diffError && (
            <p role="alert" className="mt-2 text-sm text-destructive">
              {diffError}
            </p>
          )}
          {diff !== undefined && (
            <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-background/80 p-3 font-mono text-xs leading-relaxed">
              {diff || 'Diff 内容为空'}
            </pre>
          )}
        </ResultCard>
      )}
      {metrics.length > 0 && (
        <ResultCard title="本轮运行指标" tone="green">
          <div className="grid gap-3 sm:grid-cols-2">
            {metrics.map((metric, i) => (
              <div key={i} className="min-w-0 rounded-lg bg-background/80 p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h5 className="break-words text-sm font-medium">{text(metric.name ?? metric.key)}</h5>
                  <p className="font-mono text-xl font-semibold">
                    {text(metric.value)}{' '}
                    <span className="text-xs font-normal">{typeof metric.unit === 'string' ? metric.unit : ''}</span>
                  </p>
                </div>
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  {metric.target !== null && metric.target !== undefined && <span>目标 {text(metric.target)}</span>}
                  {metric.baselineValue !== null && metric.baselineValue !== undefined && (
                    <span>
                      基线 {text(metric.baselineValue)} · Δ {delta(metric.delta)}
                    </span>
                  )}
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                  {typeof metric.passed === 'boolean' && (
                    <span
                      className={`rounded px-2 py-1 text-xs ${
                        metric.passed ? 'bg-emerald-100 text-emerald-800' : 'bg-red-50 text-red-700'
                      }`}
                    >
                      {metric.passed ? '已达标' : '未达标'}
                    </span>
                  )}
                  <RunLink runId={metric.benchRunId} />
                </div>
              </div>
            ))}
          </div>
        </ResultCard>
      )}
      {spec.content !== undefined && (
        <ResultCard
          title={`目标规划 Spec ${text(spec.version)}`}
          tone="amber"
          extra={
            <span className="rounded bg-background/80 px-2 py-1 text-xs text-muted-foreground">
              {typeof spec.content_type === 'string'
                ? spec.content_type
                : typeof spec.content === 'string'
                ? 'text'
                : 'json'}{' '}
              · {specLines} 行
            </span>
          }
        >
          <div className="relative overflow-hidden rounded-lg bg-background/80">
            <div
              className={`overflow-auto break-words p-4 text-sm leading-6 [&_h1]:mb-3 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-sm [&_h2]:font-semibold [&_p]:mb-3 [&_ol]:list-decimal [&_ul]:list-disc [&_li]:ml-5 ${
                specExpanded || !specCollapsible ? 'max-h-[640px]' : 'max-h-52'
              }`}
            >
              {typeof spec.content === 'string' ? (
                <MarkdownReport content={spec.content} />
              ) : (
                <JsonView value={spec.content} />
              )}
            </div>
            {specCollapsible && !specExpanded && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-background to-transparent" />
            )}
          </div>
          {specCollapsible && (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2 px-0"
              aria-expanded={specExpanded}
              onClick={() => setSpecExpanded((v) => !v)}
            >
              {specExpanded ? '收起 Spec' : '展开完整 Spec'}
              <ChevronDown size={14} className={`ml-1 ${specExpanded ? 'rotate-180' : ''}`} />
            </Button>
          )}
        </ResultCard>
      )}
      {(present(comparison) || typeof output.benchDecision === 'string' || typeof output.reviewStatus === 'string') && (
        <ResultCard
          title="Bench / Review"
          tone="violet"
          extra={
            <div className="flex flex-wrap gap-2 text-xs">
              {typeof output.benchDecision === 'string' && (
                <span className="rounded bg-background/80 px-2 py-1">Bench：{output.benchDecision}</span>
              )}
              {typeof output.reviewStatus === 'string' && (
                <span className="rounded bg-background/80 px-2 py-1">Review：{output.reviewStatus}</span>
              )}
            </div>
          }
        >
          {present(comparison) && (
            <>
              <p className="mb-3 text-xs text-muted-foreground">{text(comparison.name)}</p>
              <dl className="grid grid-cols-3 gap-2">
                {[
                  ['Test baseline', text(comparison.baseline)],
                  ['Test candidate', text(comparison.candidate)],
                  ['Test delta', delta(comparison.delta)],
                ].map(([name, value]) => (
                  <div key={name} className="min-w-0 rounded-lg bg-background/80 p-3">
                    <dt className="text-xs text-muted-foreground">{name}</dt>
                    <dd className="mt-2 break-all font-mono text-lg font-semibold">{value}</dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </ResultCard>
      )}
      {present(round) && (
        <ResultCard
          title="轮次决策"
          tone={round.stop === true ? 'green' : 'amber'}
          extra={
            typeof round.stop === 'boolean' && (
              <span className="rounded bg-background/80 px-2 py-1 text-xs">
                {round.stop ? '停止优化' : '继续下一轮'}
              </span>
            )
          }
        >
          <p className="text-sm leading-relaxed">{text(round.reason)}</p>
        </ResultCard>
      )}
      {[...runIds]
        .filter((id) => !knownRuns.has(id))
        .map((runId) => (
          <RunLink key={runId} runId={runId} />
        ))}
      <details className="rounded-xl border border-dashed border-border p-4">
        <summary className="cursor-pointer text-xs text-muted-foreground">原始产出数据 · 技术信息</summary>
        <div className="mt-3">
          <JsonView value={output} />
        </div>
      </details>
    </div>
  );
}
