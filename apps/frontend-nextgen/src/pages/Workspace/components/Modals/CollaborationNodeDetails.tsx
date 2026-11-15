import { Button } from '@/components/ui';
import { X } from 'lucide-react';
import { COLLABORATION_NODE_KIND_LABELS, type CollaborationGraphLayout } from './collaborationGraphLayout';

interface Props {
  node: CollaborationGraphLayout['nodes'][number];
  layout: CollaborationGraphLayout;
  onClose: () => void;
  onLocateBinding?: (binding: string) => void;
}

export function CollaborationNodeDetails({ node, layout, onClose, onLocateBinding }: Props) {
  const { data } = node;
  const outgoing = layout.edges.filter((edge) => edge.source === node.id);
  return (
    <section aria-label="节点详情" className="app-scrollbar max-h-[40%] shrink-0 space-y-3 overflow-y-auto border-t border-border bg-background px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="break-words text-sm font-semibold">{data.title}</h3>
          <span className="text-xs text-muted-foreground">{COLLABORATION_NODE_KIND_LABELS[data.definition.kind]}</span>
        </div>
        <Button variant="ghost" size="icon" aria-label="关闭节点详情" onClick={onClose}><X className="size-4" aria-hidden /></Button>
      </div>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 text-sm">
          <div className="break-words">{data.assigneeLabel}</div>
          <div className="break-words text-xs text-muted-foreground">
            {data.assigneeBotName || data.assigneeBotId || (data.assigneeBinding ? '待绑定 Bot' : '运行时确定执行者')}
          </div>
        </div>
        {data.assigneeBinding && onLocateBinding && <Button variant="outline" size="sm" onClick={() => onLocateBinding(data.assigneeBinding!)}>去绑定角色</Button>}
      </div>
      {outgoing.length > 0 && <ul className="space-y-1 text-xs text-muted-foreground">
        {outgoing.map((edge) => <li key={edge.id} className="break-words">
          {edge.label || edge.outcome} → {layout.nodes.find((next) => next.id === edge.target)?.data.title ?? edge.target}
        </li>)}
      </ul>}
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">技术信息</summary>
        <dl className="mt-2 space-y-1 break-all">
          <div><dt className="inline">预览节点 ID：</dt><dd className="inline">{data.definition.node_id}</dd></div>
          {data.definition.execution && <div><dt className="inline">循环路径：</dt><dd className="inline">
            {data.definition.execution.loop_path?.map((frame) => frame.loop_id).join(' / ') || data.definition.execution.loop_id}
          </dd></div>}
          {outgoing.map((edge) => <div key={edge.id}><dt className="inline">outcome：</dt><dd className="inline">{edge.outcome}</dd></div>)}
        </dl>
      </details>
    </section>
  );
}
