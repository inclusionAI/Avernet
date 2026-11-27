import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoAutoImportRule, TodoBotOption, TodoSourceProject } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { useEffect, useMemo, useState } from 'react';

interface TodoAutomationEditorProps {
  open: boolean;
  rule?: TodoAutoImportRule;
  projects: TodoSourceProject[];
  projectsLoading: boolean;
  bots: TodoBotOption[];
  schedulerBots: TodoBotOption[];
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (input: {
    sourceContainerIds: string[];
    filterText: string;
    botId: string;
    schedulerBotId: string;
    triggerIntervalSeconds: number;
    cronExpression: string;
  }) => Promise<boolean>;
}

const DEFAULT_TRIGGER_INTERVAL_SECONDS = 1800;
const DEFAULT_CRON_EXPRESSION = '*/30 * * * *';

const CRON_PRESETS = [
  { label: '每 30 分钟', value: '*/30 * * * *' },
  { label: '每天 8:30', value: '30 8 * * *' },
  { label: '每天 9:00', value: '0 9 * * *' },
  { label: '工作日 18:00', value: '0 18 * * 1-5' },
  { label: '工作日 9:00', value: '0 9 * * 1-5' },
  { label: '每月 1 日 8:00', value: '0 8 1 * *' },
] as const;

type ScheduleMode = 'preset' | 'custom';

function isCronShape(value: string): boolean {
  return value.trim().split(/\s+/).length === 5;
}

