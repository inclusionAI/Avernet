import { Badge, Button } from '@/components/ui';
import type { BotTeamNode, TodoBotOption } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { ArrowRight, Plus, Trash2 } from 'lucide-react';
import type { BotTeamEditorState } from './botTeamEditorModel';
import { emptyBotTeamNode } from './botTeamEditorModel';

interface BotTeamWorkflowProps {
  editor: BotTeamEditorState;
  bots: TodoBotOption[];
  editable: boolean;
  selectedNodeIndex: number;
  onChange: (next: BotTeamEditorState) => void;
  onSelectNode: (index: number) => void;
  onAddNodeAfter: (index: number) => void;
  onRemoveNode: (index: number) => void;
}

function BotTeamWorkflowNode({
  node,
  index,
  botNames,
  selected,
  editable,
  hasNext,
  onSelect,
  onAddAfter,
  onRemove,
}: {
  node: BotTeamNode;
  index: number;
  botNames: string[];
  selected: boolean;
  editable: boolean;
  hasNext: boolean;
  onSelect: () => void;
  onAddAfter: () => void;
  onRemove: () => void;
}) {
  return (
    <li className="flex items-center">
      <div className="relative">
        <Button
          type="button"
          variant="ghost"
          onClick={onSelect}
          className={cn(
            'h-auto w-44 justify-start rounded-xl border bg-card px-4 py-4 text-left shadow-sm transition-colors hover:bg-card',
            selected ? 'border-primary ring-2 ring-primary/10' : 'border-border hover:border-primary/50',
          )}
        >
          <span className="block min-w-0 flex-1">
            <span className="mb-3 flex items-center gap-2">
              <span
                className={cn(
                  'flex size-7 items-center justify-center rounded-full text-xs font-semibold',
                  selected ? 'bg-primary text-primary-foreground' : 'bg-primary/10 text-primary',
                )}
              >
                {index + 1}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
                {node.name || `节点 ${index + 1}`}
              </span>
            </span>
            <span className="block truncate text-xs text-muted-foreground">
              {botNames.length > 0 ? botNames.join('、') : node.botIds.join('、') || '未选择 Bot'}
            </span>
            <span className="mt-3 flex flex-wrap gap-1.5">
              {node.approvalRequired ? (
                <Badge className="border-0 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">需审批</Badge>
              ) : null}
              {node.botIds.length > 1 ? (
                <Badge className="border-0 bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {node.botIds.length} 个 Bot
                </Badge>
              ) : null}
              {node.completionGate.trim() ? (
                <Badge className="border-0 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">已配置门禁</Badge>
              ) : null}
            </span>
          </span>
        </Button>
        {editable ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`删除 ${node.name}`}
            onClick={onRemove}
            className="absolute -right-2 -top-2 size-5 rounded-full border border-border bg-card text-muted-foreground hover:bg-card hover:text-destructive"
          >
            <Trash2 className="size-3" aria-hidden />
          </Button>
        ) : null}
      </div>

      {hasNext || editable ? (
        <div className="relative flex h-8 w-16 items-center justify-center">
          <span className="absolute left-0 right-1 top-1/2 h-px bg-border" aria-hidden />
          {hasNext ? <ArrowRight className="absolute right-0 size-4 -translate-y-px text-border" aria-hidden /> : null}
          {editable ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`在 ${node.name || `节点 ${index + 1}`} 后添加节点`}
              onClick={onAddAfter}
              className="relative z-10 size-7 rounded-full border border-border bg-card text-muted-foreground hover:bg-accent hover:text-primary"
            >
              <Plus className="size-4" aria-hidden />
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function BotTeamWorkflow({
  editor,
  bots,
  editable,
  selectedNodeIndex,
  onChange,
  onSelectNode,
  onAddNodeAfter,
  onRemoveNode,
}: BotTeamWorkflowProps) {
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4">
        <h3 className="m-0 text-sm font-semibold text-foreground">工作流</h3>
        <p className="m-0 mt-1 text-xs text-muted-foreground">点选节点后，在下方配置执行 Bot、审批和完成门禁。</p>
      </div>

      <div className="app-scrollbar overflow-x-auto rounded-xl border border-dashed border-border bg-muted/10 px-5 py-7">
        <ol className="m-0 flex min-w-max list-none items-center p-0">
          {editor.nodes.map((node, index) => (
            <BotTeamWorkflowNode
              key={`${node.code}-${index}`}
              node={node}
              index={index}
              botNames={node.botIds.map((botId) => bots.find((item) => item.botId === botId)?.botName ?? botId)}
              selected={index === selectedNodeIndex}
              editable={editable}
              hasNext={index < editor.nodes.length - 1}
              onSelect={() => onSelectNode(index)}
              onAddAfter={() => onAddNodeAfter(index)}
              onRemove={() => onRemoveNode(index)}
            />
          ))}
          {editor.nodes.length === 0 && editable ? (
            <li>
              <Button
                variant="outline"
                className="border-dashed"
                leftIcon={<Plus className="size-3.5" aria-hidden />}
                onClick={() => {
                  const node = emptyBotTeamNode([], bots[0]?.botId);
                  onChange({ ...editor, nodes: [node] });
                  onSelectNode(0);
                }}
              >
                添加首个节点
              </Button>
            </li>
          ) : null}
          {editor.nodes.length === 0 && !editable ? (
            <li className="py-5 text-xs text-muted-foreground">该小队尚未配置节点</li>
          ) : null}
        </ol>
      </div>
    </div>
  );
}
