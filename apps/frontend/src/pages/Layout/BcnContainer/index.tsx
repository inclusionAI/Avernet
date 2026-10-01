import { useExt } from '@/capabilities';
import Button from '@/components/Button';
import PageLoading from '@/components/PageLoading';
import LoginPromptModal from '@/pages/BcnHome/components/LoginPromptModal';
import { useBcnAuthGuard } from '@/pages/BcnHome/hooks/useBcnAuthGuard';
import { AppExt } from '@/shell';
import { Outlet, useLocation } from '@umijs/max';
import React from 'react';

export default function BcnContainer() {
  const { bcnProxyOnly } = useExt(AppExt).features;
  const location = useLocation();
  const shouldGuard =
    bcnProxyOnly && location.pathname.startsWith('/bcn/chat/');
  const authGuard = useBcnAuthGuard({ mode: 'force', enabled: shouldGuard });

  if (shouldGuard && authGuard.shouldBlockContent) {
    return (
      <div className="flex h-[100vh] items-center justify-center bg-slate-100 p-2">
        {authGuard.isCheckingAuth && authGuard.status === 'unknown' ? (
          <PageLoading fullScreen={false} />
        ) : (
          <Button
            type="button"
            onClick={authGuard.login}
            loading={authGuard.isLoadingLoginUrl}
          >
            立即登录
          </Button>
        )}
        <LoginPromptModal
          open={authGuard.promptOpen || authGuard.isUnauthenticated}
          closable={false}
          onLogin={authGuard.login}
          loadingLoginUrl={authGuard.isLoadingLoginUrl}
        />
      </div>
    );
  }

  return (
    <div className="h-[100vh] bg-slate-100 p-2">
      <Outlet />
    </div>
  );
}
