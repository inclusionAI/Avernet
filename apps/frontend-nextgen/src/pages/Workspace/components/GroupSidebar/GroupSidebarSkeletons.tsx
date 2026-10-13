// 协作群目录的骨架占位——沿用对话侧 ConversationSkeletons 的「回声」语言：
// 不铺整块灰板，而是逐行复刻群一级行 / 会话二级行的真实几何（dmore artboard-010 口径）——
// 群收起行 = py-0.5 行外套 + min-h-8 行 + 16px 目录图标 + 名称条 + 类型徽标小方签
// （SIDEBAR_TAG_CLASS 同构，自由聊天/任务协作/自定义协同均为 4~5 字短签）；
// 会话行 = min-h-8 行槽 + ml-8 缩进标题条（与 SessionItem 行几何一致）。
// 宽度刻意错落，避免均质相连块的机器感；bar 用 Skeleton（ui 层 shimmer）。
import { Skeleton } from '@/components/ui';
import { cn } from '@/utils/cn';

// 群行布局：名称条宽 + 徽标签宽（w-12≈4 字 / w-14≈5 字），逐行错落。
const GROUP_ROW_LAYOUTS: Array<{ name: string; tag: string }> = [
  { name: 'w-[42%]', tag: 'w-12' },
  { name: 'w-[30%]', tag: 'w-14' },
  { name: 'w-[54%]', tag: 'w-12' },
  { name: 'w-[36%]', tag: 'w-14' },
  { name: 'w-[48%]', tag: 'w-12' },
];

/** 群一级行骨架：回声 GroupItem 收起行几何（px-2/gap-2 与真行一致），真列表换入零横跳。 */
export function GroupRowsSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="overflow-hidden">
      {Array.from({ length: rows }, (_, index) => {
        const layout = GROUP_ROW_LAYOUTS[index % GROUP_ROW_LAYOUTS.length];
        return (
          <div key={index} className="py-0.5">
            <div className="flex min-h-8 items-center gap-2 pr-2 pl-2">
              <Skeleton.Block className="h-4 w-4 shrink-0 rounded" />
              <Skeleton.Block className={cn('h-3.5 rounded-full', layout.name)} />
              <Skeleton.Block className={cn('h-4 shrink-0 rounded', layout.tag)} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

const SESSION_TITLE_WIDTHS = ['w-[62%]', 'w-[46%]', 'w-[54%]'];

/** 会话二级行骨架：回声 SessionItem 的行槽与 ml-8 缩进（真会话行相邻无 gap，同构保留）。 */
export function GroupSessionRowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="py-1">
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