import type { RepairRevision, RepairTask } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { repairService } from '@/services/taskEscortParity/repairService';
import { useCallback, useState } from 'react';
import { JsonDetails, NoData, ReadSelect, ReadState, ReadTabs } from './ReadPrimitives';
import { RepairPublication } from './RepairPublication';
import { RepairTrialsPanel } from './RepairTrialsPanel';
export function RepairChanges({
  context,
  detail,
  revision,
  canEdit,
  onRefresh,
}: {
  context: TaskEscortParityContext;
  detail: RepairTask;
  revision: RepairRevision;
  canEdit: boolean;
  onRefresh: () => void;
}) {
  const [tab, setTab] = useState<'diff' | 'checks'>('diff'),
    [base, setBase] = useState<'baseline' | 'parent'>('baseline');
  const candidate = typeof revision.draft?.candidateCommit === 'string' ? revision.draft.candidateCommit : null;
  const read = useCallback(
    (signal: AbortSignal) => repairService.diff(detail.taskId, revision.revision, base, context.userId!, signal),
    [detail.taskId, revision.revision, base, context.userId],
  );
  const result = useTaskEscortEvolutionRead(
    context,
    `diff:${detail.taskId}:${revision.revision}:${base}`,
    read,
    tab === 'diff' && detail.capabilities.diff && !!candidate,
  );
  const diff = result.data;
  const completeDiff =
    !!diff &&
    tab === 'diff' &&
    base === 'baseline' &&
    candidate === diff.candidateCommit &&
    diff.baseCommit === revision.input.baseline.packCommit &&
    !diff.truncated &&
    diff.files.length > 0 &&
    diff.files.every(
      (file) => !file.truncated && (file.binary || (typeof file.patch === 'string' && !!file.patch.trim())),
    );
  return (
    <section className="space-y-4">
      <h3 className="font-semibold">本版改动</h3>
      <ReadTabs
        label="改动详情"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'diff', label: '文件差异' },
          { value: 'checks', label: '检查详情' },
        ]}
      />
      {tab === 'checks' ? (
        <>
          {revision.checks ? (
            <JsonDetails label="实际检查结果" value={revision.checks} open />
          ) : (
            <NoData text="本版未提供检查结果，不能确认检查通过" />
          )}
          {revision.error && <JsonDetails label="实际错误" value={revision.error} open />}
        </>
      ) : !detail.capabilities.diff ? (
        <p role="status" className="text-sm text-muted-foreground">
          差异能力不可用：{detail.capabilities.reason ?? '服务端未开启差异读取'}
        </p>
      ) : !candidate ? (
        <NoData text="本版尚无候选提交，无法读取文件差异" />
      ) : (
        <>
          <ReadSelect
            label="差异基准"
            value={base}
            onChange={(v) => setBase(v as 'baseline' | 'parent')}
            options={[
              { value: 'baseline', label: '冻结基线' },
              { value: 'parent', label: '父候选版本' },
            ]}
          />
          <ReadState {...result} />
          {result.data && (
            <>
              {result.data.candidateCommit !== candidate ? (
                <ReadState error="差异候选提交不匹配，已阻止展示" />
              ) : (
                <>
                  <p className="break-all text-xs text-muted-foreground">
                    {result.data.baseCommit} → {result.data.candidateCommit}
                  </p>
                  {result.data.truncated && (
                    <p role="status" className="text-sm text-warning-foreground">
                      差异已截断，不能视作完整改动。
                    </p>
                  )}
                  <div className="space-y-3">
                    {result.data.files.map((file) => (
                      <details open key={file.path} className="overflow-hidden rounded-lg border border-border">
                        <summary className="cursor-pointer break-all bg-muted/40 p-3 text-sm font-medium">
                          <span>{file.path}</span> · {file.status}
                        </summary>
                        <div className="p-3">
                          {file.binary ? (
                            <p className="text-xs text-muted-foreground">二进制文件，不提供文本差异。</p>
                          ) : file.patch ? (
                            <pre
                              aria-label={`文件差异 ${file.path}`}
                              className="max-h-[32rem] overflow-auto whitespace-pre font-mono text-xs leading-6"
                            >
                              {file.patch}
                            </pre>
                          ) : file.before !== null || file.after !== null ? (
                            <div className="grid gap-3 md:grid-cols-2">
                              <JsonDetails label="修改前（服务端原文）" value={file.before} open />
                              <JsonDetails label="修改后（服务端原文）" value={file.after} open />
                            </div>
                          ) : (
                            <p className="text-xs text-muted-foreground">
                              服务端未提供文件内容或补丁，无法显示文本差异。
                            </p>
                          )}
                          {file.truncated && <p className="mt-2 text-xs text-warning-foreground">该文件内容已截断。</p>}
                        </div>
                      </details>
                    ))}
                  </div>
                  {!result.data.files.length && <NoData text="服务端未返回文件变更" />}
                </>
              )}
            </>
          )}
        </>
      )}
      <RepairPublication
        key={JSON.stringify([revision.revision, candidate, revision.draft?.packDigest])}
        context={context}
        detail={detail}
        revision={revision}
        canEdit={canEdit}
        completeDiff={completeDiff}
        onRefresh={onRefresh}
      />
      <section aria-label="发布记录">
        <h3 className="font-semibold">实际发布记录</h3>
        {detail.release?.deployments
          .filter((d) => d.revision === revision.revision)
          .map((d) => (
            <JsonDetails
              key={d.id}
              label={`${d.id} · ${d.status}${d.receipt ? ` · 部署 #${d.receipt.deployNumber}` : ' · 无发布回执'}`}
              value={d}
            />
          ))}
        {!detail.release?.deployments.some((d) => d.revision === revision.revision) && (
          <p className="mt-2 text-sm text-muted-foreground">本版暂无发布记录。</p>
        )}
      </section>
      <RepairTrialsPanel context={context} taskId={detail.taskId} />
    </section>
  );
}
