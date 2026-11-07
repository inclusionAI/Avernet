import { Button } from '@/components/ui/Button';
import { Pagination } from '@/components/ui/Pagination';
import { Spin } from '@/components/ui/Spin';
import type { BenchRun } from '@/domain/evolution/types';
import { formatTime } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { displayValue, elapsed } from './presentation';
import { ErrorBox, Hint, StatusBadge } from './shared';

export function DomainRuns({
  data,
  loading,
  error,
  reload,
  page,
  onPage,
}: {
  data?: { runs: BenchRun[]; total: number };
  loading: boolean;
  error: string;
  reload: () => void;
  page: number;
  onPage: (page: number) => void;
}) {
  return (
    <div className="space-y-4">
      <ErrorBox message={error} retry={reload} />
      {loading ? (
        <Spin tip="加载关联任务…" />
      ) : (
        data && (
          <>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full min-w-[1900px] table-fixed text-left text-xs">
                <colgroup>
                  {[240, 90, 240, 70, 100, 100, 90, 100, 160, 80, 160, 170, 170, 100, 160].map((width, i) => (
                    <col key={i} style={{ width }} />
                  ))}
                </colgroup>
                <thead className="bg-muted text-muted-foreground">
                  <tr>
                    {[
                      '运行 ID',
                      '范围',
                      '模板',
                      '版本',
                      '状态',
                      '得分',
                      '通过率',
                      'Token',
                      '模型',
                      '套件',
                      '场景',
                      '开始时间',
                      '完成时间',
                      '耗时',
                      'ClawMind',
                    ].map((label) => (
                      <th className="whitespace-nowrap p-3 font-medium" key={label}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.runs.map((run) => {
                    const scope =
                      run.runScope ??
                      (run.templateName === '__domain__' && run.templateVersion === 0 ? 'domain' : 'template');
                    const template =
                      scope === 'domain' ? `Domain Run（${run.templateCount ?? '—'} 个模板）` : run.templateName;
                    return (
                      <tr className="border-t border-divider" key={run.benchRunId}>
                        <td className="p-3">
                          <Hint content={run.benchRunId}>
                            <Button
                              variant="ghost"
                              style={{ height: 'auto', whiteSpace: 'normal' }}
                              type="button"
                              className="block w-full truncate text-left font-mono text-primary hover:underline"
                              onClick={() =>
                                history.push(`/evolution-factory/bench/runs/${encodeURIComponent(run.benchRunId)}`)
                              }
                            >
                              {run.benchRunId}
                            </Button>
                          </Hint>
                        </td>
                        <td className="whitespace-nowrap p-3">
                          <span
                            className={`rounded px-2 py-0.5 ${
                              scope === 'domain'
                                ? 'bg-[hsl(var(--purple)/0.08)] text-[hsl(var(--purple))]'
                                : 'bg-muted text-muted-foreground'
                            }`}
                          >
                            {scope}
                          </span>
                        </td>
                        <td className="p-3">
                          <Hint content={template}>
                            <div className="truncate">{template}</div>
                          </Hint>
                        </td>
                        <td className="whitespace-nowrap p-3">
                          {scope === 'template' ? `v${run.templateVersion}` : '—'}
                        </td>
                        <td className="whitespace-nowrap p-3">
                          <StatusBadge status={run.status} />
                        </td>
                        <td className="whitespace-nowrap p-3">
                          {displayValue(run.score)} / {displayValue(run.maxScore)}
                        </td>
                        <td className="whitespace-nowrap p-3">
                          {run.passRate === null || run.passRate === undefined
                            ? '—'
                            : `${(run.passRate * 100).toFixed(1)}%`}
                        </td>
                        <td className="whitespace-nowrap p-3">{displayValue(run.tokenUsage?.totalTokens)}</td>
                        <td className="p-3">
                          <Hint content={run.model || undefined}>
                            <div className="truncate">{run.model || '—'}</div>
                          </Hint>
                        </td>
                        <td className="p-3">
                          <Hint content={run.suite || undefined}>
                            <div className="truncate">{run.suite || '—'}</div>
                          </Hint>
                        </td>
                        <td className="p-3">
                          <Hint content={run.scene || undefined}>
                            <div className="truncate">{run.scene || '—'}</div>
                          </Hint>
                        </td>
                        <td className="whitespace-nowrap p-3">{formatTime(run.startedAt)}</td>
                        <td className="whitespace-nowrap p-3">{formatTime(run.completedAt)}</td>
                        <td className="whitespace-nowrap p-3">{elapsed(run.startedAt, run.completedAt)}</td>
                        <td className="p-3">
                          <Hint content={run.clawmindFlowId || undefined}>
                            <div className="truncate">{run.clawmindFlowId || '—'}</div>
                          </Hint>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!data.runs.length && (
              <p className="py-8 text-center text-sm text-muted-foreground">暂无关联的 Bench 运行</p>
            )}
            <Pagination current={page} total={data.total} pageSize={20} onChange={onPage} />
          </>
        )
      )}
    </div>
  );
}
