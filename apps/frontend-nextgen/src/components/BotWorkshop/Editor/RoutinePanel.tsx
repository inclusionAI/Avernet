import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { BotEditorModel, BotEditorRoutine, BotEditorRoutineInput } from '@/domain/botEditor';
import type { PendingRoutineToggle } from '@/hooks/useRoutineActivation';
import {
  DEFAULT_ROUTINE_CRON,
  isRoutineSchedulePreset,
  ROUTINE_SCHEDULE_PRESETS,
} from '@/services/botWorkshop/routineSchedule';
import { cn } from '@/utils/cn';
import { Plus } from 'lucide-react';
import { useRef, useState } from 'react';
import { RoutineCard } from './RoutineCard';
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
  compact,
  pendingRoutineToggle,
  onLoadModels,
  onSave,
  onToggle,
  onDelete,
}: {
  routines: BotEditorRoutine[];
  editable: boolean;
  /** 右侧调试面板展开时主区收窄，卡片退回单列（设计稿 artboard-079）。 */
  compact?: boolean;
  /** 启停中的任务等待态：开关进入 loading，成功后原位更新，不整页刷新列表。 */
  pendingRoutineToggle?: PendingRoutineToggle;
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
    <div className="flex min-h-full flex-col bg-background">
      <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-8 lg:px-10">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold">定时任务</h2>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">
            在当前 Bot 下维护自动触发规则；执行产生的任务实例统一到「我的任务/定时任务」查看。
          </p>
        </div>
        <Button
          variant="outline"
          className="h-8 rounded-[10px] font-normal text-zinc-600"
          disabled={!editable}
          leftIcon={<Plus className="size-3.5" />}
          onClick={() => edit()}
        >
          新建任务
        </Button>
      </div>
      <div className="w-full px-6 py-6 lg:px-8">
        {routines.length ? (
          <div className={cn('grid gap-4', !compact && 'md:grid-cols-2 xl:grid-cols-3')}>
            {routines.map((item) => (
              <RoutineCard
                key={item.id}
                item={item}
                editable={editable}
                pendingRoutineToggle={pendingRoutineToggle}
                onToggle={onToggle}
                onDelete={onDelete}
                onEdit={edit}
              />
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border bg-card py-12">
            <Empty title="暂无定时任务" description="创建一个定时任务，让 Bot 自动执行周期性工作。" />
          </div>
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
