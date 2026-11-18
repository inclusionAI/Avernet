import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Segmented } from '@/components/ui/Segmented';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useWorkflowCreation, type WorkflowCreationSource } from '@/hooks/taskEscortParity/useWorkflowCreation';
import { useId } from 'react';
import { WorkflowCloneSource } from './WorkflowCloneSource';
import { WorkflowCreationAiPreview } from './WorkflowCreationAiPreview';

interface Props {
  context: TaskEscortParityContext;
  onClose: () => void;
}
const modes: { value: WorkflowCreationSource; label: string }[] = [
  { value: 'blank', label: '空白工作流' },
  { value: 'existing', label: '基于已有工作流' },
  { value: 'ai', label: 'AI 生成' },
];
export function WorkflowCreationDialog({ context, onClose }: Props) {
  const creation = useWorkflowCreation(context, onClose);
  const id = useId();
  const titleId = useId();
  const helpId = useId();
  const { form, update, ai } = creation;
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <ModalContent size="lg" closeLabel="关闭新建工作流">
        <ModalHeader>
          <ModalTitle>新建工作流</ModalTitle>
          <ModalDescription>
            选择创建方式；已有工作流只在画布或 YAML 中修改。创建后进入编辑器，不自动部署或执行。
          </ModalDescription>
        </ModalHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void creation.submit();
          }}
        >
          <Segmented
            value={form.source}
            options={modes.map((mode) => ({
              ...mode,
              disabledReason: creation.busy || creation.uncertain ? '正在创建，请等待或关闭弹窗' : undefined,
            }))}
            onChange={(source) => update({ source })}
            aria-label="创建方式"
          />
          {form.source === 'ai' && (
            <WorkflowCloneSource
              context={context}
              sourceId={form.aiSourceWorkflowId}
              purpose="ai"
              disabled={creation.busy || creation.uncertain}
              onSelect={(aiSourceWorkflowId) => update({ aiSourceWorkflowId })}
            />
          )}
          {form.source === 'existing' && (
            <WorkflowCloneSource
              context={context}
              sourceId={form.sourceWorkflowId}
              disabled={creation.busy || creation.uncertain}
              onSelect={(sourceWorkflowId, sourceTitle) =>
                update({ sourceWorkflowId, ...(form.title.trim() ? {} : { title: sourceTitle }) })
              }
            />
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label htmlFor={id} className="text-xs font-medium">
                工作流 ID{' '}
                <span aria-hidden className="text-destructive">
                  *
                </span>
              </label>
              <Input
                id={id}
                value={form.workflowId}
                required
                disabled={creation.busy || creation.uncertain}
                placeholder="例如 my-workflow"
                maxLength={128}
                aria-invalid={!!form.workflowId && !!creation.idError}
                aria-describedby={helpId}
                onChange={(event) => update({ workflowId: event.target.value })}
              />
              <p
                id={helpId}
                className={`text-xs ${
                  form.workflowId && creation.idError ? 'text-destructive' : 'text-muted-foreground'
                }`}
              >
                {form.workflowId && creation.idError ? creation.idError : '唯一标识，保存后不可修改'}
              </p>
            </div>
            <div className="space-y-1.5">
              <label htmlFor={titleId} className="text-xs font-medium">
                工作流名称{' '}
                <span aria-hidden className="text-destructive">
                  *
                </span>
              </label>
              <Input
                id={titleId}
                value={form.title}
                required
                disabled={creation.busy || creation.uncertain}
                placeholder="例如 我的工作流"
                onChange={(event) => update({ title: event.target.value })}
              />
            </div>
          </div>
          {form.source === 'ai' && (
            <WorkflowCreationAiPreview
              ai={ai}
              fieldsValid={!creation.idError && !!form.title.trim()}
              creating={creation.busy || creation.uncertain}
            />
          )}
          {form.source === 'existing' && form.sourceWorkflowId === form.workflowId.trim() && (
            <p role="alert" className="text-xs text-destructive">
              新工作流 ID 不能与源工作流相同。
            </p>
          )}
          {creation.error && (
            <p
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive"
            >
              {creation.error}
            </p>
          )}
          {creation.uncertain && (
            <div role="alert" className="space-y-2 rounded-md border border-input bg-muted p-3 text-xs">
              <p>创建请求的结果尚未确认，已暂停再次创建。请先刷新工作流列表确认是否已创建，不要更换 ID 盲目重试。</p>
              <Button size="sm" variant="outline" onClick={context.refreshWorkflows}>
                刷新列表确认结果
              </Button>
            </div>
          )}
          {!creation.allowed && (
            <p role="status" className="text-xs text-muted-foreground">
              请先加载当前用户身份。
            </p>
          )}
          <ModalFooter>
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" loading={creation.busy} disabled={!creation.canSubmit}>
              {creation.busy || creation.uncertain ? '创建中…' : '创建'}
            </Button>
          </ModalFooter>
        </form>
      </ModalContent>
    </Modal>
  );
}
