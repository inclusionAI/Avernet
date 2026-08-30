import { AppShell } from '@/shell/AppShell';
import { Outlet } from '@umijs/max';

export default function AppLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
