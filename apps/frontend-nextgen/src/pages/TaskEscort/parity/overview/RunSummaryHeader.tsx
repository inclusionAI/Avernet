import { overviewDuration, overviewTime } from '@/domain/taskEscortParity/overview';
import type { OverviewDetailedRun, OverviewNodeExecution } from '@/domain/taskEscortParity/overviewTypes';
import { useMemo } from 'react';
import { Button, OverviewStatus, UnavailableActions, writeUnavailable } from './OverviewShared';
import { RunLaunchDetails } from './RunLaunchDetails';

function Facts({ values }: { values: Array<[string, string | number | null | undefined]> }) {
  return (
    <dl className="run-facts-grid">
      {values.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-[11px] text-muted-foreground">{label}</dt>
          <dd className="mt-1 break-all text-xs">{value ?? '接口未提供'}</dd>
        </div>
      ))}
    </dl>
  );
}

export function RunSummaryHeader({
  run,
  nodes,
  onRefresh,
}: {
  run: OverviewDetailedRun;
  nodes: OverviewNodeExecution[];
  onRefresh: () => void;
}) {
  const latest = useMemo(() => {
    const byId = new Map<string, OverviewNodeExecution>();
    for (const node of nodes)
      if (!byId.has(node.nodeId) || (node.attempt ?? 0) > (byId.get(node.nodeId)?.attempt ?? 0))
        byId.set(node.nodeId, node);
    return [...byId.values()];
  }, [nodes]);
  const succeeded = latest.filter((node) => node.status === 'succeeded').length;
  const failed = latest.filter((node) => node.status === 'failed').length;
  const total = Math.max(run.nodeCount ?? 0, latest.length);
  return (
    <section aria-label="运行摘要" className="tcov-card p-4 sm:p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="mb-1 text-[10px] font-medium tracking-widest text-muted-foreground">WORKFLOW RUN</p>
          <h2 className="break-all text-lg font-semibold">{run.workflowTitle || run.workflowId}</h2>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{run.id}</p>
        </div>
        <OverviewStatus status={run.status} />
      </header>
      <section aria-label="运行操作" className="mt-4 space-y-2 border-b border-border pb-4">
        <div className="flex flex-wrap justify-end gap-2">
          <UnavailableActions runId={run.id} />
          <Button variant="outline" size="sm" onClick={onRefresh}>
            刷新详情
          </Button>
        </div>
        <p className="text-[11px] text-muted-foreground">{writeUnavailable}</p>
      </section>
      <section aria-label="运行身份" className="py-4">
        <h3 className="run-group-heading">
          运行身份 <small>IDENTITY</small>
        </h3>
        <Facts
          values={[
            ['工作流', run.workflowId],
            ['版本', run.version === -1 ? '未绑定发布版本' : run.version],
            ['创建者', run.initiator],
            ['发起 Bot', run.botId],
            ['Plugin 版本', run.pluginVersion],
          ]}
        />
      </section>
      <section aria-label="运行上下文" className="border-t border-border py-4">
        <h3 className="run-group-heading">
          运行上下文 <small>RUNTIME</small>
        </h3>
        <Facts
          values={[
            ['运行引擎', run.engine],
            ['device_id', run.deviceId],
            ['BOT_TYPE', run.botType],
            ['STAGE', run.stage],
          ]}
        />
        <p className="mt-3 text-[11px] text-muted-foreground">
          {(run.deviceId === null || run.deviceId === undefined) &&
          (run.botType === null || run.botType === undefined) &&
          (run.stage === null || run.stage === undefined)
            ? '服务端未提供已授权的运行元数据；不可用，不从执行器凭证或节点上下文推断。'
            : '运行元数据仅使用服务端已授权投影；未提供的字段保持不可用。'}
        </p>
      </section>
      <RunLaunchDetails run={run} />
      <div className="mt-4 flex flex-wrap items-start justify-between gap-4 border-t border-border pt-4">
        <Facts
          values={[
            ['耗时', overviewDuration(run.durationMs)],
            ['开始时间', overviewTime(run.startedAt)],
            ['完成时间', overviewTime(run.completedAt)],
          ]}
        />
        <section aria-label="运行 Token 用量" className="text-right">
          <h3 className="text-[11px] text-muted-foreground">Token 用量</h3>
          <p className="mt-1 font-mono text-base font-semibold">
            {run.totalTokens === null || run.totalTokens === undefined
              ? '接口未提供'
              : run.totalTokens.toLocaleString()}
          </p>
        </section>
      </div>
      <div className="mt-4 space-y-2">
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <span>节点进度</span>
          <span>
            {succeeded} 成功 / {failed} 失败 / {total} 总计
          </span>
        </div>
        <div
          role="progressbar"
          aria-label="实际节点进度"
          aria-valuemin={0}
          aria-valuemax={total || 1}
          aria-valuenow={succeeded + failed}
          aria-valuetext={`${succeeded} 成功 / ${failed} 失败 / ${total} 总计`}
          className="flex h-2 overflow-hidden rounded-full bg-muted"
        >
          <div className="bg-success" style={{ width: total ? `${(succeeded / total) * 100}%` : 0 }} />
          <div className="bg-destructive" style={{ width: total ? `${(failed / total) * 100}%` : 0 }} />
        </div>
        <p className="text-[11px] text-muted-foreground">
          仅计每个节点最新尝试的成功和失败；等待、运行中、阻塞不计完成。
        </p>
      </div>
    </section>
  );
}
