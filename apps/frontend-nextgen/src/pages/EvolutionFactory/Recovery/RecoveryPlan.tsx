import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { RecoveryPlan, RecoveryTask } from '@/domain/recovery/types';
import { type ReactNode, useState } from 'react';
import { MarkdownReport } from '../OutputPrimitives';
import { Section } from '../shared';

export function RecoveryDisclosure({ title, content }: { title: string; content?: string | null }) {
  if (!content?.trim()) return null;
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-[13px] text-muted-foreground hover:text-foreground">{title}</summary>
      <div className="mt-2">
        <MarkdownReport content={content} />
      </div>
    </details>
  );
}

function Note({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <h4 className="mb-1 text-[13px] font-medium text-muted-foreground">{label}</h4>
      {children}
    </div>
  );
}

const panel = 'min-w-0 rounded-lg border border-border bg-card p-4';
const panelTitle = 'mb-2 text-sm font-semibold text-foreground';

export function RecoveryPlanView({
  plan,
  task,
  historical,
  busy,
  onDecide,
}: {
  plan: RecoveryPlan;
  task: RecoveryTask;
  historical: boolean;
  busy: boolean;
  onDecide(approved: boolean, cooperationDone: boolean, feedback: string): Promise<void>;
}) {
  const [cooperationDone, setCooperationDone] = useState(false);
  const [feedback, setFeedback] = useState('');
  const awaiting =
    !historical &&
    !task.simulation &&
    task.canOperate &&
    task.status === 'waiting_owner' &&
    task.ownerRequest?.status !== 'pending' &&
    !plan.approvedAt;
  return (
    <div className="space-y-3">
      {plan.owner_cooperation.length > 0 && (
        <section className={panel} aria-label="方案中的用户配合">
          <h3 className={panelTitle}>{awaiting ? '需要你确认或处理' : 'Bot Owner 配合事项'}</h3>
          {plan.approvedAt && <p className="text-sm text-muted-foreground">方案已确认</p>}
          <ol className="m-0 list-decimal space-y-3 pl-5">
            {plan.owner_cooperation.map((item, index) => (
              <li key={index}>
                <MarkdownReport content={item.request} />
                <RecoveryDisclosure title="查看配合原因" content={item.basis} />
              </li>
            ))}
          </ol>
          {awaiting && (
            <label className="mt-3 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={cooperationDone}
                onChange={(event) => setCooperationDone(event.target.checked)}
              />
              上述配合条件已满足，可以按此方案执行
            </label>
          )}
        </section>
      )}
      <div className="tc-recovery-plan-columns grid gap-3">
        <section className={panel} aria-label="计划执行的自愈操作">
          <h3 className={panelTitle}>操作安排</h3>
          {plan.actions.length ? (
            <ol className={`m-0 space-y-3 ${plan.actions.length > 1 ? 'list-decimal pl-5' : 'list-none p-0'}`}>
              {plan.actions.map((action) => (
                <li
                  key={action.actionId}
                  id={`recovery-action-${encodeURIComponent(action.actionId)}`}
                  className="space-y-3"
                >
                  <MarkdownReport content={action.summary} />
                  {action.risk && (
                    <Note label="影响与风险">
                      <MarkdownReport content={action.risk} />
                    </Note>
                  )}
                  {(action.verification ||
                    action.rollback ||
                    action.command ||
                    action.engineOperation ||
                    action.operation) && (
                    <details>
                      <summary className="cursor-pointer text-[13px] text-muted-foreground hover:text-foreground">
                        查看操作细节
                      </summary>
                      <div className="mt-2 space-y-2">
                        {action.verification && (
                          <Note label="验证方式">
                            <MarkdownReport content={action.verification} />
                          </Note>
                        )}
                        {action.rollback && (
                          <Note label="失败与回退">
                            <MarkdownReport content={action.rollback} />
                          </Note>
                        )}
                        {(action.command || action.engineOperation || action.operation) && (
                          <pre className="max-h-56 overflow-auto rounded-md bg-muted p-2 text-xs">
                            {JSON.stringify(
                              {
                                command: action.command,
                                engineOperation: action.engineOperation,
                                operation: action.operation,
                              },
                              null,
                              2,
                            )}
                          </pre>
                        )}
                      </div>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">此方案未安排自愈助手执行修复动作。</p>
          )}
        </section>
        <section className={panel} aria-label="修复范围">
          <h3 className={panelTitle}>修复范围</h3>
          <MarkdownReport content={plan.scope || '方案尚未明确修复范围。'} />
        </section>
      </div>
      {(plan.summary || plan.basis || plan.verification) && (
        <details className="rounded-lg border border-border bg-card p-3">
          <summary className="cursor-pointer text-sm font-medium">处理思路与判断依据</summary>
          <div className="mt-3 space-y-3">
            {plan.summary && (
              <Note label="处理思路">
                <MarkdownReport content={plan.summary} />
              </Note>
            )}
            {plan.basis && (
              <Note label="判断依据">
                <MarkdownReport content={plan.basis} />
              </Note>
            )}
            {plan.verification && (
              <Note label="原任务恢复标准">
                <MarkdownReport content={plan.verification} />
              </Note>
            )}
          </div>
        </details>
      )}
      {awaiting && (
        <section className={panel} aria-label="确认完整方案">
          <p className="mb-3 text-sm text-muted-foreground">确认后，自愈助手将按上述范围与安排执行。</p>
          <details className="mb-3">
            <summary className="cursor-pointer text-sm">补充信息或处理说明（可选）</summary>
            <textarea
              className="mt-2 w-full rounded-md border border-border bg-background p-2 text-sm"
              aria-label="补充信息或处理说明"
              maxLength={4000}
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
            />
          </details>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              disabled={busy || (plan.owner_cooperation.length > 0 && !cooperationDone)}
              onClick={() => {
                void onDecide(true, cooperationDone, feedback);
              }}
            >
              确认完整方案
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => {
                void onDecide(false, cooperationDone, feedback);
              }}
            >
              拒绝方案
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}

export function RecoveryOwnerAction({
  task,
  busy,
  onConfirm,
}: {
  task: RecoveryTask;
  busy: boolean;
  onConfirm(requestId: string, feedback: string): Promise<void>;
}) {
  const request = task.ownerRequest;
  const [feedback, setFeedback] = useState('');
  if (task.status !== 'waiting_owner' || request?.status !== 'pending') return null;
  return (
    <div id="owner-cooperation" tabIndex={-1} className="scroll-mt-6 outline-none">
      <Section title="需要你处理">
        <div className={`${panel} space-y-3`}>
          {request.summary && (
            <Note label="需要你配合的原因">
              <MarkdownReport content={request.summary} />
            </Note>
          )}
          {request.instructions && (
            <Note label="怎么处理">
              <MarkdownReport content={request.instructions} />
            </Note>
          )}
          {task.canOperate && !task.simulation && (
            <>
              <details>
                <summary className="cursor-pointer text-sm">补充处理说明（可选）</summary>
                <textarea
                  aria-label="补充处理说明"
                  maxLength={4000}
                  className="mt-2 w-full rounded-md border border-border bg-background p-2 text-sm"
                  value={feedback}
                  onChange={(event) => setFeedback(event.target.value)}
                />
              </details>
              <Button
                size="sm"
                type="button"
                disabled={busy}
                onClick={() => {
                  void onConfirm(request.requestId, feedback);
                }}
              >
                已处理，继续核验
              </Button>
            </>
          )}
        </div>
      </Section>
    </div>
  );
}

export function StopRecovery({ busy, onConfirm, error }: { busy: boolean; onConfirm(): Promise<void>; error: string }) {
  return (
    <ConfirmDialog
      title="停止本次自愈？"
      description={error || '后续自愈处理将取消，已经完成的操作不会撤销。'}
      disabled={busy}
      confirmText="停止自愈"
      confirmVariant="destructive"
      onConfirm={onConfirm}
    >
      <Button type="button" variant="outline" size="sm">
        停止自愈
      </Button>
    </ConfirmDialog>
  );
}
