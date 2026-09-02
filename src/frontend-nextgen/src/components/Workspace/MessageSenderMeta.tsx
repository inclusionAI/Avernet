import type { ReactNode } from 'react';

interface MessageSenderMetaProps {
  name: string;
  time?: string;
  align: 'left' | 'right';
}

/** 用户与 Bot 消息共用的发送者元信息行，确保名称和时间同行且左右对称。 */
export function MessageSenderMeta({ name, time, align }: MessageSenderMetaProps) {
  const alignmentClass = align === 'right' ? 'justify-end text-right' : 'justify-start text-left';

  return (
    <div
      data-testid="message-sender-meta"
      className={`mb-1 flex min-w-0 flex-nowrap items-center gap-1.5 text-xs leading-4 text-muted-foreground ${alignmentClass}`}
    >
      <span className="min-w-0 max-w-full truncate font-medium">
        {name || (align === 'right' ? '未命名成员' : '未命名 Bot')}
      </span>
      {time ? (
        <>
          <span aria-hidden="true">·</span>
          <span className="shrink-0">{time}</span>
        </>
      ) : null}
    </div>
  );
}

interface MessageSenderLayoutProps {
  avatar: ReactNode;
  align: 'left' | 'right';
  meta: ReactNode;
  children: ReactNode;
}

/**
 * 将头像、发送者元信息和消息正文放入同一行级布局，避免元信息单独占据头像上方的垂直空间。
 * 右对齐消息反转内容顺序，但仍保持头像与名称/时间行的顶部对齐。
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
          <div className="shrink-0">{avatar}</div>
        </>
      ) : (
        <>
          <div className="shrink-0">{avatar}</div>
          {content}
        </>
      )}
    </div>
  );
}
