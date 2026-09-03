import { useExternalAuthBoot } from '@/hooks/useExternalAuthGuard';
import { AppShell } from '@/shell/AppShell';
import { Outlet } from '@umijs/max';

export default function AppLayout() {
  // 全系统外部登录主动 boot（仅 oauth-provider 策略；ace-gateway 不触发，内部走 ACE 反应式）。见 design 决策 6。
  useExternalAuthBoot();
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
