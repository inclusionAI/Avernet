import { Badge } from '@/components/ui/Badge';
import { Textarea } from '@/components/ui/Textarea';
import { useId } from 'react';
import { Button, Empty } from './OverviewShared';

export function RunInterventionCards() {
  const conversationReason = useId(),
    approvalReason = useId();
  return (
    <>
      <section aria-label="人工干预对话" className="tcov-card w-full">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          <h3 className="text-sm font-semibold">人工干预对话</h3>
          <Badge tone="neutral">能力建设中</Badge>
        </header>
        <div id={conversationReason} className="p-4">
          <Empty
            compact
            title="对话不可用"
            description="能力建设中：未接入已验证的对话读取与发送协议；无法确认消息记录或会话状态，不会生成消息或执行干预。"
          />
        </div>
        <div className="flex flex-wrap gap-2 border-y border-border px-4 py-3">
          {['重试', '跳过', '修正', '确认'].map((label) => (
            <Button key={label} disabled variant="outline" size="sm" aria-describedby={conversationReason}>
              {label}
            </Button>
          ))}
        </div>
        <div className="flex items-end gap-2 p-4">
          <Textarea
            disabled
            aria-label="干预消息"
            aria-describedby={conversationReason}
            placeholder="干预消息不可用：等待已验证的读取与发送协议"
            size="sm"
            className="min-h-16"
          />
          <Button disabled aria-describedby={conversationReason}>
            发送
          </Button>
        </div>
      </section>
      <section aria-label="运行审批" className="tcov-card p-4">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">运行审批</h3>
          <Badge tone="neutral">能力建设中</Badge>
        </header>
        <p id={approvalReason} className="my-3 text-xs leading-relaxed text-muted-foreground">
          能力建设中：未接入已验证的审批读取与处理协议，无法确认实际待审批记录、审批人、意见或数量；运行状态不代表审批状态。
        </p>
        <Button disabled variant="outline" size="sm" aria-describedby={approvalReason}>
          处理审批
        </Button>
      </section>
    </>
  );
}
