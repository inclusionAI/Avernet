// 聊天消息历史加载骨架——把占位画布当作「誊写中的对话稿」：
// 逐行回声真实消息解剖（dmore 复测口径，chatPanelPresentation / MessageSenderLayout /
// @tc-chat/ui Bubble styles.js，2026-10-10）：
// - 画布槽位与 ChatLayout.List 同构（px-3 py-3 sm:py-4 sm:pl-14 sm:pr-3），真消息换入零横跳；
// - 头像 24px 圆（chatPanelPresentation 实测；非早期 32px 误读），头像列 gap-3（12px）；
// - meta 行 mt-1 mb-1.5：13px 名条 + 12px 时条，右置消息顺序为「时间左、名称右」；
// - 助手气泡系裸排版（LeftBubble 无底色），以行组回声 Markdown 行节奏（bar 更细、
//   行高≈13px×1.6），不画实心灰块；
// - 用户气泡直接采用真实表面 hsl(var(--muted))，半径 16px 唯右上 4px（UserBubble 直读）；
// - 头像占位用品牌蓝 10%（≈SDK Avatar 的 --aix-color-primary-light 底色家族）。
// 动效：chat-skeleton-sweep 扫光（global.css）按行负延迟错峰自上而下掠过；
// 终点为一组光珠（回声 SDK LoadingDots），示意「历史仍在抵达」。
// 材质克制：整幅只有中性 foreground/10 与品牌 primary 系两种，无装饰卡。
import { cn } from '@/utils/cn';

interface EchoMetaBars {
  /** 名条宽。 */
  name: string;
  /** 时条宽。 */
  time: string;
}

interface MessageEcho {
  align: 'left' | 'right';
  meta: EchoMetaBars;
  /** 用户气泡（muted 真实表面）；高/宽 className 片段。 */
  bubble?: string;
  /** 助手裸排版行组（Markdown 行节奏的条影）。 */
  lines?: string[];
}

// 一段可信的工程问答叙事：提问→长作答→追问→作答→确认→收尾，宽度全部错落。
const EXCHANGES: MessageEcho[] = [
  { align: 'right', meta: { name: 'w-14', time: 'w-8' }, bubble: 'h-9 w-[27%]' },
  {
    align: 'left',
    meta: { name: 'w-20', time: 'w-9' },
    lines: ['h-3.5 w-[42%]', 'h-2.5 w-[94%]', 'h-2.5 w-[88%]', 'h-2.5 w-[61%]'],
  },
  { align: 'right', meta: { name: 'w-14', time: 'w-10' }, bubble: 'h-12 w-[34%]' },
  {
    align: 'left',
    meta: { name: 'w-20', time: 'w-9' },
    lines: ['h-2.5 w-[92%]', 'h-2.5 w-[78%]', 'h-2.5 w-[68%]'],
  },
  { align: 'right', meta: { name: 'w-14', time: 'w-8' }, bubble: 'h-9 w-[22%]' },
  {
    align: 'left',
    meta: { name: 'w-20', time: 'w-9' },
    lines: ['h-2.5 w-[96%]', 'h-2.5 w-[83%]', 'h-2.5 w-[45%]'],
  },
];

/** 行扫光错峰步长：七行铺满一个 2.4s 周期内，任意时刻 1–2 道扫光在途。 */
const SWEEP_ROW_STEP = -0.22;

interface EchoCellProps {
  className?: string;
  /** 负延迟：挂载瞬间该行已在波形中途，无需从零起波。 */
  delay: number;
}

/** 占位单元：底色由 bg-* 工具类承担，扫光由 chat-skeleton-sweep 承担。 */
function EchoCell({ className, delay }: EchoCellProps) {
  return (
    <div
      aria-hidden="true"
      style={{ animationDelay: `${delay}s` }}
      className={cn('chat-skeleton-sweep', className)}
    />
  );
}

function EchoMetaRow({ meta, align, delay }: { meta: EchoMetaBars; align: 'left' | 'right'; delay: number }) {
  const isLeft = align === 'left';
  return (
    // 真实 MessageSenderMeta：左置「名称左、时间右」，右置镜像反转——这里 DOM 顺序统一，
    // 右置用 flex-row-reverse 还原真实视觉顺序。
    <div className={cn('mt-1 mb-1.5 flex items-center gap-1.5', !isLeft && 'flex-row-reverse')}>
      <EchoCell delay={delay} className={cn('h-3 rounded-full bg-foreground/10', meta.name)} />
      <EchoCell delay={delay} className={cn('h-2.5 rounded-full bg-foreground/10', meta.time)} />
    </div>
  );
}

