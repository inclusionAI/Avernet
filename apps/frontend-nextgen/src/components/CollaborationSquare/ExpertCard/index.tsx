import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { ExpertSummary } from '@/domain/expertMarket';
import { Bot, ChevronRight } from 'lucide-react';
import { Link } from '@umijs/max';
import type { ReactElement } from 'react';

/** 标签展示上限：超出折叠为 +N，保证卡片网格行高节奏稳定（描述/标签区不无限生长）。 */
const VISIBLE_TAG_COUNT = 3;

/** 不改变卡片尺寸，悬浮或聚焦时展示完整文本（与群卡/Bot 卡同一交互约定）。 */
function FullTextTooltip({ text, children }: { text: string; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="top" className="max-w-[min(24rem,calc(100vw-2rem))] whitespace-normal break-words text-left leading-5">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}

export interface ExpertCardProps {
  expert: ExpertSummary;
  /** 详情链接（调用方携带当前筛选/分页参数生成）；卡片纯展示，不自行拼路由。 */
  detailHref: string;
}

/**
 * 专家市场卡片：沿用公开协作群卡（dmore artboard-004）的白底描边语言——1px #EEE 描边、16px 圆角、
 * 无静态阴影 hover 才出；头部为「专家名（14/600）+ 右上分类胶囊」，其下为两行描述与标签行，
 * 底部分割线上方左侧「已创建 N 个 Bot」计数 chip（对应群卡「成员 N」槽位），右侧「查看详情」文本钮。
 */
export default function ExpertCard({ expert, detailHref }: ExpertCardProps) {
  const description = expert.description.trim() || '暂无描述';
  const hiddenTagCount = expert.tags.length - VISIBLE_TAG_COUNT;
  return (
    <TooltipProvider delayDuration={200}>
      <Card className="flex h-full min-w-0 flex-col rounded-2xl border border-divider bg-card p-4 pb-3 transition-shadow hover:shadow-sm">
        {/* 头部行：专家名 + 右上分类胶囊（与群卡类型胶囊同规格槽位）。 */}
        <div className="flex min-w-0 items-start justify-between gap-2">
          <FullTextTooltip text={expert.name}>
            <span tabIndex={0} className="min-w-0 flex-1 truncate text-sm font-semibold leading-[18px] text-foreground">
              {expert.name}
            </span>
          </FullTextTooltip>
          <span className="shrink-0">
            <Badge tone="primary">{expert.category}</Badge>
          </span>
        </div>
        {/* 描述（与群/Bot 卡同规格：12px/18、两行截断，悬浮看全文）。 */}
        <FullTextTooltip text={description}>
          <p tabIndex={0} className="m-0 mt-3 line-clamp-2 break-words text-xs leading-[18px] text-muted-foreground">
            {description}
          </p>
        </FullTextTooltip>
        {/* 标签行：软灰 chip，超过 {@link VISIBLE_TAG_COUNT} 个折叠为 +N。 */}
        <div className="mt-2 flex min-w-0 flex-wrap gap-1.5">
          {expert.tags.slice(0, VISIBLE_TAG_COUNT).map((tag) => (
            <span key={tag} className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
              {tag}
            </span>
          ))}
          {hiddenTagCount > 0 && (
            <span
              aria-label={`还有 ${hiddenTagCount} 个标签`}
              className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground"
            >
              +{hiddenTagCount}
            </span>
          )}
        </div>
        {/* 底部行：分割线上方，左计数 chip + 右「查看详情」hover-only 文本钮（对应群卡槽位）。 */}
        <div className="mt-auto flex items-center justify-between gap-3 border-t border-divider pt-2.5">
          <span className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
            <Bot aria-hidden className="h-3 w-3 shrink-0" />
            <span className="truncate">已创建 {expert.creation_count} 个 Bot</span>
          </span>
          <Button
            asChild
            variant="ghost"
            size="sm"
            className="h-6 shrink-0 gap-0.5 rounded-md px-1.5 text-xs font-medium text-primary hover:text-primary"
          >
            <Link to={detailHref}>
              查看详情
              <ChevronRight aria-hidden className="h-3.5 w-3.5" />
            </Link>
          </Button>
        </div>
      </Card>
    </TooltipProvider>
  );
}

/**
 * 专家卡加载骨架：逐区镜像真实专家卡——头部名称行 + 右上分类胶囊位、两行描述、
 * 三个标签 chip 位、分割线底部行（左计数 chip 位 + 右 CTA 位），loading→loaded 零跳版；
 * 容器与真卡同描边规格。占位块统一走 ui/Skeleton（脉冲叠色），本文件不直写 animate-*。
 */
export function ExpertCardSkeleton() {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-2xl border border-divider bg-card p-4 pb-3">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <Skeleton.Line className="h-[18px] min-w-0 flex-1" />
        <Skeleton.Block className="h-5 w-[65px] shrink-0 rounded-full" />
      </div>
      <div className="mt-3 space-y-1.5">
        <Skeleton.Line className="h-[18px]" />
        <Skeleton.Line className="h-[18px] w-3/4" />
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Skeleton.Block className="h-6 w-16 rounded-md" />
        <Skeleton.Block className="h-6 w-12 rounded-md" />
        <Skeleton.Block className="h-6 w-14 rounded-md" />
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-divider pt-2.5">
        <Skeleton.Block className="h-4 w-28 rounded-full" />
        <Skeleton.Block className="h-6 w-[68px] rounded-md" />
      </div>
    </div>
  );
}