export function TodoAutomationEditor({
  open,
  rule,
  projects,
  projectsLoading,
  bots,
  schedulerBots,
  saving,
  onOpenChange,
  onSave,
}: TodoAutomationEditorProps) {
  const [containerIds, setContainerIds] = useState<string[]>([]);
  const [filterText, setFilterText] = useState('');
  const [botId, setBotId] = useState('');
  const [schedulerBotId, setSchedulerBotId] = useState('');
  const [triggerIntervalSeconds, setTriggerIntervalSeconds] = useState(String(DEFAULT_TRIGGER_INTERVAL_SECONDS));
  const [cronExpression, setCronExpression] = useState(DEFAULT_CRON_EXPRESSION);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>('preset');

  useEffect(() => {
    if (!open) return;
    const nextCron = rule?.cronExpression ?? DEFAULT_CRON_EXPRESSION;
    setContainerIds(rule?.sourceContainerIds ?? []);
    setFilterText(rule?.filterText ?? '');
    setBotId(rule?.botId ?? bots[0]?.botId ?? '');
    setSchedulerBotId(rule?.schedulerBotId ?? schedulerBots[0]?.botId ?? '');
    setTriggerIntervalSeconds(String(rule?.triggerIntervalSeconds ?? DEFAULT_TRIGGER_INTERVAL_SECONDS));
    setCronExpression(nextCron);
    setScheduleMode(CRON_PRESETS.some((preset) => preset.value === nextCron) ? 'preset' : 'custom');
  }, [bots, open, rule, schedulerBots]);

  const normalizedTriggerInterval = Number(triggerIntervalSeconds);
  const triggerIntervalValid =
    Number.isInteger(normalizedTriggerInterval) && normalizedTriggerInterval >= 1 && normalizedTriggerInterval <= 86_400;
  const cronValid = useMemo(() => isCronShape(cronExpression), [cronExpression]);

  const toggleProject = (projectId: string, checked: boolean) => {
    setContainerIds((current) =>
      checked ? [...new Set([...current, projectId])] : current.filter((item) => item !== projectId),
    );
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="lg" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle>{rule ? '编辑自动化' : '新建自动化'}</ModalTitle>
          <ModalDescription>当前自动化使用自然语言条件，由外部 Agent 负责实际扫描和调度。</ModalDescription>
        </ModalHeader>
        <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-foreground">Dima 空间 / 项目</legend>
            {projectsLoading ? <p className="m-0 text-xs text-muted-foreground">加载中…</p> : null}
            <div className="grid max-h-48 gap-2 overflow-y-auto sm:grid-cols-2">
              {projects.map((project) => (
                <label key={project.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-border p-3 text-xs">
                  <Checkbox
                    checked={containerIds.includes(project.id)}
                    onCheckedChange={(checked) => toggleProject(project.id, checked)}
                  />
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-foreground">{project.name}</span>
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">{project.id}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>导入条件</span>
            <Textarea
              value={filterText}
              onChange={(event) => setFilterText(event.target.value)}
              rows={4}
              placeholder="例如：导入需要研发处理且尚未完成的事项"
            />
            <span className="block text-xs font-normal text-muted-foreground">使用单一自由文本描述筛选条件。</span>
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>待办执行 Bot</span>
            <Select value={botId || undefined} onValueChange={setBotId} disabled={bots.length === 0}>
              <SelectTrigger aria-label="选择导入后待办执行 Bot">
                <SelectValue placeholder="请选择 Bot" />
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
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>定时任务执行者</span>
            <Select
              value={schedulerBotId || undefined}
              onValueChange={setSchedulerBotId}
              disabled={schedulerBots.length === 0}
            >
              <SelectTrigger aria-label="选择定时任务执行者">
                <SelectValue placeholder="请选择创建者名下的 Bot" />
              </SelectTrigger>
              <SelectContent>
                {schedulerBots.map((bot) => (
                  <SelectItem key={bot.botId} value={bot.botId}>
                    {bot.botName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="block text-xs font-normal text-muted-foreground">
              仅展示当前创建者名下的 Bot，用于执行定时扫描与导入。
            </span>
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>触发间隔（秒）</span>
            <Input
              aria-label="触发间隔（秒）"
              type="number"
              min={1}
              max={86_400}
              value={triggerIntervalSeconds}
              onChange={(event) => setTriggerIntervalSeconds(event.target.value)}
            />
            {!triggerIntervalValid ? (
              <span className="block text-xs font-normal text-destructive">请输入 1 到 86400 之间的整数。</span>
            ) : null}
          </label>
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-foreground">执行频率</legend>
            <div className="flex gap-2">
              {(['preset', 'custom'] as const).map((mode) => (
                <Button
                  key={mode}
                  size="sm"
                  variant={scheduleMode === mode ? 'default' : 'outline'}
                  onClick={() => setScheduleMode(mode)}
                >
                  {mode === 'preset' ? '常用频率' : '自定义 Cron'}
                </Button>
              ))}
            </div>
            {scheduleMode === 'preset' ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {CRON_PRESETS.map((preset) => (
                  <Button
                    key={preset.value}
                    size="sm"
                    variant="outline"
                    className={cn(
                      'justify-start',
                      cronExpression === preset.value && 'border-primary bg-primary/5 text-primary',
                    )}
                    onClick={() => setCronExpression(preset.value)}
                  >
                    {preset.label}
                  </Button>
                ))}
              </div>
            ) : (
              <Input
                aria-label="自定义 Cron"
                value={cronExpression}
                onChange={(event) => setCronExpression(event.target.value)}
                placeholder="例如：*/30 * * * *"
                className="font-mono"
              />
            )}
            <p className="m-0 text-xs text-muted-foreground">
              对应 Cron：<code className="font-mono text-foreground">{cronExpression || '—'}</code>
            </p>
            {!cronValid ? <p className="m-0 text-xs text-destructive">Cron 必须包含 5 个字段。</p> : null}
          </fieldset>
        </div>
        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            loading={saving}
            disabled={
              containerIds.length === 0 ||
              !filterText.trim() ||
              !botId ||
              !schedulerBotId ||
              !triggerIntervalValid ||
              !cronValid
            }
            onClick={() =>
              void onSave({
                sourceContainerIds: containerIds,
                filterText: filterText.trim(),
                botId,
                schedulerBotId,
                triggerIntervalSeconds: normalizedTriggerInterval,
                cronExpression: cronExpression.trim(),
              })
            }
          >
            保存
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
