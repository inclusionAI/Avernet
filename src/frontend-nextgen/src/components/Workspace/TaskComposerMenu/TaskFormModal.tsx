// @asset-migrated: teamclaw 自研
/** 任务发起表单 Modal（动态任务 / 工作流任务）。被 ComposerCapabilitiesMenu 选用。 */
import {
  Button,
  Input,
  Modal,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from '@/components/ui';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import type { TaskComposerForm } from '@/services/tasks/taskMapper';
import { useEffect, useState } from 'react';

const EMPTY_FORM: TaskComposerForm = {
  title: '',
  instruction: '',
  objective: '',
  acceptances: [''],
  taskType: 'dynamic',
  workflowId: undefined,
};

export interface TaskFormModalProps {
  open: boolean;
  onClose: () => void;
  execution: UseTaskExecutionResult;
  initialTaskType?: 'dynamic' | 'workflow';
}

export function TaskFormModal({ open, onClose, execution, initialTaskType = 'dynamic' }: TaskFormModalProps) {
  const [form, setForm] = useState<TaskComposerForm>({ ...EMPTY_FORM, taskType: initialTaskType });
  const [acceptances, setAcceptances] = useState<string[]>(['']);

  // Modal 常驻挂载、靠 open 控制显隐：每次打开按当前 initialTaskType 重置表单，
  // 否则点「工作流任务」后 taskType 仍停留首次挂载的 'dynamic'，下拉不出现。
  useEffect(() => {
    if (open) {
      setForm({ ...EMPTY_FORM, taskType: initialTaskType });
      setAcceptances(['']);
    }
  }, [open, initialTaskType]);

  const reason = execution.validate(form) ?? (execution.submitting ? '任务正在提交中' : null);
  const canSubmit = !execution.submitting && !execution.validate(form);

  const close = () => {
    onClose();
  };
  const reset = () => {
    setForm(EMPTY_FORM);
    setAcceptances(['']);
  };

  const handleSubmit = async () => {
    const res = await execution.submit({ ...form, acceptances });
    if (res.ok) {
      reset();
      close();
    }
  };

  return (
    <Modal open={open} onOpenChange={(o) => !o && close()}>
      <ModalContent size="md">
        <ModalHeader>
          <ModalTitle>{form.taskType === 'workflow' ? '工作流任务' : '动态任务'}</ModalTitle>
        </ModalHeader>
        <div className="flex flex-col gap-3 px-6 pb-2">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">任务标题 *</span>
            <Input
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              placeholder="例如：存储行业尽调"
              maxLength={60}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">任务目标 *</span>
            <Input
              value={form.objective}
              onChange={(e) => setForm((f) => ({ ...f, objective: e.target.value }))}
              placeholder="一句话目标"
              maxLength={120}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">任务描述</span>
            <Textarea
              value={form.instruction}
              onChange={(e) => setForm((f) => ({ ...f, instruction: e.target.value }))}
              placeholder="补充背景与要求"
              rows={2}
            />
          </label>
          {form.taskType === 'workflow' && (
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">选择 Workflow *</span>
              <Select
                value={form.workflowId ?? ''}
                onValueChange={(v) => setForm((f) => ({ ...f, workflowId: v }))}
                disabled={execution.workflowsLoading || execution.workflows.length === 0}
              >
                <SelectTrigger>
                  <SelectValue placeholder={execution.workflowsLoading ? '加载工作流列表…' : '选择一个工作流'} />
                </SelectTrigger>
                <SelectContent>
                  {execution.workflows.map((w) => (
                    <SelectItem key={w.workflowId} value={w.workflowId}>
                      <span className="font-medium">{w.title}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!execution.workflowsLoading && execution.workflows.length === 0 && (
                <span className="text-destructive">未加载到工作流，请确认会话所属 Bot 可用工作流</span>
              )}
            </label>
          )}
          <div className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">验收标准</span>
            <div className="flex flex-col gap-1.5">
              {acceptances.map((a, i) => (
                <Input
                  key={i}
                  value={a}
                  onChange={(e) => setAcceptances((l) => l.map((it, idx) => (idx === i ? e.target.value : it)))}
                  placeholder={`验收项 ${i + 1}`}
                />
              ))}
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setAcceptances((l) => [...l, ''])}
              className="mt-1 self-start text-primary"
            >
              + 添加验收项
            </Button>
          </div>
          {reason && <p className="text-xs text-destructive">{reason}</p>}
        </div>
        <ModalFooter className="px-6">
          <Button variant="secondary" size="sm" onClick={close} disabled={execution.submitting}>
            取消
          </Button>
          <Button size="sm" onClick={handleSubmit} loading={execution.submitting} disabled={!canSubmit}>
            提交并打开副屏
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
