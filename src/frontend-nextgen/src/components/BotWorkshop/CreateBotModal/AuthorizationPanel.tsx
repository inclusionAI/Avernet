import { Button } from '@/components/ui/Button';
import { ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotCreateAuthorization } from '@/services/botWorkshop';
import { ExternalLink, ShieldCheck } from 'lucide-react';

export function AuthorizationPanel({
  authorization,
  creating,
  onClose,
}: {
  authorization: BotCreateAuthorization & { message?: string; error?: string };
  creating: boolean;
  onClose: () => void;
}) {
  return (
    <>
      <ModalHeader>
        <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <ShieldCheck aria-hidden className="size-5" />
        </div>
        <ModalTitle>完成 AgentPass 授权</ModalTitle>
        <ModalDescription id="create-bot-description">
          请在下方完成授权。授权成功后系统会自动确认并完成 Bot 创建，请勿重复提交。
        </ModalDescription>
      </ModalHeader>
      {authorization.iframeUrl ? (
        <iframe
          title="AgentPass 授权"
          src={authorization.iframeUrl}
          className="h-[520px] w-full rounded-lg border border-border bg-background"
          referrerPolicy="strict-origin-when-cross-origin"
        />
      ) : (
        <div className="flex min-h-64 flex-col items-center justify-center rounded-lg border border-border bg-muted/30 p-6 text-center">
          <p className="m-0 text-xs text-muted-foreground">授权服务要求在新窗口中继续。</p>
          <Button
            className="mt-4"
            leftIcon={<ExternalLink className="size-4" />}
            onClick={() => window.open(authorization.redirectUrl, '_blank', 'noopener,noreferrer')}
          >
            打开授权页面
          </Button>
        </div>
      )}
      <div aria-live="polite" className="rounded-lg border border-border bg-muted/30 px-4 py-3 text-xs">
        {authorization.error ? (
          <span className="text-destructive">{authorization.error}</span>
        ) : (
          <span className="text-muted-foreground">
            {authorization.message || '正在等待授权结果，完成后将自动关闭此窗口…'}
          </span>
        )}
      </div>
      <ModalFooter>
        <Button variant="secondary" disabled={creating} onClick={onClose}>
          取消创建
        </Button>
        {authorization.redirectUrl && authorization.iframeUrl ? (
          <Button
            variant="secondary"
            leftIcon={<ExternalLink className="size-4" />}
            onClick={() => window.open(authorization.redirectUrl, '_blank', 'noopener,noreferrer')}
          >
            新窗口打开
          </Button>
        ) : null}
      </ModalFooter>
    </>
  );
}
