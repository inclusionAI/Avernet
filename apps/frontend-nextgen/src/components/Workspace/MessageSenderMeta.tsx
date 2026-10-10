import { cn } from '@/utils/cn';
import type { ReactNode } from 'react';

interface MessageSenderMetaProps {
  name: string;
  time?: string;
  align: 'left' | 'right';
}

/** 用户与 Bot 消息共用的发送者元信息行，确保名称和时间同行且左右对称。
 *  dmore index.html 实测（restore-design-chat-page-b2-b3 Batch 2）：
 *  名称 13px——时间在左、名称在右 / Bot 名称在左、时间在右；
 *  2026-10-10 用户反馈：用户侧名称不加重颜色，与 Bot 侧统一用 content-strong；
 *  时间 12px #71717A，名称与时间小空隙并排（无分隔点）。 */
export function MessageSenderMeta({ name, time, align }: MessageSenderMetaProps) {
  const alignmentClass = align === 'right' ? 'justify-end text-right' : 'justify-start text-left';
  const isUser = align === 'right';
  const timeNode = time ? <span className="shrink-0 text-xs leading-4 text-muted-foreground">{time}</span> : null;
  const nameNode = (
    <span className={cn('min-w-0 max-w-full truncate', 'font-normal text-content-strong')}>
      {name || (isUser ? '未命名成员' : '未命名 Bot')}
    </span>
  );

  return (
    <div
      data-testid="message-sender-meta"
      className={`mt-1 mb-1.5 flex min-w-0 flex-nowrap items-center gap-1.5 text-[13px] leading-4 ${alignmentClass}`}
    >
      {isUser ? (
        <>
          {timeNode}
          {nameNode}
        </>
      ) : (
        <>
          {nameNode}
          {timeNode}
        </>
      )}
    </div>
  );
}

interface MessageSenderLayoutProps {
  /** 头像槽位；null 表示无头像形态（dmore 稿 Bot 消息为裸排版，无头像）。 */
  avatar?: ReactNode | null;
  align: 'left' | 'right';
  meta: ReactNode;
  children: ReactNode;
}

/**
 * 将头像、发送者元信息和消息正文放入同一行级布局，避免元信息单独占据头像上方的垂直空间。
 * 右对齐消息反转内容顺序，但仍保持头像与名称/时间行的顶部对齐。
 * 头像缺省（null）时直接裸排版 meta+内容（dmore 稿 Bot 消息形态）。
 */
export function MessageSenderLayout({ avatar, align, meta, children }: MessageSenderLayoutProps) {
  const content = (
    <div className="min-w-0 flex-1">
      {meta}
      {children}
    </div>
  );

  return (
    <div className={`flex min-w-0 items-start gap-3 ${align === 'right' ? 'justify-end' : 'justify-start'}`}>
      {align === 'right' ? (
        <>
          {content}
          {avatar ? <div className="shrink-0">{avatar}</div> : null}
        </>
      ) : (
        <>
          {avatar ? <div className="shrink-0">{avatar}</div> : null}
          {content}
        </>
      )}
    </div>
  );
}
