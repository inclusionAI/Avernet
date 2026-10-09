import { cn } from '@/utils/utils';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import React from 'react';
import {
  buildCollaborationNodePresentation,
  getCollaborationNodeTone,
  type CollaborationGraphNodeData,
  type CollaborationNodeTone,
} from '../utils/collaborationGraphLayout';

interface CollaborationFlowNodeData extends CollaborationGraphNodeData {
  selected: boolean;
  highlighted: boolean;
  onSelect?: (nodeId: string) => void;
}

export type CollaborationFlowNode = Node<CollaborationFlowNodeData, 'collaboration'>;

const NODE_THEME_CLASSES: Record<
  CollaborationNodeTone,
  {
    default: string;
    highlighted: string;
    selected: string;
    badge: string;
    bot: string;
    role: string;
  }
> = {
  blue: {
    default:
      'border border-blue-300 bg-blue-50/60 hover:border-blue-400 hover:bg-blue-50',
    highlighted:
      'border border-blue-400 bg-blue-50 ring-2 ring-blue-100/80',
    selected:
      'border-2 border-blue-500 bg-blue-100/80 ring-2 ring-blue-200/60 shadow-md',
    badge: 'border-blue-200 bg-blue-500 text-white',
    bot: 'text-blue-700',
    role: 'border-blue-200 bg-white/90 text-blue-700',
  },
  green: {
    default:
      'border border-emerald-300 bg-emerald-50/60 hover:border-emerald-400 hover:bg-emerald-50',
    highlighted:
      'border border-emerald-400 bg-emerald-50 ring-2 ring-emerald-100/80',
    selected:
      'border-2 border-emerald-500 bg-emerald-100/80 ring-2 ring-emerald-200/60 shadow-md',
    badge: 'border-emerald-200 bg-emerald-500 text-white',
    bot: 'text-emerald-700',
    role: 'border-emerald-200 bg-white/90 text-emerald-700',
  },
  neutral: {
    default:
      'border border-slate-300 bg-slate-50/80 hover:border-slate-400 hover:bg-slate-100/70',
    highlighted:
      'border border-slate-400 bg-slate-100/70 ring-2 ring-slate-200/70',
    selected:
      'border-2 border-slate-500 bg-slate-100 ring-2 ring-slate-300/60 shadow-md',
    badge: 'border-slate-200 bg-slate-500 text-white',
    bot: 'text-slate-600',
    role: 'border-slate-200 bg-white/90 text-slate-600',
  },
};

export function buildNodeAriaLabel(data: CollaborationGraphNodeData) {
  const presentation = buildCollaborationNodePresentation(data);
  const markers = [
    data.isInitial ? '入口节点' : '',
    data.definition.final_output ? '最终输出节点' : '',
    data.definition.judge ? 'Judge 节点' : '',
  ].filter(Boolean);
  return [
    presentation.title,
    `预览节点 ${data.definition.node_id}`,
    ...(data.definition.execution ? [
      `Loop ${data.definition.execution.loop_id}`,
      `逻辑节点 ${data.definition.execution.definition_node_id}`,
    ] : []),
    `类型 ${presentation.kindLabel}`,
    `Bot ${presentation.botName}`,
    `角色 ${presentation.roleName}`,
    ...markers,
  ].join('，');
}

export default function CollaborationNode({ data }: NodeProps<CollaborationFlowNode>) {
  const handleSelect = () => data.onSelect?.(data.definition.node_id);
  const ariaLabel = buildNodeAriaLabel(data);
  const presentation = buildCollaborationNodePresentation(data);
  const tone = getCollaborationNodeTone(data.definition.kind);
  const theme = NODE_THEME_CLASSES[tone];
  const isUnboundBot = !!data.assigneeBinding && !data.assigneeBotId;

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={data.selected}
      aria-label={ariaLabel}
      data-loop-id={data.definition.execution?.loop_id}
      data-loop-iteration={data.definition.execution?.iteration}
      title={ariaLabel}
      onClick={handleSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleSelect();
        }
      }}
      className={cn(
        'nodrag nopan relative flex min-h-[84px] w-[210px] cursor-pointer flex-col justify-center rounded-[18px] px-4 py-3 shadow-sm outline-none transition-all',
        'focus-visible:ring-2 focus-visible:ring-slate-300 focus-visible:ring-offset-1',
        data.selected
          ? theme.selected
          : data.highlighted
          ? theme.highlighted
          : theme.default,
      )}
    >
      <span
        className={cn(
          'absolute -right-1.5 -top-2 rounded-full border px-2 py-0.5 text-[10px] font-semibold shadow-sm',
          theme.badge,
        )}
      >
        {presentation.kindLabel}
      </span>
      <Handle
        type="target"
        id="main"
        position={Position.Top}
        className="pointer-events-none opacity-0"
      />
      <Handle type="source" id="loop-return" position={Position.Right} style={{ top: '65%' }} className="pointer-events-none opacity-0" />
      <Handle type="target" id="loop-return" position={Position.Right} style={{ top: '35%' }} className="pointer-events-none opacity-0" />
      <div className="flex min-w-0 items-center justify-center gap-2 text-sm font-semibold text-slate-900">
        <span className="truncate">
          {data.definition.execution ? data.definition.display_name.trim() || data.definition.execution.definition_node_id : presentation.title}
        </span>
      </div>
      <div className="mt-3 flex min-w-0 items-center justify-between gap-3">
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-xs font-medium',
            isUnboundBot ? 'text-amber-600' : theme.bot,
          )}
          title={presentation.botName}
        >
          {presentation.botName}
        </span>
        <span
          className={cn(
            'max-w-[46%] flex-shrink-0 truncate rounded-full border px-2 py-0.5 text-[11px] font-semibold',
            theme.role,
          )}
          title={presentation.roleName}
        >
          {presentation.roleName}
        </span>
      </div>
      <Handle
        type="source"
        id="main"
        position={Position.Bottom}
        className="pointer-events-none opacity-0"
      />
    </div>
  );
}
