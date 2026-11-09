import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { createHelpSourceDetails, helpSourceContext } from '@/domain/communityHelp/sources';
import type { HelpBoard, HelpSource, HelpTaskDraft } from '@/domain/communityHelp/types';

export function HelpTaskSourceFields({
  board,
  draft,
  onChange,
}: {
  board: HelpBoard;
  draft: HelpTaskDraft;
  onChange: (draft: HelpTaskDraft) => void;
}) {
  const manual = draft.source === '手动输入';
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <label htmlFor="help-task-source" className="text-xs font-medium">
            任务来源 <span className="text-destructive">*</span>
          </label>
          <Select
            value={draft.source}
            onValueChange={(value) =>
              onChange({
                ...draft,
                source: value as HelpSource,
                sourceRef: '',
                syncToDima: false,
                dimaProjectSpace: '',
              })
            }
          >
            <SelectTrigger id="help-task-source">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {board.sources.map((source) => (
                <SelectItem key={source} value={source}>
                  {source}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {!manual && (
          <label className="space-y-2 text-xs font-medium">
            <span>来源任务编号（选填）</span>
            <Input
              maxLength={120}
              value={draft.sourceRef}
              onChange={(event) => onChange({ ...draft, sourceRef: event.target.value })}
              placeholder="例如：DIMA-1042 / TD-2086"
            />
          </label>
        )}
      </div>
      <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-3 text-xs leading-6">
        <p className="break-all text-muted-foreground">
          {helpSourceContext(createHelpSourceDetails(draft.source, board.sourceConfig))}
        </p>
        {manual && (
          <>
            <label className="flex cursor-pointer items-center gap-2 font-medium">
              <Checkbox
                checked={draft.syncToDima}
                onCheckedChange={(checked) =>
                  onChange({
                    ...draft,
                    syncToDima: checked,
                    dimaProjectSpace: checked ? board.sourceConfig.dima.projectSpace : '',
                  })
                }
              />
              自动同步到 Dima 项目
            </label>
            {draft.syncToDima && (
              <label className="block space-y-2 font-medium">
                <span>
                  同步目标 Dima 项目空间 <span className="text-destructive">*</span>
                </span>
                <Input
                  required
                  maxLength={120}
                  value={draft.dimaProjectSpace}
                  onChange={(event) => onChange({ ...draft, dimaProjectSpace: event.target.value })}
                  placeholder="输入目标项目空间名称或 ID"
                />
              </label>
            )}
          </>
        )}
        <p className="text-muted-foreground">
          {manual && draft.syncToDima
            ? '仅保存同步意愿与目标，创建后显示模拟待同步，不会实际写入 Dima。'
            : '仅保存任务及来源信息，不读取或同步外部任务。'}
        </p>
      </div>
    </div>
  );
}
