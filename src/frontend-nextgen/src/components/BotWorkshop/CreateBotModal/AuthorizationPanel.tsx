import type { BotCreateAuthorization } from '@/services/botWorkshop';
import { createPortal } from 'react-dom';

/** 老版 AgentPass 交互：授权页面直接覆盖视口，不嵌套在创建弹窗的内容框中。 */
export function AuthorizationPanel({
  authorization,
}: {
  authorization: BotCreateAuthorization & { message?: string; error?: string };
}) {
  const authorizationUrl = authorization.iframeUrl || authorization.redirectUrl;

  if (typeof document === 'undefined') return null;

  return createPortal(
    <div className="fixed inset-0 z-[200] h-dvh w-screen bg-background">
      <iframe
        title="Bot 授权"
        src={authorizationUrl}
        className="h-full w-full border-none"
        referrerPolicy="strict-origin-when-cross-origin"
      />
    </div>,
    document.body,
  );
}
