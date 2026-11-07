import { useEffect, useState } from 'react';
import { useSubworkflows } from '../../hooks/useSubworkflows';
import ChildRunDrawer from './ChildRunDrawer';

export default function SubworkflowPanel({ flowId, nodeId, groupExecutionId }: {
  flowId: string; nodeId: string; groupExecutionId: string;
}) {
  const { data, loading, error, paging, loadMore, retry } = useSubworkflows(flowId, nodeId, groupExecutionId);
  const [childFlowId, setChildFlowId] = useState<string | null>(null);
  useEffect(() => { setChildFlowId(null); }, [flowId, nodeId, groupExecutionId]);
  useEffect(() => { if (error) setChildFlowId(null); }, [error]);
  return <section aria-label="子工作流组" className="space-y-3 rounded-lg border border-input bg-card p-4 text-sm text-foreground">
    <header className="flex items-center justify-between"><h3 className="font-semibold">子工作流组</h3>
      <span className="text-xs text-muted-foreground">只读 · 展开时自动刷新</span></header>
    {loading && <p role="status" className="text-muted-foreground">正在读取子工作流…</p>}
    {error && <div role="alert" className="text-destructive">{error}
      <button type="button" className="ml-2 text-primary" onClick={retry}>重试</button></div>}
    {data && <>
      <p className="text-xs text-muted-foreground">以下状态和统计来自组快照；父流程停止后，子运行可能继续执行。打开子运行可查看当前状态。</p>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span>状态 <strong data-testid="fanout-status">{data.status}</strong></span><span>{data.successPolicy}</span>
        <span>并发 {data.activeCount} / {data.concurrency}</span><span>revision {data.revision}</span>
      </div>
      <p className="text-xs text-muted-foreground">{data.countScope === 'visible' ? '仅可见项统计；组总数不公开' : `组总数 ${data.groupTotal}`}
        {` · 可见 ${data.visibleTotal} · 已加载 ${data.children.length}`}</p>
      <div className="flex flex-wrap gap-3">
        <span>成功 {data.counts.successCount}</span><span>失败 {data.counts.failedCount}</span><span>待完成 {data.counts.pendingCount}</span>
        <span>取消 {data.counts.cancelledCount}</span><span>超时 {data.counts.timedOutCount}</span><span>拒绝 {data.counts.rejectedCount}</span>
      </div>
      {data.children.length === 0 ? <p className="text-muted-foreground">暂无可见子运行</p> : <ul className="divide-y divide-input">
        {data.children.map(child => <li key={child.childFlowId} className="space-y-1 py-3">
          <div className="flex flex-wrap items-center gap-3"><span>{child.displayIndex + 1}. {child.itemId}</span>
            <span className="rounded bg-muted px-2 py-0.5 text-xs">{child.status}</span><span className="text-xs text-muted-foreground">attempt {child.attemptNumber}</span>
            <button type="button" aria-label={`查看 ${child.childFlowId}`} onClick={() => setChildFlowId(child.childFlowId)}
              className="break-all text-primary underline underline-offset-2">{child.childFlowId}</button></div>
          {child.error && <div className="text-xs text-destructive"><span>{child.error.category} / {child.error.code}</span>
            <p className="break-words">{child.error.message}</p></div>}
        </li>)}
      </ul>}
      {data.nextCursor && <button type="button" onClick={() => void loadMore()} disabled={paging}
        className="rounded border border-input bg-secondary px-3 py-1.5 text-secondary-foreground disabled:opacity-50">{paging ? '读取中…' : '加载更多'}</button>}
    </>}
    {childFlowId && <ChildRunDrawer key={childFlowId} childFlowId={childFlowId} parentFlowId={flowId} onClose={() => setChildFlowId(null)} />}
  </section>;
}
