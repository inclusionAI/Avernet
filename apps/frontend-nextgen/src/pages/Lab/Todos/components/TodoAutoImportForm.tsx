import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoBotOption, TodoSourceProject } from '@/domain/todos/models';
import { Loader2 } from 'lucide-react';
import { useState } from 'react';
import { useTodoAutoImportRules } from './useTodoAutoImportRules';

interface TodoAutoImportFormProps {
  active: boolean;
  spaceId?: number;
  projects: TodoSourceProject[];
  projectsLoading: boolean;
  projectsError: string;
  bots: TodoBotOption[];
  botId: string;
  onBotIdChange: (botId: string) => void;
  onRetryProjects: () => void;
}

export function TodoAutoImportForm({
  active,
  spaceId,
  projects,
  projectsLoading,
  projectsError,
  bots,
  botId,
  onBotIdChange,
  onRetryProjects,
}: TodoAutoImportFormProps) {
  const [selectedContainerIds, setSelectedContainerIds] = useState<string[]>([]);
  const [filterText, setFilterText] = useState('');
  const rules = useTodoAutoImportRules({ active, spaceId });

  const toggleContainer = (containerId: string, checked: boolean) => {
    setSelectedContainerIds((current) =>
      checked ? [...current, containerId] : current.filter((item) => item !== containerId),
    );
  };

  const save = async () => {
    if (!spaceId || selectedContainerIds.length === 0 || !filterText.trim() || !botId) return;
    const success = await rules.createRule({
      spaceId,
      sourceContainerIds: selectedContainerIds,
      filterText: filterText.trim(),
      botId,
    });
    if (success) {
      setSelectedContainerIds([]);
      setFilterText('');
    }
  };

  return (
    <div className="space-y-4">
      <div className="space-y-3 rounded-lg border border-border bg-muted/20 p-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="m-0 text-sm font-medium text-foreground">自动导入条件</p>
            <p className="m-0 mt-0.5 text-xs text-muted-foreground">
              外部 Agent 会读取已启用规则、扫描 Dima，并逐条回写处理进度。
            </p>
          </div>
          {projectsLoading ? <Loader2 aria-label="Dima 空间加载中" className="size-4 animate-spin" /> : null}
        </div>

        {projectsError ? (
          <div className="flex items-center justify-between gap-3 text-xs text-destructive" role="alert">
            <span>{projectsError}</span>
            <Button size="sm" variant="outline" onClick={onRetryProjects}>
              重试
            </Button>
          </div>
        ) : null}

        {!projectsLoading && projects.length === 0 && !projectsError ? (
          <p className="m-0 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
            当前账号没有可访问的 Dima 空间或项目。
          </p>
        ) : null}

        {projects.length > 0 ? (
          <fieldset className="space-y-1.5">
            <legend className="text-xs font-medium text-foreground">Dima 空间 / 项目（可多选）</legend>
            <div className="grid max-h-40 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
              {projects.map((project) => {
                const checked = selectedContainerIds.includes(project.id);
                const kindLabel = project.kind === 'workspace' ? '空间' : '项目';
                return (
                  <label
                    key={project.id}
                    className="flex cursor-pointer items-start gap-2 rounded-md border border-border bg-background px-2.5 py-2 text-xs"
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={(nextChecked) => toggleContainer(project.id, nextChecked)}
                      aria-label={`选择自动导入 Dima ${kindLabel} ${project.name}`}
                    />
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-foreground">{project.name}</span>
                      <span className="mt-0.5 block text-[11px] text-muted-foreground">
                        {kindLabel} · {project.id}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ) : null}

        <label className="block space-y-1.5 text-xs font-medium text-foreground">
          <span>过滤条件</span>
          <Textarea
            aria-label="自动导入过滤条件"
            value={filterText}
            onChange={(event) => setFilterText(event.target.value)}
            placeholder="例如：只导入需要研发处理且尚未完成的事项"
            rows={3}
          />
          <span className="block font-normal text-muted-foreground">使用中文自然语言描述，由外部 Agent 判断。</span>
        </label>

        <label className="block space-y-1.5 text-xs font-medium text-foreground">
          <span>执行 Bot</span>
          <Select value={botId || undefined} onValueChange={onBotIdChange} disabled={bots.length === 0}>
            <SelectTrigger aria-label="自动导入执行 Bot">
              <SelectValue placeholder={bots.length === 0 ? '当前空间暂无可用 Bot' : '请选择 Bot'} />
            </SelectTrigger>
            <SelectContent>
              {bots.map((bot) => (
                <SelectItem key={bot.botId} value={bot.botId}>
                  {bot.botName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        <Button
          onClick={() => void save()}
          loading={rules.saving}
          disabled={!spaceId || selectedContainerIds.length === 0 || !filterText.trim() || !botId}
        >
          保存自动导入规则
        </Button>
      </div>

      <div className="space-y-2 rounded-lg border border-border p-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="m-0 text-sm font-medium text-foreground">当前空间规则</p>
            <p className="m-0 mt-0.5 text-xs text-muted-foreground">停用后，外部 Agent 不再扫描该规则。</p>
          </div>
          {rules.loading ? <Loader2 aria-label="自动导入规则加载中" className="size-4 animate-spin" /> : null}
        </div>

        {rules.error ? (
          <div className="flex items-center justify-between gap-3 text-xs text-destructive" role="alert">
            <span>{rules.error}</span>
            <Button size="sm" variant="outline" onClick={() => void rules.refresh()}>
              重试规则
            </Button>
          </div>
        ) : null}

        {!rules.loading && rules.rules.length === 0 && !rules.error ? (
          <p className="m-0 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
            当前空间还没有自动导入规则。
          </p>
        ) : null}

        {rules.rules.map((rule) => (
          <article key={rule.ruleId} className="rounded-md border border-border bg-background p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="m-0 text-xs font-medium text-foreground">{rule.filterText}</p>
                <p className="m-0 mt-1 text-[11px] text-muted-foreground">
                  {rule.sourceContainerIds.join('、')} · {bots.find((bot) => bot.botId === rule.botId)?.botName ?? rule.botId}
                </p>
                {rule.cursorUpdatedAt ? (
                  <p className="m-0 mt-1 text-[11px] text-muted-foreground">游标：{rule.cursorUpdatedAt}</p>
                ) : null}
              </div>
              <Switch
                size="sm"
                checked={rule.status === 'enabled'}
                loading={rules.updatingRuleId === rule.ruleId}
                onCheckedChange={(checked) => void rules.setRuleEnabled(rule, checked)}
                aria-label={`${rule.status === 'enabled' ? '停用' : '启用'}自动导入规则 ${rule.filterText}`}
              />
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
