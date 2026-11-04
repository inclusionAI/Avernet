import { RoutineExecutionEnvironment } from '@/components/BotWorkshop/Editor/RoutineExecutionEnvironment';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { BotEditorModel } from '@/domain/botEditor';
import {
  DEFAULT_ROUTINE_CRON,
  isRoutineSchedulePreset,
  ROUTINE_SCHEDULE_PRESETS,
} from '@/services/botWorkshop/routineSchedule';
import {
  stageNames,
  validateCreateTarget,
  routineInstanceMode,
  routineInstanceIds,
  type ManagedRoutine,
  type RoutineContext,
  type RoutineForm,
  type RoutineStage,
} from '@/services/botScheduledTasks/types';
import { useEffect, useRef, useState } from 'react';

export function RoutineFormDialog({
  context,
  original,
  editable,
  onClose,
  onSave,
  onLoadModels,
}: {
  context: RoutineContext;
  original?: ManagedRoutine;
  editable: boolean;
  onClose: () => void;
  onSave: (form: RoutineForm) => Promise<void>;
  onLoadModels: () => Promise<BotEditorModel[]>;
}) {
  const [form, setForm] = useState<RoutineForm>(() =>
    original
      ? { ...original, timezone: original.timezone || 'Asia/Shanghai' }
      : {
          name: '',
          command: '',
          cron: DEFAULT_ROUTINE_CRON,
          enabled: true,
          timezone: 'Asia/Shanghai',
          timeoutSecs: 1800,
          runtime_stage: context.caller_mode ? 'online' : undefined,
        },
  );
  const [customCron, setCustomCron] = useState(!isRoutineSchedulePreset(form.cron));
  const [models, setModels] = useState<BotEditorModel[]>([]);
  const [modelsError, setModelsError] = useState('');
  const [modelsLoading, setModelsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void onLoadModels()
      .then((items) => {
        if (active) setModels(items);
      })
      .catch((reason: unknown) => {
        if (active) setModelsError(reason instanceof Error ? reason.message : '模型列表加载失败');
      })
      .finally(() => {
        if (active) setModelsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onLoadModels]);
  const instanceMode = routineInstanceMode(context, form.runtime_stage);
  const instanceIds = routineInstanceIds(context, form.runtime_stage);
  useEffect(() => {
    if (!original && context.caller_mode && form.runtime_stage !== 'online')
      setForm((current) => ({ ...current, runtime_stage: 'online', device_uuid: undefined, operator_id: undefined }));
  }, [original, context.caller_mode, form.runtime_stage]);
  let targetError = '';
  try {
    if (!original) validateCreateTarget(context, form, 'current-user');
  } catch (reason) {
    targetError = (reason as Error).message;
  }
  const valid =
    form.name.trim() &&
    form.command.trim() &&
    form.cron.trim() &&
    Number.isInteger(form.timeoutSecs) &&
    form.timeoutSecs > 0 &&
    !targetError;
  async function save() {
    if (savingRef.current || !editable || !valid) return;
    savingRef.current = true;
    setSaving(true);
    setError('');
    try {
      await onSave(form);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败，输入已保留');
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>{original ? '编辑定时任务' : '创建定时任务'}</ModalTitle>
        </ModalHeader>
        <fieldset disabled={!editable || saving} className="m-0 min-w-0 space-y-4 border-0 p-0">
          <label className="block text-sm">
            任务名称
            <Input
              aria-label="任务名称"
              className="mt-1"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          {context.bot_type === 'service' && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1 text-sm">
                <span>生效环境</span>
                <Select
                  value={form.runtime_stage || ''}
                  disabled={Boolean(original) || !editable || saving}
                  onValueChange={(value) =>
                    setForm({
                      ...form,
                      runtime_stage: value as RoutineStage,
                      device_uuid: undefined,
                      operator_id: undefined,
                    })
                  }
                >
                  <SelectTrigger aria-label="生效环境">
                    <SelectValue placeholder="请选择生效环境" />
                  </SelectTrigger>
                  <SelectContent>
                    {(['draft', 'verify', 'online'] as const).map((stage) => (
                      <SelectItem key={stage} value={stage} disabled={context.caller_mode && stage !== 'online'}>
                        {stageNames[stage]}
                        {context.caller_mode && stage !== 'online' ? '（Caller 模式不支持）' : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {instanceMode === 'multiple' && (
                <div className="space-y-1 text-sm">
                  <span>运行实例</span>
                  <Select
                    value={form.device_uuid || ''}
                    disabled={Boolean(original) || !editable || saving}
                    onValueChange={(device_uuid) => setForm({ ...form, device_uuid })}
                  >
                    <SelectTrigger aria-label="运行实例">
                      <SelectValue placeholder="请选择运行实例" />
                    </SelectTrigger>
                    <SelectContent>
                      {[...new Set([...instanceIds, ...(original?.device_uuid ? [original.device_uuid] : [])])].map(
                        (uid) => (
                          <SelectItem key={uid} value={uid}>
                            {uid}
                          </SelectItem>
                        ),
                      )}
                    </SelectContent>
                  </Select>
                </div>
              )}
              {!original && form.runtime_stage && instanceMode !== 'unknown' && targetError && (
                <p className="text-xs text-destructive sm:col-span-2" role="status">
                  {targetError}
                </p>
              )}
              {original && (
                <p className="text-xs text-muted-foreground sm:col-span-2">
                  执行目标已固定。更换环境或实例请创建新任务，避免改动其他目标上的同名任务。
                </p>
              )}
            </div>
          )}
          <div className="space-y-2 text-sm">
            <span>执行频率</span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={!customCron ? 'default' : 'outline'}
                onClick={() => {
                  setCustomCron(false);
                  if (!isRoutineSchedulePreset(form.cron)) setForm({ ...form, cron: DEFAULT_ROUTINE_CRON });
                }}
              >
                常用频率
              </Button>
              <Button size="sm" variant={customCron ? 'default' : 'outline'} onClick={() => setCustomCron(true)}>
                高级设置
              </Button>
            </div>
            {customCron ? (
              <>
                <Input
                  aria-label="Cron 表达式"
                  value={form.cron}
                  onChange={(e) => setForm({ ...form, cron: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">依次填写分钟、小时、日期、月份、星期，不支持秒。</p>
              </>
            ) : (
              <Select
                value={form.cron}
                disabled={!editable || saving}
                onValueChange={(cron) => setForm({ ...form, cron })}
              >
                <SelectTrigger aria-label="常用执行频率">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROUTINE_SCHEDULE_PRESETS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <label className="block text-sm">
            执行指令
            <Textarea
              aria-label="执行指令"
              className="mt-1 min-h-28"
              value={form.command}
              onChange={(e) => setForm({ ...form, command: e.target.value })}
            />
          </label>
          <RoutineExecutionEnvironment
            form={form}
            models={models}
            loading={modelsLoading}
            error={modelsError}
            allowDefaultModel={!original?.model}
            onChange={(value) => setForm({ ...form, ...value })}
          />
        </fieldset>
        {!editable && !saving && (
          <p className="text-sm text-destructive" role="alert">
            当前无法编辑，请到 Bot 列表确认权限及编辑锁后再保存。当前输入已保留。
          </p>
        )}
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <ModalFooter>
          <Button variant="secondary" disabled={saving} onClick={onClose}>
            取消
          </Button>
          <Button loading={saving} disabled={!valid || !editable || saving} onClick={() => void save()}>
            保存
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
