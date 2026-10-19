import { sanitizeMarkdownHtml } from '@/assets/TaskPanel/sanitizeHtml';
import { Button } from '@/components/ui/Button';
import type { JsonObject } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { ArrowUpRight, ChevronDown } from 'lucide-react';
import MarkdownIt from 'markdown-it';
import { useMemo, useState, type ReactNode } from 'react';
import { displayValue } from './presentation';
import { JsonView } from './shared';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true });
export function MarkdownReport({ content }: { content: string }) {
  const html = useMemo(() => sanitizeMarkdownHtml(markdown.render(content)), [content]);
  return (
    <div
      className="prose prose-sm max-w-none overflow-x-auto break-words dark:prose-invert"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
const object = (v: unknown): JsonObject => (v && typeof v === 'object' && !Array.isArray(v) ? (v as JsonObject) : {});
const array = (v: unknown): JsonObject[] => (Array.isArray(v) ? v.map(object) : []);
const present = (v: JsonObject) => Object.keys(v).length > 0;
const text = displayValue;
const delta = (v: unknown) => (typeof v === 'number' && v > 0 ? `+${text(v)}` : text(v));
const tones = {
  blue: 'border-blue-200/70 bg-blue-50/60 dark:border-blue-800 dark:bg-blue-950/20',
  violet: 'border-violet-200/70 bg-violet-50/60 dark:border-violet-800 dark:bg-violet-950/20',
  green: 'border-emerald-200/70 bg-emerald-50/60 dark:border-emerald-800 dark:bg-emerald-950/20',
  amber: 'border-amber-200/70 bg-amber-50/60 dark:border-amber-800 dark:bg-amber-950/20',
};
function ResultCard({
  title,
  tone = 'blue',
  extra,
  children,
}: {
  title: string;
  tone?: keyof typeof tones;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`min-w-0 rounded-xl border p-4 ${tones[tone]}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{title}</h4>
        {extra}
      </div>
      {children}
    </section>
  );
}
function RunLink({ runId }: { runId: unknown }) {
  if (typeof runId !== 'string' || !runId) return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      className="max-w-full px-0 text-primary"
      title={runId}
      aria-label={`评估结果 · ${runId}`}
      onClick={() => history.push(`/evolution-factory/bench/runs/${encodeURIComponent(runId)}`)}
    >
      查看评测结果
      <ArrowUpRight size={14} className="ml-1 shrink-0" />
    </Button>
  );
}
function Values({ rows }: { rows: [string, unknown][] }) {
  return (
    <dl className="grid grid-cols-2 gap-3">
      {rows.map(([name, value]) => (
        <div key={name} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{name}</dt>
          <dd className="mt-1 break-words font-mono text-sm font-semibold">{text(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
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
  const diagnosis = object(output.diagnosis),
    cases = object(output.cases),
    goal = object(output.goal);
  const spec = object(output.spec ?? output.specV0),
    comparison = object(output.scoreComparison);
  const baseline = object(output.baseline),
    benchCases = object(output.benchCases),
    domains = object(output.benchDomains);
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
  const caseItems = array(cases.items);
  const renderCase = (item: JsonObject, i: number) => (
    <div key={i} className="py-3 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-start gap-2">
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${
            item.type === 'good'
              ? 'bg-emerald-100 text-emerald-800'
              : item.type === 'bad'
              ? 'bg-red-50 text-red-700'
              : 'bg-muted text-muted-foreground'
          }`}
        >
          {text(item.type)}
        </span>
        <span className="min-w-0 break-all font-mono text-xs leading-5">{text(item.caseId)}</span>
      </div>
      <p className="mt-2 break-words text-sm leading-relaxed text-muted-foreground">{text(item.summary)}</p>
    </div>
  );
  return (
    <div className="space-y-4">
      {showTitle && <h3 className="font-medium">产出物</h3>}
      {typeof output.summary === 'string' && (
        <p className="text-sm leading-relaxed text-muted-foreground">{output.summary}</p>
      )}
      {(present(diagnosis) || present(cases) || Boolean(output.goal)) && (
        <div className="grid gap-3 sm:grid-cols-2">
          {present(diagnosis) && (
            <ResultCard title="诊断结论" tone="violet">
              <p className="break-words text-sm leading-relaxed">{text(diagnosis.summary)}</p>
            </ResultCard>
          )}
          {present(cases) && (
            <ResultCard title="Bench Case">
              <p className="text-xl font-semibold tabular-nums">
                {text(cases.total ?? caseItems.length)}{' '}
                <span className="text-xs font-normal text-muted-foreground">个 Case</span>
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                Good {text(cases.goodCount)} / Bad {text(cases.badCount)}
              </p>
            </ResultCard>
          )}
          {Boolean(output.goal) && (
            <ResultCard title="进化目标" tone="green">
              <p className="break-words text-sm leading-relaxed">
                {typeof output.goal === 'string' ? output.goal : text(goal.title ?? goal.summary)}
              </p>
            </ResultCard>
          )}
        </div>
      )}
      {array(diagnosis.issues).length > 0 && (
        <details className="rounded-xl border border-border p-4">
          <summary className="cursor-pointer text-sm font-medium">
            问题与优化建议 · {array(diagnosis.issues).length}
          </summary>
          <div className="mt-3 divide-y divide-border">
            {array(diagnosis.issues).map((issue, i) => (
              <div key={i} className="py-3 first:pt-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium">{text(issue.title)}</p>
                  <span className="rounded bg-muted px-2 py-0.5 text-xs">{text(issue.severity)}</span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{text(issue.suggestion)}</p>
              </div>
            ))}
          </div>
        </details>
      )}
      {array(goal.metrics).length > 0 && (
        <ResultCard title="目标指标" tone="green">
          <div className="grid gap-2 sm:grid-cols-2">
            {array(goal.metrics).map((metric, i) => (
              <div
                key={i}
                className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-lg bg-background/80 p-3 text-sm"
              >
                <span className="break-words font-medium">{text(metric.name ?? metric.key)}</span>
                <span className="font-mono">
                  {typeof metric.operator === 'string' ? metric.operator : ''} {text(metric.target)}{' '}
                  {typeof metric.unit === 'string' ? metric.unit : ''}
                </span>
              </div>
            ))}
          </div>
        </ResultCard>
      )}
      {caseItems.length > 0 && (
        <section className="rounded-xl border border-border p-4">
          <h4 className="mb-3 text-sm font-semibold">诊断 Case</h4>
          <div className="divide-y divide-border">{caseItems.slice(0, 3).map(renderCase)}</div>
          {caseItems.length > 3 && (
            <details className="mt-3 border-t border-border pt-3">
              <summary className="cursor-pointer text-xs text-primary">查看其余 {caseItems.length - 3} 个 Case</summary>
              <div className="mt-3 divide-y divide-border">{caseItems.slice(3).map(renderCase)}</div>
            </details>
          )}
        </section>
      )}
      {present(benchCases) && (
        <ResultCard title="训练 / 测试 Case">
          <Values
            rows={[
              ['训练集', benchCases.trainCount],
              ['测试集', benchCases.testCount],
            ]}
          />
          {present(domains) && (
            <div className="mt-3 space-y-1 break-all text-xs text-muted-foreground">
              <p>Train Domain：{text(domains.trainBenchDomainId)}</p>
              <p>Test Domain：{text(domains.testBenchDomainId)}</p>
            </div>
          )}
          {array(benchCases.items).length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer text-xs text-primary">
                查看 Case 明细 · {array(benchCases.items).length}
              </summary>
              <div className="mt-3 divide-y divide-border">
                {array(benchCases.items).map((item, i) => {
                  const template = object(item.template);
                  return (
                    <div key={i} className="space-y-1 py-2 text-xs">
                      <p className="break-all">
                        <span className="mr-2 rounded bg-background px-1.5 py-0.5">{text(item.split)}</span>
                        {text(item.sourceCaseId ?? item.taskId)}
                      </p>
                      <p className="break-all text-muted-foreground">
                        {text(template.templateName)} · v{text(template.version)}
                      </p>
                    </div>
                  );
                })}
              </div>
            </details>
          )}
        </ResultCard>
      )}
      {present(baseline) && (
        <ResultCard title="Train / Test Baseline">
          <div className="grid gap-3 sm:grid-cols-2">
            {(['train', 'test'] as const).map((role) => {
              const result = object(baseline[role]);
              if (!present(result)) return null;
              const values = object(result.metrics),
                producedHere = Boolean(stepId && result.producerStepId === stepId);
              return (
                <div key={role} className="min-w-0 rounded-lg bg-background/80 p-3">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <h5 className="text-sm font-semibold">
                      {role === 'train' ? 'Train · 训练基线' : 'Test · 测试基线'}
                    </h5>
                    {typeof result.producerStepId === 'string' && (
                      <span className="rounded bg-muted px-2 py-0.5 text-xs">
                        {producedHere ? '本节点生成' : '复用基线'}
                      </span>
                    )}
                  </div>
                  <Values
                    rows={[
                      ['得分', values.score],
                      ['通过率', values.passRate],
                      ['Case 数量', values.caseCount],
                      ['满分', values.maxScore],
                    ]}
                  />
                  {typeof result.domainId === 'string' && (
                    <p className="mt-3 truncate text-xs text-muted-foreground" title={result.domainId}>
                      Domain：{result.domainId}
                    </p>
                  )}
                  {typeof result.producerStepId === 'string' && !producedHere && (
                    <p className="mt-2 break-all text-xs text-muted-foreground">来源 Step：{result.producerStepId}</p>
                  )}
                  <RunLink runId={result.benchRunId} />
                </div>
              );
            })}
          </div>
        </ResultCard>
      )}
      {typeof output.benchRunId === 'string' && (
        <ResultCard title="Bench 结果">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Values
              rows={[
                ['得分 / 满分', `${text(object(output.metrics).score)} / ${text(object(output.metrics).maxScore)}`],
                ['通过率', object(output.metrics).passRate],
                ['Case 数量', object(output.metrics).caseCount],
              ]}
            />
            <RunLink runId={output.benchRunId} />
          </div>
        </ResultCard>
      )}
      {present(change) && (
        <ResultCard title="本轮 Diff">
          <p className="text-sm leading-relaxed">{text(change.summary)}</p>
          {array(change.files).length > 0 && (
            <div className="mt-3 divide-y divide-border rounded-lg bg-background/80 px-3">
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
