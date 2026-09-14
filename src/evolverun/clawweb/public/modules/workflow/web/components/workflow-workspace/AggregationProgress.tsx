import { Link } from 'react-router-dom';
import { useIssueGroups } from './issue-groups';

/** Independent from the already-completed single-run analysis task. */
export default function AggregationProgress({ workflowId, flowId }: { workflowId: string; flowId: string }) {
  const query = useIssueGroups(workflowId);
  const groups = query.data?.groups.filter(group => group.flowIds.includes(flowId)
    || group.summarySources?.some(source => source.flowId === flowId)) ?? [];
  return <section aria-label="聚合分析进度" className="mt-3 rounded-lg border border-slate-200 p-3 text-xs">
    <h4 className="font-semibold text-slate-800">聚合分析（独立状态）</h4>
    <p className="mt-1 text-slate-500">单次分析结果已保存；聚合失败不会覆盖上次有效结论。</p>
    {query.isLoading ? <p>正在读取聚合状态…</p> : query.isError ? <p role="alert">聚合状态加载失败，不能据此判断聚合成功或失败。</p>
      : !groups.length ? <p className="mt-2">暂无相关聚合任务；不代表已完成聚合。</p>
        : groups.map(group => <div key={group.signature} className="mt-2">
          <p>{group.signature}：{group.aggregationStatus === 'queued' ? '正在生成聚合结论'
            : group.aggregationStatus === 'completed' ? '聚合已完成'
              : group.aggregationStatus === 'failed' ? '聚合更新失败'
                : group.aggregationStatus === 'too_large' ? '输入超出上限，未生成聚合' : '尚未生成聚合'}</p>
          {group.summary && (group.stale || group.aggregationStatus === 'failed') && <p>保留上次有效结论；尚未覆盖最新分析。</p>}
        </div>)}
    <Link className="mt-2 inline-block text-blue-600" to={`/workflows/workspace?${new URLSearchParams({ workflowId, tab: 'evolution', evoTab: 'diagnosis', runId: flowId })}`}>查看聚合结论与来源</Link>
  </section>;
}
