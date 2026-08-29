import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import type { BotEditorRoutine, BotEditorRoutineInput, BotEditorRoutineRun } from '@/domain/botEditor';
import { Play, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

const empty: BotEditorRoutineInput = {
  name: '',
  cron: '0 9 * * *',
  command: '',
  enabled: true,
  timezone: 'Asia/Shanghai',
};
export function RoutinePanel({
  routines,
  editable,
  onSave,
  onToggle,
  onDelete,
  onRun,
  runs,
  onLoadRuns,
}: {
  routines: BotEditorRoutine[];
  editable: boolean;
  onSave: (input: BotEditorRoutineInput, id?: string) => Promise<void>;
  onToggle: (routine: BotEditorRoutine) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onRun: (id: string) => Promise<void>;
  runs: BotEditorRoutineRun[];
  onLoadRuns: (id: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState<BotEditorRoutine>();
  const [form, setForm] = useState<BotEditorRoutineInput>(empty);
  const [open, setOpen] = useState(false);
  const [runsOpen, setRunsOpen] = useState(false);
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
          }
        : empty,
    );
    setOpen(true);
  };
  return (
    <div className="space-y-4 p-5 sm:p-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="m-0 text-base font-semibold">定时任务</h2>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            使用标准五段 Cron，在每次触发时创建新会话并向 Bot 发送指令。
          </p>
        </div>
        <Button disabled={!editable} leftIcon={<Plus className="size-4" />} onClick={() => edit()}>
          新建任务
        </Button>
      </div>
      {routines.length ? (
        routines.map((item) => (
          <Card key={item.id}>
            <CardContent>
              <div className="flex flex-wrap items-start gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="font-medium text-[var(--color-primary)]"
                      onClick={() => edit(item)}
                    >
                      {item.name}
                    </Button>
                    <Badge tone={item.enabled ? 'success' : 'neutral'}>{item.enabled ? '已启用' : '已停用'}</Badge>
                  </div>
                  <p className="mt-2 text-sm">{item.command}</p>
                  <div className="mt-3 text-xs text-[var(--color-muted)]">
                    Cron：{item.cron} · {item.timezone || 'Asia/Shanghai'}
                  </div>
                </div>
                <Switch checked={item.enabled} disabled={!editable} onCheckedChange={() => void onToggle(item)} />
                <Button
                  variant="secondary"
                  size="sm"
                  leftIcon={<Play className="size-3.5" />}
                  onClick={() => void onRun(item.id)}
                >
                  立即执行
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setRunsOpen(true);
                    void onLoadRuns(item.id);
                  }}
                >
                  执行记录
                </Button>
                <ConfirmDialog
                  title="删除定时任务"
                  description={`确认删除「${item.name}」？`}
                  confirmVariant="destructive"
                  onConfirm={() => onDelete(item.id)}
                  disabled={!editable}
                >
                  <Button variant="ghost" size="icon" aria-label="删除任务" leftIcon={<Trash2 className="size-4" />} />
                </ConfirmDialog>
              </div>
            </CardContent>
          </Card>
        ))
      ) : (
        <Empty
          title="暂无定时任务"
          description="创建一个 Cron 任务，让 Bot 自动执行周期性工作。"
          action={
            <Button disabled={!editable} onClick={() => edit()}>
              新建任务
            </Button>
          }
        />
      )}
      <Modal open={open} onOpenChange={setOpen}>
        <ModalContent size="lg">
          <ModalHeader>
            <ModalTitle>{editing ? '编辑定时任务' : '新建定时任务'}</ModalTitle>
          </ModalHeader>
          <div className="space-y-4">
            <label className="block text-sm">
              任务名称
              <Input className="mt-1" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="block text-sm">
              Cron 表达式
              <Input className="mt-1" value={form.cron} onChange={(e) => setForm({ ...form, cron: e.target.value })} />
            </label>
            <label className="block text-sm">
              时区
              <Input
                className="mt-1"
                value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              执行指令
              <Textarea
                className="mt-1 min-h-28"
                value={form.command}
                onChange={(e) => setForm({ ...form, command: e.target.value })}
              />
            </label>
          </div>
          <ModalFooter>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              disabled={!form.name.trim() || !form.command.trim() || !form.cron.trim()}
              onClick={() => void onSave(form, editing?.id).then(() => setOpen(false))}
            >
              保存
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
      <Modal open={runsOpen} onOpenChange={setRunsOpen}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>执行记录</ModalTitle>
          </ModalHeader>
          <div className="space-y-2">
            {runs.length ? (
              runs.map((run, index) => (
                <div key={`${run.id}-${index}`} className="flex items-center gap-3 rounded-lg border border-border p-3">
                  <Badge
                    tone={
                      run.status === 'ok' || run.status === 'completed'
                        ? 'success'
                        : run.status === 'error' || run.status === 'failed'
                        ? 'error'
                        : 'neutral'
                    }
                  >
                    {run.status || 'unknown'}
                  </Badge>
                  <span className="text-xs text-[var(--color-muted)]">
                    {run.startedAt || '时间未上报'}
                    {run.finishedAt ? ` → ${run.finishedAt}` : ''}
                  </span>
                </div>
              ))
            ) : (
              <Empty compact title="暂无执行记录" />
            )}
          </div>
        </ModalContent>
      </Modal>
    </div>
  );
}
