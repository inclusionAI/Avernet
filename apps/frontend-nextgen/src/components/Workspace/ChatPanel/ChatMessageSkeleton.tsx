// 聊天消息历史加载骨架:把「正在装填的这段群聊」直接画出来,而不是空画布正中转 Spin。
// 逐行回声真实消息解剖(MessageSenderLayout/SystemNotice/Bubble)的几何:
// 32px 圆头像、名称+时间双段 meta 行、maxWidth 48rem 的气泡块;
// 群聊特有的成分也占位——开场系统提示 pill、成段长回答、短提问、图片附件卡 + 简短评述。
// 宽度/高度全部错落,拒绝均质三连块;坐标细节对齐 ChatLayout.List 画布槽位。
import { Skeleton } from '@/components/ui';
import { cn } from '@/utils/cn';

interface MessageEcho {
  align: 'left' | 'right';
  name: string;
  time: string;
  /** 正文气泡(className 片段,高度/宽度)。 */
  bubble?: string;
  /** 图片附件卡(群聊常见成分)+ 卡下简短评述气泡。 */
  attachment?: { card: string; caption: string };
}

const EXCHANGES: MessageEcho[] = [
  // 短提问开场(用户右侧)。
  { align: 'right', name: 'w-12', time: 'w-8', bubble: 'h-9 w-[26%]' },
  // 成段长回答(助手左侧,高气泡暗喻多段Markdown)。
  { align: 'left', name: 'w-16', time: 'w-10', bubble: 'h-24 w-[54%]' },
  // 追问(用户右侧,中等)。
  { align: 'right', name: 'w-12', time: 'w-8', bubble: 'h-12 w-[38%]' },
  // 图片附件 + 简短评述(群聊高频形态)。
  { align: 'left', name: 'w-20', time: 'w-10', attachment: { card: 'h-20 w-32 rounded-xl', caption: 'h-8 w-[42%]' } },
  // 收尾短消息(助手左侧)。
  { align: 'left', name: 'w-16', time: 'w-6', bubble: 'h-10 w-[46%]' },
];

function ChatMessageEchoRow({ echo }: { echo: MessageEcho }) {
  const isLeft = echo.align === 'left';
  return (
    <div className={cn('flex min-w-0 items-start gap-3', isLeft ? 'justify-start' : 'flex-row-reverse')}>
      <Skeleton.Block className="h-8 w-8 shrink-0 rounded-full" />
      <div className={cn('flex min-w-0 flex-1 flex-col gap-1.5', isLeft ? 'items-start' : 'items-end')}>
        {/* meta 行:名称 + 时间双段错落(对应 MessageSenderMeta 的 name+time 并排)。 */}
        <div className={cn('flex items-center gap-1.5', isLeft ? '' : 'flex-row-reverse')}>
          <Skeleton.Block className={cn('h-3 rounded-full', echo.name)} />
          <Skeleton.Block className={cn('h-2.5 rounded-full', echo.time)} />
        </div>
        {echo.bubble && <Skeleton.Block className={cn('max-w-3xl rounded-2xl', echo.bubble)} />}
        {echo.attachment && (
          <>
            <Skeleton.Block className={cn('shrink-0', echo.attachment.card)} />
            <Skeleton.Block className={cn('max-w-3xl rounded-2xl', echo.attachment.caption)} />
          </>
        )}
      </div>
    </div>
  );
}

/** 消息历史首拉在途的画布占位(aria-label 延续既有「加载会话消息」)。 */
export function ChatMessageSkeleton() {
  return (
    <div
      role="status"
      aria-label="加载会话消息"
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background px-3 py-3 sm:px-5 sm:py-4"
    >
      <div className="flex flex-col gap-4">
        {/* 开场系统提示 pill(「xx 加入会话」类 SystemNotice)——群聊语境的地道成分。 */}
        <Skeleton.Block className="h-6 w-28 self-center rounded-full" />
        {EXCHANGES.map((echo, index) => (
          <ChatMessageEchoRow key={index} echo={echo} />
        ))}
      </div>
    </div>
  );
}
