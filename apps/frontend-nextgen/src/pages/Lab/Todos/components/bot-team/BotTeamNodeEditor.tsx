import { Button, Input, Switch } from '@/components/ui';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { BotTeamNode, TodoBotOption } from '@/domain/todos/models';
import { ChevronLeft, ChevronRight, Plus, Trash2 } from 'lucide-react';

interface BotTeamNodeEditorProps {
  node?: BotTeamNode;
  nodeIndex: number;
  nodeCount: number;
  bots: TodoBotOption[];
  editable: boolean;
  onUpdate: (update: Partial<BotTeamNode>) => void;
  onMove: (direction: -1 | 1) => void;
}

function CompletionGateEditor({
  node,
  editable,
  onUpdate,
}: {
  node: BotTeamNode;
  editable: boolean;
  onUpdate: (update: Partial<BotTeamNode>) => void;
}) {
  const gates = node.completionGate.requiredArtifacts;
  const updateGate = (gateIndex: number, update: Partial<(typeof gates)[number]>) => {
    const requiredArtifacts = gates.map((item, itemIndex) => (itemIndex === gateIndex ? { ...item, ...update } : item));
    onUpdate({ completionGate: { requiredArtifacts } });
  };

  return (
    <div className="border-t border-divider pt-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <p className="m-0 text-xs font-semibold text-foreground">完成门禁</p>
          <p className="m-0 mt-0.5 text-[11px] text-muted-foreground">按产物类型、状态和最少数量判断节点是否完成。</p>
        </div>
        {editable ? (
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<Plus className="size-3.5" aria-hidden />}
            onClick={() =>
              onUpdate({
                completionGate: {
                  requiredArtifacts: [...gates, { artifactType: 'text', artifactState: 'created', minCount: 1 }],
                },
              })
            }
          >
            添加门禁
          </Button>
        ) : null}
      </div>

      {gates.length === 0 ? (
        <p className="m-0 rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">未配置产物门禁</p>
      ) : (
        <div className="space-y-2">
          {gates.map((gate, gateIndex) => (
            <div key={`${gate.artifactType}-${gateIndex}`} className="grid gap-2 md:grid-cols-[1fr_1fr_120px_32px]">
              <Input
                aria-label="产物类型"
                value={gate.artifactType}
                disabled={!editable}
                placeholder="产物类型"
                onChange={(event) => updateGate(gateIndex, { artifactType: event.target.value })}
              />
              <Input
                aria-label="产物状态"
                value={gate.artifactState}
                disabled={!editable}
                placeholder="产物状态"
                onChange={(event) => updateGate(gateIndex, { artifactState: event.target.value })}
              />
              <Input
                aria-label="最少数量"
                type="number"
                min={1}
                value={gate.minCount}
                disabled={!editable}
                onChange={(event) => updateGate(gateIndex, { minCount: Math.max(1, Number(event.target.value) || 1) })}
              />
              {editable ? (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="删除门禁"
                  onClick={() => onUpdate({ completionGate: { requiredArtifacts: gates.filter((_, index) => index !== gateIndex) } })}
                >
                  <Trash2 className="size-3.5" aria-hidden />
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function BotTeamNodeEditor({
  node,
  nodeIndex,
  nodeCount,
  bots,
  editable,
  onUpdate,
  onMove,
}: BotTeamNodeEditorProps) {
  const selectedBot = node ? bots.find((bot) => bot.botId === node.botId) : undefined;

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h3 className="m-0 text-sm font-semibold text-foreground">节点配置</h3>
        {node ? <span className="text-xs text-muted-foreground">· {node.name}</span> : null}
        {editable && node ? (
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="icon" aria-label="节点前移" disabled={nodeIndex === 0} onClick={() => onMove(-1)}>
              <ChevronLeft className="size-4" aria-hidden />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label="节点后移"
              disabled={nodeIndex === nodeCount - 1}
              onClick={() => onMove(1)}
            >
              <ChevronRight className="size-4" aria-hidden />
            </Button>
          </div>
        ) : null}
      </div>

      {node ? (
        <div className="space-y-5">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-1.5 text-xs font-medium text-foreground">
              <span>节点名称</span>
              <Input value={node.name} disabled={!editable} onChange={(event) => onUpdate({ name: event.target.value })} />
            </label>
            <label className="space-y-1.5 text-xs font-medium text-foreground">
              <span>节点 code</span>
              <Input
                value={node.code}
                disabled={!editable}
                className="font-mono"
                onChange={(event) => onUpdate({ code: event.target.value })}
              />
            </label>
            <label className="space-y-1.5 text-xs font-medium text-foreground">
              <span>执行 Bot</span>
              <Select
                value={node.botId || undefined}
                disabled={!editable || bots.length === 0}
                onValueChange={(botId) => onUpdate({ botId })}
              >
                <SelectTrigger aria-label={`节点 ${nodeIndex + 1} 执行 Bot`}>
                  <SelectValue placeholder="选择 Bot" />
                </SelectTrigger>
                <SelectContent>
                  {bots.map((bot) => (
                    <SelectItem key={bot.botId} value={bot.botId}>
                      {bot.botName}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!editable ? <span className="block text-[11px] text-muted-foreground">{selectedBot?.botName}</span> : null}
            </label>
            <div className="space-y-1.5 text-xs font-medium text-foreground">
              <span className="block">是否审批</span>
              <div className="flex h-9 items-center gap-2 rounded-md border border-input bg-background px-3">
                <Switch
                  size="sm"
                  checked={node.approvalRequired}
                  disabled={!editable}
                  onCheckedChange={(approvalRequired) => onUpdate({ approvalRequired })}
                />
                <span>{node.approvalRequired ? '需人工审批' : '无需审批'}</span>
              </div>
            </div>
          </div>
          <CompletionGateEditor node={node} editable={editable} onUpdate={onUpdate} />
        </div>
      ) : (
        <div className="py-8 text-center text-sm text-muted-foreground">暂无可配置节点</div>
      )}
    </div>
  );
}