function EchoAvatar({ delay, className }: { delay: number; className?: string }) {
  return <EchoCell delay={delay} className={cn('size-6 shrink-0 rounded-full bg-primary/10', className)} />;
}

function ChatMessageEchoRow({ echo, index }: { echo: MessageEcho; index: number }) {
  const isLeft = echo.align === 'left';
  const delay = index * SWEEP_ROW_STEP;
  const avatar = <EchoAvatar delay={delay} />;
  const content = (
    <div className={cn('flex min-w-0 flex-1 flex-col', isLeft ? 'items-start' : 'items-end')}>
      <EchoMetaRow meta={echo.meta} align={echo.align} delay={delay} />
      {echo.bubble ? (
        // 用户气泡：真实 muted 表面 + 16px 圆角唯右上 4px（Bubble UserBubble 直读）。
        <EchoCell delay={delay} className={cn('max-w-3xl rounded-2xl rounded-tr-[4px] bg-muted', echo.bubble)} />
      ) : (
        // 助手裸排版行组：max-w-48rem（sender.maxWidth '48rem' = max-w-3xl），行距近 line-height 1.6。
        <div className="flex w-full max-w-3xl flex-col gap-2">
          {echo.lines?.map((line, lineIndex) => (
            <EchoCell key={lineIndex} delay={delay} className={cn('rounded-full bg-foreground/10', line)} />
          ))}
        </div>
      )}
    </div>
  );
  // 同构 MessageSenderLayout：右置消息交换 DOM 顺序（content 在前、头像殿后）+ justify-end，不用 row-reverse。
  return (
    <div className={cn('flex min-w-0 items-start gap-3', isLeft ? 'justify-start' : 'justify-end')}>
      {isLeft ? (
        <>
          {avatar}
          {content}
        </>
      ) : (
        <>
          {content}
          {avatar}
        </>
      )}
    </div>
  );
}

/** 终点行：光珠回声 SDK LoadingDots（styles.js：9px、gap6、pad 6/0、1.2s bounce、0.2s 逐粒）。 */
function ChatTypingEchoRow({ index }: { index: number }) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <EchoAvatar delay={index * SWEEP_ROW_STEP} />
      <div className="flex min-w-0 flex-1 flex-col items-start">
        <EchoMetaRow meta={{ name: 'w-20', time: 'w-9' }} align="left" delay={index * SWEEP_ROW_STEP} />
        <div className="flex items-center gap-1.5 py-1.5">
          {[0, 1, 2].map((dot) => (
            <span key={dot} className="chat-skeleton-dot size-[9px] rounded-full bg-primary/45" />
          ))}
        </div>
      </div>
    </div>
  );
}

/** 消息历史首拉在途的画布占位。ariaLabel 可由宿主覆写（如展开 Bot 时
 * 会话列表未定，ConversationSessionPlaceholder 借用本骨架时读屏话术应为「加载会话列表」）；
 * paddingClassName 同理由宿主对齐真实消息列表的画布 padding（协作群 BubbleList 为
 * px-3 py-3 sm:px-6 sm:py-4），保证真消息换入零横跳。 */
export function ChatMessageSkeleton({
  ariaLabel = '加载会话消息',
  paddingClassName = 'px-3 py-3 sm:py-4 sm:pl-14 sm:pr-3',
}: {
  ariaLabel?: string;
  paddingClassName?: string;
}) {
  const typingRowIndex = EXCHANGES.length;
  return (
    <div
      role="status"
      aria-label={ariaLabel}
      className={cn('flex min-h-0 flex-1 flex-col overflow-hidden bg-background', paddingClassName)}
    >
      <div className="flex flex-col">
        {/* dmore SystemNotice 形态：居中弱文本行（showIcon=false），不是灰胶囊。 */}
        <div className="mb-6 flex justify-center">
          <EchoCell delay={0} className="h-2.5 w-24 rounded-full bg-foreground/10" />
        </div>
        {EXCHANGES.map((echo, index) => (
          // 行距回声 messagePresentation：发者切换 24px；末行助手紧接光珠行（同发者 16px）。
          <div key={index} className={index === typingRowIndex - 1 ? 'mb-4' : 'mb-6'}>
            <ChatMessageEchoRow echo={echo} index={index} />
          </div>
        ))}
        <div className="mb-6">
          <ChatTypingEchoRow index={typingRowIndex} />
        </div>
      </div>
    </div>
  );
}
