import { Button } from '@/components/ui/Button';
import { Spin } from '@/components/ui/Spin';
import type { JsonObject } from '@/domain/evolution/types';
import { formatTime } from '@/domain/evolution/types';
import { history, useParams } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';
import { useCallback, useState } from 'react';
import { BenchBreakdown, BenchSessionReader } from './BenchReader';
import { MarkdownReport } from './StepOutput';
import { formatCompactNumber, formatTokenUsage } from './benchToken';
import { ErrorBox, EvolutionHeader, JsonView, Section, StatusBadge, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

function BenchRunDetailContent() {
  const { benchRunId = '' } = useParams<{ benchRunId: string }>();
  const identity = useEvolutionContext();
  const evolutionApi = identity.api;
  const run = useResource(
    useCallback(() => evolutionApi.run(benchRunId), [benchRunId, identity.key]),
    identity.ready,
  );
  const cases = useResource(
    useCallback(() => evolutionApi.results(benchRunId), [benchRunId, identity.key]),
    identity.ready,
  );
  const sessions = useResource(
    useCallback(() => evolutionApi.sessions(benchRunId), [benchRunId, identity.key]),
    identity.ready,
  );
  const [session, setSession] = useState<JsonObject>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const openSession = async (artifactId: string) => {
    setBusy(true);
    setError('');
    try {
      setSession(await evolutionApi.session(benchRunId, artifactId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Session 加载失败');
    } finally {
      setBusy(false);
    }
  };
  const goBack = () =>
    run.data
      ? history.push(
          `/evolution-factory/domains/${encodeURIComponent(run.data.ownerUserId)}/${encodeURIComponent(
            run.data.domainId,
          )}`,
        )
      : history.push('/evolution-factory?tab=templates');
  const report = run.data?.summary?.reportMarkdown;
  const reportError = run.data?.summary?.reportError;
  return (
    <main className="h-full overflow-y-auto bg-background p-4 sm:p-6 lg:px-10">
      <div className="w-full min-w-0 space-y-5">
        <EvolutionHeader tab="templates" showTabs={false} />
        <header className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="icon" aria-label="返回 Domain" onClick={goBack}>
            <ArrowLeft size={18} />
          </Button>
          <h2 className="break-all text-lg font-semibold">{benchRunId}</h2>
          {run.data && <StatusBadge status={run.data.status} />}
          <Button
            className="ml-auto"
            variant="outline"
            onClick={() => {
              run.reload();
              cases.reload();
              sessions.reload();
              setSession(undefined);
            }}
          >
            刷新
          </Button>
        </header>
        <ErrorBox message={identity.error ?? run.error} retry={run.reload} />
        {run.loading && <Spin tip="加载评估结果…" />}
        {run.data && (
          <Section title="评估结果">
            <dl className="grid gap-4 text-sm sm:grid-cols-3">
              {[
                ['Domain', run.data.domainId],
                ['模板', `${run.data.templateName} · v${run.data.templateVersion}`],
                ['模型', run.data.model || '—'],
                ['分数', `${run.data.score ?? '—'} / ${run.data.maxScore ?? '—'}`],
                [
                  '通过率',
                  run.data.passRate === null || run.data.passRate === undefined
                    ? '—'
                    : `${(run.data.passRate * 100).toFixed(1)}%`,
                ],
                ['开始时间', formatTime(run.data.startedAt)],
                ['完成时间', formatTime(run.data.completedAt)],
                ['Token', formatTokenUsage(run.data.tokenUsage)],
              ].map(([key, value]) => (
                <div key={key}>
                  <dt className="text-xs text-muted-foreground">{key}</dt>
                  <dd className="mt-1">{value}</dd>
                </div>
              ))}
            </dl>
            {run.data.errorText && <p className="mt-4 text-sm text-destructive">{run.data.errorText}</p>}
          </Section>
        )}
        <Section title="分析报告">
          {typeof report === 'string' && report ? (
            <MarkdownReport content={report} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {reportError === 'report disabled'
                ? '当前未启用分析报告，评估指标、Case 和 Session 仍可查看。'
                : '暂无分析报告。'}
            </p>
          )}
          {typeof reportError === 'string' && reportError !== 'report disabled' && <ErrorBox message={reportError} />}
        </Section>
        <Section title="Case 结果">
          <ErrorBox message={cases.error} retry={cases.reload} />
          {cases.loading ? (
            <Spin tip="加载 Case…" />
          ) : (
            <div className="space-y-3">
              {cases.data?.map((item) => (
                <details key={item.resultId} className="rounded-lg border border-border p-3">
                  <summary
                    className="cursor-pointer break-words text-sm"
                    onClick={() => Tracert.click(Points.evolution.viewBenchReport, { task_id: item.taskId || undefined })}
                  >
                    {item.taskName || item.taskId} · {item.score ?? '—'} / {item.maxScore ?? '—'} ·{' '}
                    <StatusBadge status={item.status} />
                  </summary>
                  <div className="mt-3 space-y-3">
                    <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                      <span>Token：{formatTokenUsage(item.tokenUsage)}</span>
                      <span>
                        耗时：
                        {typeof item.executionTimeMs === 'number'
                          ? `${(item.executionTimeMs / 1000).toFixed(1)}s`
                          : '—'}
                      </span>
                      <span>评分方式：{item.gradingType || '—'}</span>
                    </div>
                    {item.errorText && <ErrorBox message={item.errorText} />}
                    {item.resultJson && (
                      <details>
                        <summary className="cursor-pointer text-xs text-muted-foreground">原始结果</summary>
                        <JsonView value={item.resultJson} />
                      </details>
                    )}
                    {item.breakdown && <BenchBreakdown value={item.breakdown} />}
                  </div>
                </details>
              ))}
              {!cases.error && !cases.data?.length && <p className="text-sm text-muted-foreground">暂无 Case 结果</p>}
            </div>
          )}
        </Section>
        <Section title="Session">
          <ErrorBox message={sessions.error} retry={sessions.reload} />
          <ErrorBox message={error} />
          <div className="mb-4 flex flex-wrap gap-2">
            {sessions.data?.sessions.map((item) => (
              <Button
                key={item.artifactId}
                variant="outline"
                size="sm"
                className="h-auto max-w-full whitespace-normal break-all py-2 text-left"
                disabled={busy}
                onClick={() => void openSession(item.artifactId)}
              >
                {item.taskId || item.filename || item.artifactId} · {item.eventCount ?? '—'} 事件 ·{' '}
                {formatCompactNumber(item.totalTokens)} Token
              </Button>
            ))}
          </div>
          {!sessions.loading && !sessions.error && !sessions.data?.sessions.length && (
            <p className="text-sm text-muted-foreground">暂无已上传的 Session 数据</p>
          )}
          {session && <BenchSessionReader session={session} />}
        </Section>
      </div>
    </main>
  );
}

export default function BenchRunDetail() {
  const context = useEvolutionContext();
  return <BenchRunDetailContent key={context.key} />;
}
