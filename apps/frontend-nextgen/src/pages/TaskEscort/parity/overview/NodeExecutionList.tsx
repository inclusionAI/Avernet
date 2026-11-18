import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import { overviewDuration, overviewTime } from '@/domain/taskEscortParity/overview';
import type { OverviewNodeExecution } from '@/domain/taskEscortParity/overviewTypes';
import { useMemo, useRef, useState } from 'react';
import {
  Button,
  Empty,
  EvidenceValue,
  OverviewSelect,
  OverviewStatus,
  UnavailableActions,
  writeUnavailable,
} from './OverviewShared';

function tokenSummary(value: string | null) {
  if (!value) return '—';
  try {
    const data = JSON.parse(value);
    const total = data?.totalTokens ?? data?.total_tokens ?? data?.total;
    return typeof total === 'number' ? total.toLocaleString() : '查看详情';
  } catch {
    return '查看详情';
  }
}
function NodeDetail({
  nodes,
  onClose,
  onReturnFocus,
}: {
  nodes: OverviewNodeExecution[];
  onClose: () => void;
  onReturnFocus: () => void;
}) {
  const sorted = [...nodes].sort((a, b) => (b.attempt ?? 0) - (a.attempt ?? 0));
  const [id, setId] = useState(sorted[0]?.id);
  const node = sorted.find((node) => node.id === id) ?? sorted[0];
  if (!node) return null;
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent
        size="lg"
        closeLabel="关闭节点详情"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onReturnFocus();
        }}
        className="max-w-full"
      >
        <DrawerHeader>
          <DrawerTitle>节点详情 · {node.title || node.nodeId}</DrawerTitle>
          <DrawerDescription>
            {node.nodeId} · {node.runId} · 仅展示真实执行字段
          </DrawerDescription>
        </DrawerHeader>
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-3">
            <OverviewStatus status={node.status} />
            <OverviewSelect
              label="执行尝试"
              value={id}
              onChange={setId}
              options={sorted.map((node) => [node.id, `第 ${node.attempt ?? '未知'} 次尝试`])}
            />
            <span className="text-xs text-muted-foreground">{overviewDuration(node.durationMs)}</span>
          </div>
          <dl className="grid grid-cols-2 gap-3 text-xs">
            {[
              ['执行器', node.executor],
              ['触发者', node.initiator],
              ['阶段', node.phase],
              ['开始时间', overviewTime(node.startedAt)],
              ['完成时间', overviewTime(node.completedAt)],
              ['Session ID', node.sessionId],
              ['Session Key', node.sessionKey],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="mt-1 break-all">{value || '—'}</dd>
              </div>
            ))}
          </dl>
          <EvidenceValue label="输入" value={node.input} />
          <EvidenceValue label="输出" value={node.output} />
          <EvidenceValue label="错误" value={node.error} />
          <EvidenceValue label="Token 用量" value={node.tokens} />
          <EvidenceValue label="系统上下文" value={node.context} />
          <EvidenceValue label="实际 Prompt" value={node.prompt} />
          <EvidenceValue label="执行进度" value={node.progress} />
          <UnavailableActions runId={node.runId} />
          <p className="text-[11px] text-muted-foreground">{writeUnavailable}</p>
        </div>
      </DrawerContent>
    </Drawer>
  );
}

export function NodeExecutionList({ nodes }: { nodes: OverviewNodeExecution[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const latest = useMemo(() => {
    const map = new Map<string, OverviewNodeExecution>();
    for (const node of nodes)
      if (!map.has(node.nodeId) || (node.attempt ?? 0) > (map.get(node.nodeId)?.attempt ?? 0))
        map.set(node.nodeId, node);
    return [...map.values()];
  }, [nodes]);
  if (nodes.length === 0)
    return <Empty compact title="暂无节点执行记录" description="未根据工作流规格推测执行结果。" />;
  return (
    <>
      {nodes.length >= 500 && (
        <p className="p-3 text-xs text-muted-foreground">接口最多返回 500 条实际执行尝试；此处不代表完整历史。</p>
      )}
      <div className="tcov-scroll">
        <table className="tcov-table" aria-label="节点执行记录">
          <thead>
            <tr>
              {['节点', '状态', '执行器', '触发者', '阶段', '尝试', '耗时', 'Token', '开始时间', '完成时间'].map(
                (name) => (
                  <th key={name}>{name}</th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {latest.map((node) => (
              <tr key={node.id}>
                <td>
                  <Button
                    variant="link"
                    size="sm"
                    className="px-0"
                    onClick={(event) => {
                      trigger.current = event.currentTarget;
                      setSelected(node.nodeId);
                    }}
                  >
                    {node.title || node.nodeId}
                  </Button>
                  <p className="font-mono text-[10px] text-muted-foreground">{node.nodeId}</p>
                </td>
                <td>
                  <OverviewStatus status={node.status} />
                </td>
                <td>{node.executor || '—'}</td>
                <td>{node.initiator || '—'}</td>
                <td>{node.phase || '—'}</td>
                <td>{node.attempt ?? '—'}</td>
                <td>{overviewDuration(node.durationMs)}</td>
                <td>{tokenSummary(node.tokens)}</td>
                <td>{overviewTime(node.startedAt)}</td>
                <td>{overviewTime(node.completedAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && (
        <NodeDetail
          key={selected}
          nodes={nodes.filter((node) => node.nodeId === selected)}
          onClose={() => setSelected(null)}
          onReturnFocus={() => trigger.current?.focus()}
        />
      )}
    </>
  );
}
