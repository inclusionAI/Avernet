import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { HELP_SOURCES, type HelpBoardDraft } from '@/domain/communityHelp/types';

export function HelpBoardSources({
  draft,
  onChange,
}: {
  draft: HelpBoardDraft;
  onChange: (draft: HelpBoardDraft) => void;
}) {
  const config = draft.sourceConfig;
  return (
    <fieldset className="space-y-3">
      <legend className="mb-2 text-xs font-medium">
        任务来源 <span className="text-destructive">*</span>
      </legend>
      <div className="flex flex-wrap gap-4">
        {HELP_SOURCES.map((source) => (
          <label key={source} className="flex items-center gap-2 text-xs">
            <Checkbox
              checked={draft.sources.includes(source)}
              onCheckedChange={(checked) =>
                onChange({
                  ...draft,
                  sources: checked ? [...draft.sources, source] : draft.sources.filter((item) => item !== source),
                })
              }
            />
            {source}
          </label>
        ))}
      </div>
      {draft.sources.includes('Dima') && (
        <div className="rounded-xl border border-border bg-muted/30 p-3">
          <label className="block space-y-2 text-xs font-medium">
            <span>
              Dima 项目空间 <span className="text-destructive">*</span>
            </span>
            <Input
              required
              maxLength={120}
              value={config.dima.projectSpace}
              onChange={(event) =>
                onChange({ ...draft, sourceConfig: { ...config, dima: { projectSpace: event.target.value } } })
              }
              placeholder="输入 Dima 项目空间名称或 ID"
            />
          </label>
        </div>
      )}
      {draft.sources.includes('Task Discover') && (
        <div className="space-y-3 rounded-xl border border-border bg-muted/30 p-3">
          <label className="block space-y-2 text-xs font-medium">
            <span>
              Task Discover Bot <span className="text-destructive">*</span>
            </span>
            <Input
              required
              maxLength={80}
              value={config.taskDiscover.botName}
              onChange={(event) =>
                onChange({
                  ...draft,
                  sourceConfig: { ...config, taskDiscover: { botName: event.target.value } },
                })
              }
              placeholder="输入发现任务的 Bot 名称"
            />
          </label>
          <p className="text-xs leading-5 text-muted-foreground">
            用于标记任务发现来源，独立于下方协作 Bot，不限定为圈选成员。
          </p>
        </div>
      )}
      {draft.sources.includes('手动输入') && (
        <p className="text-xs leading-5 text-muted-foreground">
          支持用户直接输入求助内容，创建时可选自动同步到 Dima 项目。
        </p>
      )}
      <p className="text-xs leading-5 text-muted-foreground">
        新任务使用当前来源配置，已有任务保留来源快照；此处不连接外部服务。
      </p>
    </fieldset>
  );
}
