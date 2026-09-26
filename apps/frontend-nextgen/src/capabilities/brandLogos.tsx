import AvernetWordmark from '@/assets/Images/avernet-header-logo.png';
import AvernetMark from '@/assets/Images/avernet-label-logo.png';

/**
 * Avernet 品牌视觉组件（Open Core 默认，经 getProductBrand capability 下发消费）。
 * 位图原稿为白底导出后转 alpha 的透明版（浅色/暗色底均无白块、无灰边伪影）；
 * internal overlay 的 TeamClaw 视觉组件在 src/extensions/internal.ts 内自带（随其剥离）。
 */

/** 横版 wordmark（mark + "Avernet" 字标一张图），页头等横向场景使用。 */
export function AvernetWordmarkLogo({ className }: { className?: string }) {
  return <img src={AvernetWordmark} alt="Avernet" className={className} />;
}

/** 方版 mark（纯 AV 节点图形），登录/空态等方形场景使用。 */
export function AvernetMarkLogo({ className }: { className?: string }) {
  return <img src={AvernetMark} alt="Avernet" className={className} />;
}
