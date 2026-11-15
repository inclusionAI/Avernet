import { useEffect, useState } from 'react';
import DetailDrawer from '../workflow-workspace/DetailDrawer';
import { observationError, readChildRun, type ChildRunDetail } from '../../api/run-subworkflows';

export default function ChildRunDrawer({ childFlowId, parentFlowId, onClose }: {
  childFlowId: string; parentFlowId: string; onClose: () => void;
}) {
  const [detail, setDetail] = useState<ChildRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    setDetail(null); setError(null);
    const read = async () => {
      try {
        const next = await readChildRun(childFlowId, controller.signal);
        if (!controller.signal.aborted) { setDetail(next); setError(null); }
      } catch (error) {
        if (!controller.signal.aborted) { setDetail(null); setError(observationError(error)); }
      } finally { if (!controller.signal.aborted) timer = setTimeout(() => void read(), 3000); }
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [childFlowId, refresh]);
  return <DetailDrawer title={`子运行 ${childFlowId}`} onClose={onClose}
    header={<><h2 className="font-semibold text-foreground">子运行 {childFlowId}</h2>
      <p className="text-xs text-muted-foreground">父运行 {parentFlowId} · 只读</p></>}
    navigation={<nav className="flex items-center justify-between border-b border-input px-5 py-3">
      <button type="button" onClick={onClose} className="text-sm text-primary">返回父运行</button>
      <a href={`/runs/${encodeURIComponent(childFlowId)}`} className="text-sm text-primary" target="_blank" rel="noreferrer">打开完整运行</a>
    </nav>}>
    {error ? <div role="alert" className="text-sm text-destructive">{error}
      <button type="button" className="ml-2 text-primary" onClick={() => setRefresh(value => value + 1)}>重试</button></div>
      : !detail ? <p role="status" className="text-muted-foreground">正在读取子运行…</p>
      : <div className="space-y-4 text-sm text-foreground">
        <h3 className="font-semibold">{detail.run.workflow_title || detail.run.workflow_id || childFlowId}</h3>
        <p>子运行当前状态：{detail.run.status} · 子运行节点：{detail.run.node_count ?? detail.nodes.length}</p>
        <p className="text-xs text-muted-foreground">子运行节点不计入父运行节点统计。</p>
        {detail.nodes.length === 0 ? <p>暂无节点执行记录</p> : <table className="w-full text-left">
          <thead><tr className="border-b border-input"><th className="py-2">节点</th><th>状态</th></tr></thead>
          <tbody>{detail.nodes.map(node => <tr key={node.node_id} className="border-b border-input">
            <td className="py-2">{node.node_title || node.node_id}
              {node.error_text && <p className="break-words text-xs text-destructive">{node.error_text}</p>}</td><td>{node.status}</td>
          </tr>)}</tbody></table>}
      </div>}
  </DetailDrawer>;
}
