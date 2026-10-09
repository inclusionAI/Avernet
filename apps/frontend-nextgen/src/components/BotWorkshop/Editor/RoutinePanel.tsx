import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import type { BotEditorModel, BotEditorRoutine, BotEditorRoutineInput } from '@/domain/botEditor';
import {
  DEFAULT_ROUTINE_CRON,
  getRoutineScheduleLabel,
  isRoutineSchedulePreset,
  ROUTINE_SCHEDULE_PRESETS,
} from '@/services/botWorkshop/routineSchedule';
import { Clock3, Pencil, Plus, Trash2 } from 'lucide-react';
import { useRef, useState } from 'react';
import { RoutineExecutionEnvironment } from './RoutineExecutionEnvironment';

const empty: BotEditorRoutineInput = {
  name: '',
  cron: DEFAULT_ROUTINE_CRON,
  command: '',
  enabled: true,
  timezone: 'Asia/Shanghai',
  model: undefined,
  timeoutSecs: 1800,
};

export function RoutinePanel({
  routines,
  editable,
  onLoadModels,
  onSave,
  onToggle,
  onDelete,
}: {
  routines: BotEditorRoutine[];
  editable: boolean;
  onLoadModels: () => Promise<BotEditorModel[]>;
  onSave: (input: BotEditorRoutineInput, id?: string) => Promise<void>;
  onToggle: (routine: BotEditorRoutine) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState<BotEditorRoutine>();
  const [form, setForm] = useState<BotEditorRoutineInput>(empty);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [models, setModels] = useState<BotEditorModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [modelsError, setModelsError] = useState('');
  const [scheduleMode, setScheduleMode] = useState<'preset' | 'custom'>('preset');
  const loadModels = () => {
    if (modelsLoaded || modelsLoading) return;
    setModelsLoading(true);
    setModelsError('');
    void onLoadModels()
      .then((items) => {
        setModels(items);
        setModelsLoaded(true);
      })
      .catch((error: unknown) => {
        setModelsError(error instanceof Error ? error.message : '模型列表加载失败');
      })
      .finally(() => setModelsLoading(false));
  };
  const edit = (item?: BotEditorRoutine) => {
    setEditing(item);
    setForm(
      item
        ? {
            name: item.name,
            cron: item.cron,
            command: item.command,
            enabled: item.enabled,
            timezone: item.timezone || 'Asia/Shanghai',
            model: item.model,
            timeoutSecs: item.timeoutSecs ?? 86400,
          }
        : empty,
    );
    setScheduleMode(item && !isRoutineSchedulePreset(item.cron) ? 'custom' : 'preset');
    setOpen(true);
    loadModels();
  };
  const save = async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    try {
      await onSave(form, editing?.id);
      setOpen(false);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };
  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="m-0 text-sm font-semibold">定时任务</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">
            配置执行计划；启停开关位于任务列表，编辑框只修改配置。
          </p>
        </div>
        <Button size="sm" disabled={!editable} leftIcon={<Plus className="size-4" />} onClick={() => edit()}>
          新建任务
        </Button>
      </div>
      <div className="space-y-3 px-5 py-4">
        {routines.length ? (
          routines.map((item) => (
            <section key={item.id} className="rounded-lg border border-border bg-card p-4 shadow-sm">
              <div className="flex items-start gap-3">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Clock3 className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-semibold">{item.name}</span>
                    <Badge tone={item.enabled ? 'success' : 'neutral'}>{item.enabled ? '已启用' : '已停用'}</Badge>
                  </div>
                  <p className="m-0 mt-2 line-clamp-2 text-xs leading-5 text-foreground">{item.command}</p>
                  <p className="m-0 mt-2 text-xs text-muted-foreground">
                    {getRoutineScheduleLabel(item.cron)} · {item.timezone || 'Asia/Shanghai'}
                  </p>
                  <p className="m-0 mt-1 text-xs text-muted-foreground">
                    模型：{item.model || '跟随 Bot 默认模型'} · 超时：{item.timeoutSecs ?? 86400} 秒
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Switch
                    checked={item.enabled}
                    disabled={!editable}
                    aria-label={`${item.enabled ? '停用' : '启用'}${item.name}`}
                    onCheckedChange={() => void onToggle(item)}
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={!editable}
                    aria-label={`编辑${item.name}`}
                    leftIcon={<Pencil className="size-4" />}
                    onClick={() => edit(item)}
                  />
                  <ConfirmDialog
                    title="删除定时任务"
                    description={`确认删除「${item.name}」？删除后无法恢复。`}
                    confirmVariant="destructive"
                    onConfirm={() => onDelete(item.id)}
                    disabled={!editable}
                  >
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`删除${item.name}`}
                      leftIcon={<Trash2 className="size-4" />}
                    />
                  </ConfirmDialog>
                </div>
              </div>
            </section>
          ))
        ) : (
          <Empty title="暂无定时任务" description="创建一个定时任务，让 Bot 自动执行周期性工作。" />
        )}
      </div>
      <Modal open={open} onOpenChange={(next) => !saving && setOpen(next)}>
        <ModalContent size="lg">
          <ModalHeader>
            <ModalTitle>{editing ? '编辑定时任务' : '新建定时任务'}</ModalTitle>
          </ModalHeader>
          <div className="space-y-4">
            <label className="block text-sm">
              任务名称
              <Input
                aria-label="任务名称"
                className="mt-1"
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </label>
            <div className="space-y-2">
              <span className="block text-sm">执行频率</span>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant={scheduleMode === 'preset' ? 'default' : 'outline'}
                  onClick={() => {
                    setScheduleMode('preset');
                    if (!isRoutineSchedulePreset(form.cron)) setForm({ ...form, cron: DEFAULT_ROUTINE_CRON });
                  }}
                >
                  常用频率
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={scheduleMode === 'custom' ? 'default' : 'outline'}
                  onClick={() => setScheduleMode('custom')}
                >
                  高级设置
                </Button>
              </div>
              {scheduleMode === 'preset' ? (
                <Select value={form.cron} onValueChange={(cron) => setForm({ ...form, cron })}>
                  <SelectTrigger aria-label="常用执行频率">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROUTINE_SCHEDULE_PRESETS.map((preset) => (
                      <SelectItem key={preset.value} value={preset.value}>
                        {preset.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <div>
                  <Input
                    aria-label="Cron 表达式"
                    value={form.cron}
                    onChange={(event) => setForm({ ...form, cron: event.target.value })}
                    placeholder="例如：0 9 * * 1-5"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">依次填写分钟、小时、日期、月份和星期，不支持秒。</p>
                </div>
              )}
            </div>
            <label className="block text-sm">
              执行指令
              <Textarea
                aria-label="执行指令"
                className="mt-1 min-h-28"
                value={form.command}
                onChange={(event) => setForm({ ...form, command: event.target.value })}
              />
            </label>
            <RoutineExecutionEnvironment
              form={form}
              models={models}
              loading={modelsLoading}
              error={modelsError}
              allowDefaultModel={!editing?.model}
              onChange={setForm}
            />
          </div>
          <ModalFooter>
            <Button variant="secondary" disabled={saving} onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              loading={saving}
              disabled={
                saving ||
                !form.name.trim() ||
                !form.command.trim() ||
                !form.cron.trim() ||
                !Number.isInteger(form.timeoutSecs) ||
                form.timeoutSecs < 1
              }
              onClick={() => void save().catch(() => undefined)}
            >
              保存
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}
