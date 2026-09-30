import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui';
import { useExternalAuth } from '@/hooks/useExternalAuth';
import { useEffect } from 'react';

/**
 * 欢迎页 sticky 顶栏(Header-lite):brand Logo + 登录入口。
 * 仅 `oauth-provider` 策略挂载登录探活与按钮(internal 不会到达本页,此为纵深防御);
 * 登录提示复用全局 ExternalLoginPromptModal,本页不自建弹窗(design 决策 6)。
 * 已登录态一期仅隐藏登录按钮(取实现最简,不展示用户信息)。
 */
export function WelcomeHeader() {
  const brand = getCapabilities().getProductBrand().value;
  const isOauthProvider = getCapabilities().getLoginStrategy().value === 'oauth-provider';
  const { status, checkAuth, login } = useExternalAuth();

  // 挂载一次性探活,纠正「已登录但状态未知 → 误显示登录按钮」;未被全局 ExternalLoginPromptModal
  // 的 soft 提醒复用(本页不挂 useExternalAuthGuard,避免落地页自动弹登录弹窗)。
  useEffect(() => {
    if (!isOauthProvider) return;
    void checkAuth();
  }, [checkAuth, isOauthProvider]);

  return (
    <header className="border-b border-border bg-background/95 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1200px] items-center justify-between gap-6 px-8 py-4">
        <brand.Logo className="h-9 w-auto" />
        {isOauthProvider && status !== 'authenticated' && (
          <Button size="sm" onClick={() => void login()}>
            登录
          </Button>
        )}
      </div>
    </header>
  );
}
