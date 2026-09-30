import { Button } from '@/components/ui';
import { history } from '@umijs/max';

// 未注册路由兜底页：访问不存在的子路由时展示，保留 AppShell 导航框架。
// react-router 通配符 * 优先级最低，不会覆盖已注册的精确路由。
export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center">
      <p className="text-7xl font-bold leading-none text-primary">404</p>
      <p className="mt-6 text-base font-medium text-foreground">页面不存在</p>
      <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
        你访问的页面可能已被移除、重命名，或暂时不可用。
      </p>
      <div className="mt-6 flex items-center gap-3">
        <Button onClick={() => history.push('/workspace')}>返回工作台</Button>
        <Button variant="outline" onClick={() => window.history.back()}>
          返回上一页
        </Button>
      </div>
    </div>
  );
}
