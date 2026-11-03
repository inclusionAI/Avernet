import { Spin } from '@/components/ui/Spin';
import { cn } from '@/utils/cn';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

/** 老版 AgentPass 交互：保留列表背景，iframe 首屏就绪后再显示授权页面。 */
export interface AuthorizationPanelProps {
  /**
   * 仅消费 iframeUrl/redirectUrl(message/error 可选用于状态文案)。create 与 copy 的
   * 授权视图结构不同(create 携 request 走轮询,copy 不携带),故此处取结构最小集,
   * 让两条流程共用此面板而不强行耦合它们的 result 形状。
   */
  authorization: { iframeUrl?: string; redirectUrl?: string; message?: string; error?: string };
}

export function AuthorizationPanel({ authorization }: AuthorizationPanelProps) {
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
