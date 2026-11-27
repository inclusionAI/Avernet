import { Button, Checkbox, Input, Switch, Textarea } from '@/components/ui';
import type { BotTeamNode, TodoBotOption } from '@/domain/todos/models';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface BotTeamNodeEditorProps {
  node?: BotTeamNode;
  nodeIndex: number;
  nodeCount: number;
  bots: TodoBotOption[];
  editable: boolean;
  onUpdate: (update: Partial<BotTeamNode>) => void;
  onMove: (direction: -1 | 1) => void;
}

function BotMultiSelect({
  node,
  bots,
  editable,
  onUpdate,
}: {
  node: BotTeamNode;
  bots: TodoBotOption[];
  editable: boolean;
  onUpdate: (update: Partial<BotTeamNode>) => void;
}) {
  const toggleBot = (botId: string, checked: boolean) => {
    onUpdate({
      botIds: checked
        ? [...new Set([...node.botIds, botId])]
        : node.botIds.filter((selectedBotId) => selectedBotId !== botId),
    });
  };

  return (
    <fieldset className="space-y-2 md:col-span-2">
      <legend className="text-xs font-medium text-foreground">执行 Bot（可多选）</legend>
      {bots.length === 0 ? (
        <p className="m-0 rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">
          当前空间暂无可选 Bot
        </p>
      ) : (
        <div className="grid max-h-48 gap-2 overflow-y-auto rounded-lg border border-border p-2 sm:grid-cols-2">
          {bots.map((bot) => {
            const checked = node.botIds.includes(bot.botId);
            return (
              <label
                key={bot.botId}
                className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-2 text-xs hover:bg-muted/40"
              >
                <Checkbox
                  checked={checked}
                  disabled={!editable}
                  onCheckedChange={(nextChecked) => toggleBot(bot.botId, nextChecked)}
                  aria-label={`选择执行 Bot ${bot.botName}`}
                />
                <span className="min-w-0">
                  <span className="block truncate font-medium text-foreground">{bot.botName}</span>
                  <span className="block truncate text-[11px] text-muted-foreground">{bot.botId}</span>
                </span>
              </label>
            );
          })}
        </div>
      )}
      <p className="m-0 text-[11px] text-muted-foreground">
        已选择 {node.botIds.length} 个 Bot；该节点会把全部候选 Bot 写入小队协议。
      </p>
    </fieldset>
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
            <BotMultiSelect node={node} bots={bots} editable={editable} onUpdate={onUpdate} />
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
          <label className="block space-y-1.5 border-t border-divider pt-4 text-xs font-medium text-foreground">
            <span>完成门禁</span>
            <Textarea
              aria-label="完成门禁"
              value={node.completionGate}
              disabled={!editable}
              rows={4}
              placeholder="例如：代码已提交，自动化测试通过，并给出验证结论"
              onChange={(event) => onUpdate({ completionGate: event.target.value })}
            />
            <span className="block text-[11px] font-normal text-muted-foreground">
              使用一段文本描述节点完成条件，不再逐条配置产物类型和状态。
            </span>
          </label>
        </div>
      ) : (
        <div className="py-8 text-center text-sm text-muted-foreground">暂无可配置节点</div>
      )}
    </div>
  );
}
