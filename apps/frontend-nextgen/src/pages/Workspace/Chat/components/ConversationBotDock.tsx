// 收起分组的「Bot 泊位」预览条(2026-10-10 需求:收起后不留盲区)。
// 与展开列表同材料的压缩视图:16px AvatarTile 头像 + 溢出 +N 收口,
// 头像可直达(展开分组 + 打开该 Bot 会话),当前选中 Bot 带选中底,
// 让收起态保持「看得出有谁 / 有几个 / 正开着哪个」。
import { Button } from '@/components/ui';
import type { ConversationBotView } from '@/domain/conversation/types';
import { cn } from '@/utils/cn';
import { AvatarTile } from '../../components/AvatarTile';

/** head 最多可见头像数,超出走「+N」溢出收口。 */
const DOCK_MAX_VISIBLE = 6;

export interface ConversationBotDockProps {
  items: ConversationBotView[];
  /** 当前选中的 botId(跨 section 由调用方判定后传入);选中头像带 bg-selected 底。 */
  selectedBotId: string | null;
  /** 头像直达:调用方负责展开分组并对未展开的 Bot 补一次 toggleBot 懒加载。 */
  onQuickOpen(view: ConversationBotView): void;
  /** 「+N」溢出点击:仅展开分组(与标题行为一致)。 */
  onExpandGroup(): void;
}

export function ConversationBotDock({ items, selectedBotId, onQuickOpen, onExpandGroup }: ConversationBotDockProps) {
  const visible = items.slice(0, DOCK_MAX_VISIBLE);
  const overflow = items.length - visible.length;
  return (
    <div className="flex items-center gap-0.5 px-3 pt-0.5 pb-1">
      {visible.map((view) => {
        const { bot } = view;
        // 与行内 handleToggle 同口径:AgentCoding / 不可聊 Bot 不建会话,泊位呈降权占位。
        const interactive = !bot.isAgentCodingBot && bot.chatable;
        const selected = bot.botId === selectedBotId;
        const tile = (
          <AvatarTile
            src={bot.avatarUrl}
            label={bot.displayName}
            className="h-4 w-4 rounded-full"
            fallbackContent={
              <span className="text-[8px] font-semibold leading-none tracking-[0.06em]">
                {bot.displayName.slice(0, 1)}
              </span>
            }
          />
        );
        return interactive ? (
          <Button
            key={bot.botId}
            variant="ghost"
            aria-label={`打开 ${bot.displayName}`}
            aria-current={selected ? 'true' : undefined}
            className={cn('h-auto w-auto shrink-0 rounded-full p-1 hover:bg-muted', selected && 'bg-selected')}
            onClick={() => onQuickOpen(view)}
          >
            {tile}
          </Button>
        ) : (
          <span key={bot.botId} className="flex shrink-0 cursor-not-allowed rounded-full p-1 opacity-50">
            {tile}
          </span>
        );
      })}
      {overflow > 0 && (
        <Button
          variant="ghost"
          aria-label={`展开更多 Bot（还有 ${overflow} 个）`}
          className="h-auto w-auto shrink-0 rounded-full px-2 py-1 text-[10px] font-normal text-content-soft hover:bg-muted"
          onClick={onExpandGroup}
        >
          +{overflow}
        </Button>
      )}
    </div>
  );
}
