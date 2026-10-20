import { Button } from '@/components/ui';
import type { ParticipantView } from '@/domain/collaboration';
import type { GroupChatExecApprovalsModel } from '@/pages/Workspace/hooks/useGroupChatExecApprovals';
import { ShieldQuestion } from 'lucide-react';

export function GroupExecApprovalPanel({
  model,
  participants,
}: {
  model: GroupChatExecApprovalsModel;
  participants: ParticipantView[];
}) {
  if (!model.interactions.length) return null;
  return (
    <section
      aria-label="待处理执行审批"
      className="max-h-[min(38vh,360px)] shrink-0 space-y-2 overflow-y-auto border-t border-input p-3"
    >
      <h3 className="flex items-center gap-2 text-sm font-medium">
        <ShieldQuestion aria-hidden className="h-4 w-4 text-primary" />
        等待执行审批（{model.interactions.length}）
      </h3>
      {!model.connected ? (
        <p role="status" className="text-xs text-muted-foreground">
          连接已断开，重连后可处理审批。
        </p>
      ) : null}
      {model.interactions.map((approval) => {
        const botName = participants.find((item) => item.actorId === approval.botId)?.name || approval.botId;
        const submitting = model.submittingKeys.includes(approval.key);
        return (
          <section
            key={approval.key}
            aria-label={`${botName} 的执行审批`}
            className="space-y-2 rounded-xl border border-input p-3"
          >
            <p className="text-sm font-medium">
              {botName} 请求执行 {approval.toolName}
            </p>
            {approval.reason ? (
              <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">{approval.reason}</p>
            ) : null}
            {approval.cwd ? <p className="break-all text-xs text-muted-foreground">目录：{approval.cwd}</p> : null}
            {approval.command ? (
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted p-2 text-xs">
                {approval.command}
              </pre>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              {approval.options.map((option) => (
                <Button
                  key={option.decision}
                  type="button"
                  size="sm"
                  variant={option.decision === 'deny' ? 'secondary' : 'default'}
                  disabled={!model.connected || submitting}
                  onClick={() => {
                    void model.resolve(approval.key, option.decision);
                  }}
                >
                  {option.decision === 'allow_once' ? '允许一次' : '拒绝'}
                </Button>
              ))}
              {submitting ? (
                <span role="status" className="text-xs text-muted-foreground">
                  正在提交审批…
                </span>
              ) : null}
            </div>
          </section>
        );
      })}
    </section>
  );
}
