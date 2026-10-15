import AvernetWordmark from '@/assets/Images/avernet-header-logo.png';
import AvernetClawMark from '@/assets/Images/avernet-mark-claw.png';
import AvernetMark from '@/assets/Images/avernet-label-logo.png';
import { cn } from '@/utils/cn';

/**
 * Avernet 品牌视觉组件（Open Core 默认，经 getProductBrand capability 下发消费）。
 * 位图原稿为新版品牌套件（墨黑笔画 + 蓝绿节点）透明底直出，浅色/暗色底均无白块；
 * internal overlay 的 TeamClaw 视觉组件在 src/extensions/internal.ts 内自带（随其剥离）。
 */

/** 横版 wordmark（mark + "Avernet" 字标一张图），页头等横向场景使用。
 *  compact（左侧导航宽松态顶部紧凑位）渲染同一 mark 方版（426×338，宽 24px 时高约 19px）。 */
export function AvernetWordmarkLogo({ className, compact }: { className?: string; compact?: boolean }) {
  if (compact)
    return <img src={AvernetClawMark} alt="Avernet" className={cn('w-6 shrink-0', className)} />;
  return <img src={AvernetWordmark} alt="Avernet" className={className} />;
}

/** 方版 mark（纯 AV 节点图形），登录/空态等方形场景使用。 */
export function AvernetMarkLogo({ className }: { className?: string }) {
  return <img src={AvernetMark} alt="Avernet" className={className} />;
}
