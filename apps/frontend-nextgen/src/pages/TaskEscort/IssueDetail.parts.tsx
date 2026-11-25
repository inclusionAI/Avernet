import { useDetailFocusRestore } from '@/hooks/taskEscortWorkspace/useDetailFocus';
import { FileCode2 } from 'lucide-react';
import type { Issue } from './types';
import { Badge, Button, Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle, Switch } from './ui';
export function CodeComparison({ issue }: { issue: Issue }) {
  return (
    <section aria-label="修复前后代码对比" className="overflow-hidden rounded-lg border border-border">
      <div className="flex items-center gap-2 border-b border-border bg-muted/30 px-4 py-3 text-sm font-medium">
        <FileCode2 className="size-4 text-muted-foreground" />
        建议变更预览
        <Badge tone="outline" className="ml-auto text-[10px] font-normal">
          只读 · 不会执行
        </Badge>
      </div>
      <div className="grid divide-y divide-border md:grid-cols-2 md:divide-x md:divide-y-0">
        <div className="min-w-0">
          <h4 className="border-b border-border bg-destructive/5 px-4 py-2 text-xs font-medium text-destructive">
            − 修复前
          </h4>
          <pre className="max-h-64 overflow-auto p-4 text-xs leading-6">
            <code>{issue.before}</code>
          </pre>
        </div>
        <div className="min-w-0">
          <h4 className="border-b border-border bg-success/5 px-4 py-2 text-xs font-medium text-success">
            + 建议修复后
          </h4>
          <pre className="max-h-64 overflow-auto p-4 text-xs leading-6">
            <code>{issue.after}</code>
          </pre>
        </div>
      </div>
    </section>
  );
}

interface ConfirmationProps {
  issue: Issue;
  open: boolean;
  isApply: boolean;
  acknowledged: boolean;
  allowed: boolean;
  onAcknowledged: (value: boolean) => void;
  onConfirm: () => void;
  onClose: () => void;
}
export function IssueDetailConfirmation({
  issue,
  open,
  isApply,
  acknowledged,
  allowed,
  onAcknowledged,
  onConfirm,
  onClose,
}: ConfirmationProps) {
  const focusRestore = useDetailFocusRestore();
  return (
    <Modal
      open={open}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <ModalContent {...focusRestore} className="w-[calc(100%-2rem)] max-w-lg">
        <ModalHeader className="text-left">
          <ModalTitle>{isApply ? '确认应用这条建议？' : '确认已完成人工验证？'}</ModalTitle>
          <ModalDescription>
            {isApply
              ? '此操作只把异常标记为「已应用」，不会执行代码，也不会自动验证或生成经验。'
              : '请先人工检查修复后的结果。本演示不会自动运行任务，也不会替你判断修复是否有效。'}
          </ModalDescription>
        </ModalHeader>
        <div className="rounded-lg border border-border bg-muted/30 p-4">
          <p className="text-sm font-medium">{issue.title}</p>
          <p className="mt-2 text-xs leading-6 text-muted-foreground">
            工作流：{issue.workflowName}
            <br />
            范围：当前浏览器中的演示状态，不影响生产环境。
          </p>
        </div>
        <div className="flex items-center justify-between gap-4 rounded-lg border border-border p-4">
          <label htmlFor="issue-safety-acknowledgement" className="cursor-pointer text-sm leading-6">
            {isApply ? '我已了解：仅更新演示状态，不执行代码' : '我已人工检查修复效果，确认验证通过'}
          </label>
          <Switch
            id="issue-safety-acknowledgement"
            checked={acknowledged}
            onCheckedChange={onAcknowledged}
            aria-label={isApply ? '确认应用建议的演示安全范围' : '确认已人工检查修复结果'}
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={onConfirm} disabled={!acknowledged || !allowed}>
            {isApply ? '确认应用（仅演示）' : '确认人工验证通过'}
          </Button>
        </div>
      </ModalContent>
    </Modal>
  );
}
