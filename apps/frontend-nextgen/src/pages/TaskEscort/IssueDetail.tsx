import { useDetailFocusRestore } from '@/hooks/taskEscortWorkspace/useDetailFocus';
import { useIssueDetail } from '@/hooks/taskEscortWorkspace/useIssueDetail';
import {
  ArrowRight,
  BookOpenCheck,
  Check,
  CircleCheck,
  GitBranch,
  Lightbulb,
  ListChecks,
  ScanSearch,
  ShieldCheck,
} from 'lucide-react';
import { CodeComparison, IssueDetailConfirmation } from './IssueDetail.parts';
import { IssueLifecycle, SeverityBadge } from './Issues.parts';
import type { ViewProps } from './types';
import { Button, Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle, StatusBadge } from './ui';

export function IssueDetail({ demo }: ViewProps) {
  const focusRestore = useDetailFocusRestore();
  const {
    issue,
    actions,
    close,
    beginConfirmation,
    confirm,
    closeConfirmation,
    saveExperience,
    acknowledged,
    setAcknowledged,
    confirming,
    isApply,
    allowed,
    lifecycleMessage,
    lifecycleStep,
    verified,
  } = useIssueDetail(demo);
  if (!issue) return null;
  const confidence = Math.round(issue.confidence > 1 ? issue.confidence : issue.confidence * 100);

  return (
    <>
      <Modal
        open={Boolean(issue)}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <ModalContent
          {...focusRestore}
          data-detail-focus-fallback
          className="max-h-[90vh] w-[calc(100%-2rem)] max-w-4xl overflow-y-auto p-0"
        >
          <ModalHeader className="space-y-3 border-b border-border px-6 pb-5 pt-6 text-left">
            <div className="flex flex-wrap items-center gap-2 pr-7">
              <span className="mono text-xs text-muted-foreground">{issue.id}</span>
              <SeverityBadge severity={issue.severity} />
              <StatusBadge status={issue.status} />
            </div>
            <ModalTitle className="pr-5 text-xl leading-8">{issue.title}</ModalTitle>
            <ModalDescription>{issue.description} 以下分析来自演示数据，所有变更仅保存在当前演示中。</ModalDescription>
          </ModalHeader>
          <div className="space-y-6 px-6 py-5">
            <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <GitBranch className="size-3.5" />
                {issue.workflowName}
              </span>
              <span>
                异常节点：
                <strong className="font-medium text-foreground">{issue.node}</strong>
              </span>
              <span>影响 {issue.affectedRuns} 次运行</span>
              <span>分析置信度 {confidence}%</span>
            </div>
            <IssueLifecycle current={lifecycleStep} />
            <section aria-labelledby="issue-evidence-heading">
              <h3 id="issue-evidence-heading" className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <ScanSearch className="size-4 text-primary" />
                运行证据
                <span className="ml-auto text-xs font-normal text-muted-foreground">
                  {issue.evidence.length} 条记录
                </span>
              </h3>
              <ol className="space-y-2">
                {issue.evidence.map((evidence, index) => (
                  <li
                    key={`${index}-${evidence}`}
                    className="flex gap-3 rounded-lg border border-border bg-muted/20 px-3 py-2.5"
                  >
                    <span className="mono pt-0.5 text-[10px] text-muted-foreground">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <code className="min-w-0 break-all text-xs leading-6">{evidence}</code>
                  </li>
                ))}
              </ol>
              {!issue.evidence.length && (
                <p className="text-sm text-muted-foreground">此条演示异常未附加日志，请结合下方根因分析人工判断。</p>
              )}
            </section>
            <div className="grid gap-4 md:grid-cols-2">
              <section className="rounded-lg border border-border p-4">
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  <ScanSearch className="size-4 text-muted-foreground" />
                  根因分析
                </h3>
                <p className="mt-3 text-sm leading-7 text-muted-foreground">{issue.rootCause}</p>
              </section>
              <section className="rounded-lg border border-primary/20 bg-primary/[0.03] p-4">
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  <Lightbulb className="size-4 text-primary" />
                  修复建议
                </h3>
                <p className="mt-3 text-sm leading-7 text-muted-foreground">{issue.recommendation}</p>
              </section>
            </div>
            <CodeComparison issue={issue} />
            <div className="flex gap-2 rounded-lg bg-muted/50 p-3 text-xs leading-6 text-muted-foreground">
              <ShieldCheck className="mt-1 size-4 shrink-0 text-primary" />
              <p>
                安全边界：仅本地演示。不会调用后端、修改生产工作流或执行以上代码。应用不等于修复成功，人工验证后才可保存经验。
              </p>
            </div>
            <div
              role="status"
              aria-live="polite"
              data-testid="issue-lifecycle-message"
              className={`flex items-start gap-2 rounded-lg border p-3 text-sm leading-6 ${
                verified
                  ? 'border-success/20 bg-success/5 text-success'
                  : 'border-primary/20 bg-primary/5 text-foreground'
              }`}
            >
              {verified ? (
                <CircleCheck className="mt-1 size-4 shrink-0" />
              ) : (
                <ListChecks className="mt-1 size-4 shrink-0 text-primary" />
              )}
              <span>{lifecycleMessage}</span>
            </div>
          </div>
          <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-6 py-4">
            <Button
              variant="ghost"
              size="sm"
              className="px-0 text-xs text-primary"
              onClick={() => {
                close();
                demo.navigate('runs');
                demo.setWorkflowId(issue.workflowId);
                demo.setRunFilter('all');
                demo.setQuery('');
              }}
            >
              查看相关工作流运行
              <ArrowRight className="ml-1 size-3.5" />
            </Button>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={close}>
                关闭
              </Button>
              {actions.canApply && (
                <Button size="sm" onClick={() => beginConfirmation('apply')}>
                  <Check className="mr-1.5 size-3.5" />
                  应用建议
                </Button>
              )}
              {actions.canVerify && (
                <Button size="sm" onClick={() => beginConfirmation('verify')}>
                  <ListChecks className="mr-1.5 size-3.5" />
                  人工验证通过
                </Button>
              )}
              {actions.canSave && (
                <Button size="sm" onClick={saveExperience}>
                  <BookOpenCheck className="mr-1.5 size-3.5" />
                  保存为经验
                </Button>
              )}
              {actions.saved && (
                <Button size="sm" variant="secondary" disabled>
                  <BookOpenCheck className="mr-1.5 size-3.5" />
                  已保存经验
                </Button>
              )}
            </div>
          </footer>
        </ModalContent>
      </Modal>
      <IssueDetailConfirmation
        issue={issue}
        open={confirming}
        isApply={isApply}
        acknowledged={acknowledged}
        onAcknowledged={setAcknowledged}
        onConfirm={confirm}
        allowed={allowed}
        onClose={closeConfirmation}
      />
    </>
  );
}
