import { overviewTime } from '@/domain/taskEscortParity/overview';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortRunArchive } from '@/hooks/taskEscortParity/useTaskEscortRunDetail';
import { Empty, ReadFeedback } from './OverviewShared';
import { RunEvidenceValue } from './RunEvidenceValue';

export function RunArchivePanel({ context, runId }: { context: TaskEscortParityContext; runId: string }) {
  const state = useTaskEscortRunArchive(context, runId),
    archive = state.data;
  return (
    <div className="space-y-5 p-4">
      <ReadFeedback state={state} label="运行档案" />
      {!state.loading && !state.error && !archive && <Empty compact title="接口未提供运行档案" />}
      {archive && (
        <>
          <div className="flex flex-wrap justify-between gap-2 text-xs">
            <span className="font-mono">{archive.id}</span>
            <span className="text-muted-foreground">
              {archive.status ?? '状态未知'} · {overviewTime(archive.createdAt)}
            </span>
          </div>
          {archive.errors.length > 0 && <RunEvidenceValue label="档案收集错误" value={archive.errors} />}
          <section className="tcov-card p-4">
            <h3 className="mb-3 text-sm font-semibold">1. 运行过程数据</h3>
            <div className="space-y-3">
              {archive.sections.map((section) => (
                <details key={section.key} className="rounded-lg border border-border p-3">
                  <summary className="cursor-pointer text-xs font-medium">
                    {section.label}
                    {Array.isArray(section.value) ? ` (${section.value.length})` : ''}
                  </summary>
                  <div className="mt-3">
                    <RunEvidenceValue label={section.label} value={section.value} />
                  </div>
                </details>
              ))}
              {!archive.sections.length && <p className="text-xs text-muted-foreground">接口未提供运行过程数据</p>}
            </div>
          </section>
          <section className="tcov-card space-y-3 p-4">
            <h3 className="text-sm font-semibold">
              2. 错误详情{' '}
              <span className="text-xs font-normal text-muted-foreground">
                失败节点 {archive.failedNodeCount ?? '—'}
              </span>
            </h3>
            {archive.failedNodes.map((node, index) => (
              <div
                key={`${node.nodeId}:${index}`}
                className="space-y-2 rounded-lg border border-destructive/20 bg-destructive/5 p-3"
              >
                <p className="text-xs font-medium">
                  {node.title || node.nodeId} · 尝试 {node.attempt ?? '未知'}
                </p>
                <RunEvidenceValue label="实际错误" value={node.error} />
              </div>
            ))}
            {!archive.failedNodes.length && <p className="text-xs text-muted-foreground">档案未提供失败节点</p>}
            <RunEvidenceValue label="根因线索" value={archive.rootCauseHints.length ? archive.rootCauseHints : null} />
          </section>
          <section className="tcov-card space-y-3 p-4">
            <h3 className="text-sm font-semibold">3. 单次运行 AI 诊断</h3>
            {archive.analysis ? (
              <>
                <p className="text-xs text-muted-foreground">
                  来源 {archive.analysis.source ?? '未知'} · 状态 {archive.analysis.status ?? '未知'}
                </p>
                <RunEvidenceValue label="分析摘要" value={archive.analysis.summary} />
                <RunEvidenceValue label="分析错误" value={archive.analysis.error} />
                <RunEvidenceValue
                  label="诊断证据"
                  value={archive.analysis.diagnoses.length ? archive.analysis.diagnoses : null}
                />
              </>
            ) : (
              <p className="text-xs text-muted-foreground">档案未提供 AI 诊断；未自动发起分析。</p>
            )}
          </section>
          <section className="tcov-card space-y-3 p-4">
            <h3 className="text-sm font-semibold">4. 建议的工作流 YAML</h3>
            {archive.suggestedYaml ? (
              <>
                <RunEvidenceValue label="建议说明" value={archive.suggestedYaml.summary} />
                <RunEvidenceValue label="建议 YAML" value={archive.suggestedYaml.content} />
                <RunEvidenceValue label="Patch Proposal" value={archive.suggestedYaml.proposal} />
              </>
            ) : (
              <p className="text-xs text-muted-foreground">档案未提供建议 YAML；未生成示例或替代内容。</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
