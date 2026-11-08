import { IconButton } from '@/components/ui';
import { Menu } from 'lucide-react';
import { createContext, useContext, useLayoutEffect } from 'react';

/** AppShell → 页头的一级导航通道：页头就地渲染 <lg 入口（置于「打开会话列表」左），壳据此抑制 h-12 兜底条带。 */
export interface PrimaryNavApi {
  /** 打开壳的一级导航抽屉（与兜底条带共用同一条抽屉）。 */
  openNav: () => void;
  /** 内联入口挂载/卸载登记（登记数 >0 时兜底条带不渲染）。 */
  mountNavEntry: () => void;
  unmountNavEntry: () => void;
}

export const PrimaryNavContext = createContext<PrimaryNavApi | null>(null);

/** 壳外与精简路由（navigationVisibility=hidden）下为 null：页头可无条件挂载入口，上下文缺失自然降级。 */
export function usePrimaryNavApi(): PrimaryNavApi | null {
  return useContext(PrimaryNavContext);
}

/** <lg 一级导航入口钮：28px(sm) 对齐页头 Chevron；登记走 layout effect，先于绘制收敛条带、无闪现窗口。 */
export function PrimaryNavToggle() {
  const api = usePrimaryNavApi();
  useLayoutEffect(() => {
    if (!api) return;
    api.mountNavEntry();
    return () => api.unmountNavEntry();
  }, [api]);
  if (!api) return null;
  return (
    <IconButton
      label="打开导航"
      icon={<Menu className="h-4 w-4" />}
      size="sm"
      className="shrink-0 lg:hidden"
      onClick={api.openNav}
    />
  );
}