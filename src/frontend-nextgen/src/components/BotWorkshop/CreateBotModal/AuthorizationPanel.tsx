import { Spin } from '@/components/ui/Spin';
import type { BotCreateAuthorization } from '@/services/botWorkshop';
import { cn } from '@/utils/cn';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

/** 老版 AgentPass 交互：保留列表背景，iframe 首屏就绪后再显示授权页面。 */
export function AuthorizationPanel({
  authorization,
}: {
  authorization: BotCreateAuthorization & { message?: string; error?: string };
}) {
  const authorizationUrl = authorization.iframeUrl || authorization.redirectUrl;
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setLoaded(false);
  }, [authorizationUrl]);

  if (typeof document === 'undefined') return null;

  return createPortal(
    <div className="fixed inset-0 z-[200] h-dvh w-screen">
      {!loaded ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/20 p-4 backdrop-blur-[1px]">
          <div
            role="status"
            aria-label="正在加载 AgentPass 授权页面"
            aria-live="polite"
            className="w-full max-w-sm rounded-lg border border-border bg-background px-6 py-5 text-center text-foreground shadow-lg"
          >
            <Spin tip="正在加载 AgentPass 授权页面…" className="py-1" />
            <p className="mt-2 text-xs text-muted-foreground">Bot 列表已保留，授权页面加载完成后将自动展示。</p>
          </div>
        </div>
      ) : null}
      <iframe
        title="Bot 授权"
        src={authorizationUrl}
        className={cn(
          'h-full w-full border-none transition-opacity duration-150',
          loaded ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        referrerPolicy="strict-origin-when-cross-origin"
        onLoad={() => setLoaded(true)}
      />
    </div>,
    document.body,
  );
}
