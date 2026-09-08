import { useExternalAuthBoot } from '@/hooks/useExternalAuthGuard';
import { useInviteCodeGateBoot } from '@/hooks/useInviteCodeGate';
import { AppShell } from '@/shell/AppShell';
import { Outlet } from '@umijs/max';

export default function AppLayout() {
  // 全系统外部登录主动 boot（仅 oauth-provider 策略；ace-gateway 不触发，内部走 ACE 反应式）。见 design 决策 6。
  useExternalAuthBoot();
  // 全系统邀请码门禁主动 boot（仅 oauth-provider + inviteCodeGate enabled）：登录态确认后查 /me，未绑则弹门禁。
  // 与登录 boot 无竞态（登录先于门禁，本 boot 等 authenticated 才触发）。见 add-invite-code-gate design 决策 1。
  useInviteCodeGateBoot();
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
