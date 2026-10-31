import type { JsonObject } from '@/domain/evolution/types';
import { array, object, present, ResultCard, text } from './OutputPrimitives';

export function DiagnosisResults({ output }: { output: JsonObject }) {
  const diagnosis = object(output.diagnosis),
    cases = object(output.cases),
    goal = object(output.goal);
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
    <>
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
          <div className="mt-3 divide-y divide-divider">
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
          <div className="divide-y divide-divider">{caseItems.slice(0, 3).map(renderCase)}</div>
          {caseItems.length > 3 && (
            <details className="mt-3 border-t border-divider pt-3">
              <summary className="cursor-pointer text-xs text-primary">查看其余 {caseItems.length - 3} 个 Case</summary>
              <div className="mt-3 divide-y divide-divider">{caseItems.slice(3).map(renderCase)}</div>
            </details>
          )}
        </section>
      )}
    </>
  );
}
