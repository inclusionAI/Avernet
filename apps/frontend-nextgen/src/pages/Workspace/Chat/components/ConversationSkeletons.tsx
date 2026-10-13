// 会话列表 / 好友目录的骨架占位:不铺整块灰板,而是逐行「回声」真实内容的几何——
// 32px 行高 + gap-1(=36px 列节奏,dmore DOM 实测)与会话行 ml-8 缩进一致,
// 好友分组行 = 头像方板 + 名称条,与 AvatarTile+displayName 行同构。
// 标题条宽度刻意错落,避免均质相连块的机器感;bar 用 Skeleton(ui 层 shimmer)。
import { Skeleton } from '@/components/ui';
import { cn } from '@/utils/cn';

const SESSION_TITLE_WIDTHS = ['w-[62%]', 'w-[46%]', 'w-[54%]'];

const BOT_NAME_LAYOUTS: Array<{ name: string; chip?: string }> = [
  { name: 'w-[38%]', chip: 'w-9' },
  { name: 'w-[26%]' },
  { name: 'w-[33%]', chip: 'w-7' },
  { name: 'w-[29%]' },
];

/** 一级 Bot 目录行骨架:回声 ConversationBotItem 行几何(16px 圆头像 + 14px 名称,
 *  x=16 与列表平齐;部分行带引擎徽标小方签 chip,宽度错落)。 */
export function ConversationBotRowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="flex flex-col gap-1 overflow-hidden">
      {Array.from({ length: rows }, (_, index) => {
        const layout = BOT_NAME_LAYOUTS[index % BOT_NAME_LAYOUTS.length];
        return (
          <div key={index} className="flex min-h-8 items-center gap-2 px-4">
            <Skeleton.Block className="h-4 w-4 shrink-0 rounded-full" />
            <Skeleton.Block className={cn('h-3.5 rounded-full', layout.name)} />
            {layout.chip && <Skeleton.Block className={cn('h-4 shrink-0 rounded', layout.chip)} />}
          </div>
        );
      })}
    </div>
  );
}

/** 二级会话行骨架:复刻 ConversationSessionRow 的行槽与文字缩进。 */
export function ConversationSessionRowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="flex flex-col gap-1 py-1">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex min-h-8 items-center">
          <Skeleton.Block
            className={cn('ml-8 h-3.5 rounded-full', SESSION_TITLE_WIDTHS[index % SESSION_TITLE_WIDTHS.length])}
          />
        </div>
      ))}
    </div>
  );
}

const FRIEND_NAME_WIDTHS = ['w-[38%]', 'w-[26%]', 'w-[33%]'];

/** 好友分组行骨架:复刻 ConversationFriendGroup 分组行(头像 + 名称)的几何与 pl-6 缩进。 */
export function ConversationFriendRowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="flex flex-col gap-1 py-1 pl-6">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex min-h-12 items-center gap-2 px-4 py-1.5">
          <Skeleton.Block className="h-8 w-8 shrink-0 rounded-md" />
          <Skeleton.Block className={cn('h-4 rounded-full', FRIEND_NAME_WIDTHS[index % FRIEND_NAME_WIDTHS.length])} />
        </div>
      ))}
    </div>
  );
}
