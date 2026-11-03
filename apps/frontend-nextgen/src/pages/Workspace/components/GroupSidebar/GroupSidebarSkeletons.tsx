// 协作群目录的骨架占位——沿用对话侧 ConversationSkeletons 的「回声」语言：
// 不铺整块灰板，而是逐行复刻群一级行 / 会话二级行的真实几何（dmore artboard-010 口径）——
// 群收起行 = 52px 行 + 36px 头像槽 + 名称/类型两行；
// 会话行 = 32px 行槽 + 61px 标题起点（含真实按钮的 1px 边框）。
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

/** 群一级行骨架：与 GroupItem 的固定头像槽、双行名称一致；块间无分隔线（2026-10-22 与实装行同构）。 */
export function GroupRowsSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="overflow-hidden">
      {Array.from({ length: rows }, (_, index) => {
        const layout = GROUP_ROW_LAYOUTS[index % GROUP_ROW_LAYOUTS.length];
        return (
          <div key={index} className="py-0.5">
            <div className="flex min-h-13 items-center gap-2 pr-4 pl-[17px]">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center">
                <Skeleton.Block className="h-5 w-5 rounded-full" />
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <Skeleton.Block className={cn('h-3.5 rounded-full', layout.name)} />
                <Skeleton.Block className={cn('h-4 rounded', layout.tag)} />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const SESSION_TITLE_WIDTHS = ['w-[62%]', 'w-[46%]', 'w-[54%]'];

/** 会话二级行骨架：回声 SessionItem 的行槽、标题起点与右缘相对时间轻提示。 */
export function GroupSessionRowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="py-1">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex min-h-8 items-center">
          <Skeleton.Block
            className={cn('ml-[61px] h-3.5 rounded-full', SESSION_TITLE_WIDTHS[index % SESSION_TITLE_WIDTHS.length])}
          />
          <Skeleton.Block className="ml-auto mr-6 h-2.5 w-8 shrink-0 rounded-full" />
        </div>
      ))}
    </div>
  );
}
