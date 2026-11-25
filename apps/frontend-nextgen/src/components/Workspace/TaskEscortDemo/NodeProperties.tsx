import { Input, Textarea } from '@/components/ui';
import { Settings2 } from 'lucide-react';
import { useId } from 'react';
import { DemoSelect, StatusBadge } from './DemoPrimitives';
import { DemoNode, NodeId, RunStatus } from './model';
import { TaskEscortDemoState } from './useTaskEscortDemo';

export function NodeProperties({
  node,
  nodeStatuses,
  onChange,
}: {
  node: DemoNode;
  nodeStatuses: Record<NodeId, RunStatus>;
  onChange: TaskEscortDemoState['updateNode'];
}) {
  const id = useId();
  return (
    <aside aria-label="节点属性" className="rounded-xl border border-border bg-card">
      <div className="flex items-center gap-2 border-b border-border px-3 py-3">
        <Settings2 className="size-3.5 text-muted-foreground" aria-hidden />
        <h3 className="text-xs font-medium">节点属性</h3>
        <span className="ml-auto text-[9px] text-primary">可编辑</span>
      </div>
      <div className="space-y-4 p-3">
        <div className="flex items-center justify-between gap-2">
          <code className="text-[10px] text-muted-foreground">{node.id}</code>
          <StatusBadge status={nodeStatuses[node.id]} />
        </div>
        <div>
          <label htmlFor={`${id}-name`} className="mb-1.5 block text-[11px] font-medium">
            节点名称
          </label>
          <Input
            id={`${id}-name`}
            value={node.name}
            maxLength={32}
            required
            aria-invalid={!node.name.trim()}
            onChange={(event) => onChange(node.id, { name: event.target.value })}
          />
        </div>
        <div>
          <label htmlFor={`${id}-description`} className="mb-1.5 block text-[11px] font-medium">
            节点说明
          </label>
          <Textarea
            id={`${id}-description`}
            className="min-h-28 resize-none leading-5"
            value={node.description}
            maxLength={400}
            onChange={(event) => onChange(node.id, { description: event.target.value })}
          />
        </div>
        <div>
          <span className="mb-1.5 block text-[11px] font-medium">超时配置</span>
          <DemoSelect
            label="节点超时配置"
            value={`${node.timeoutSeconds}`}
            options={[...new Set([30, 60, 120, 300, node.timeoutSeconds])]
              .sort((a, b) => a - b)
              .map((value) => ({ value: `${value}`, label: `${value} 秒` }))}
            onChange={(value) => onChange(node.id, { timeoutSeconds: Number(value) })}
            className="w-full"
          />
        </div>
        <div className="border-t border-border pt-3 text-[10px] leading-5 text-muted-foreground">
          <p>
            节点类型 <span className="float-right font-mono">{node.kind}</span>
          </p>
          <p className="mt-2">属性与 YAML 双向同步。运行状态为固定演示快照，不随草稿编辑改变。</p>
        </div>
        <p className="rounded-lg bg-primary/5 p-2.5 text-[10px] leading-5 text-primary">
          此处仅编辑本地示意编排，不修改线上配置，不执行真实审批。
        </p>
      </div>
    </aside>
  );
}
