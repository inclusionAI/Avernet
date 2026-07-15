/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * BcnHome - BCN 产品首页（开源专属落地页）
 *
 * 页面样式和静态内容以 GitHub 最新 src/frontend/src/pages/BcnHome 为基线；
 * 本仓仅在其基础上叠加一期 BCN 开源登录态逻辑。
 */

import { useExt } from '@/capabilities';
import { AppExt } from '@/shell';
import { useBcnHomeStore } from '@/stores/bcnHomeStore';
import React, { useEffect } from 'react';
import AccessSection from './components/AccessSection';
import BcnFooter from './components/BcnFooter';
import BcnHeader from './components/BcnHeader';
import HeroSection from './components/HeroSection';
import LoginPromptModal from './components/LoginPromptModal';
import ScenariosSection from './components/ScenariosSection';
import { useBcnAuthGuard } from './hooks/useBcnAuthGuard';
import { useRegisterToken } from './hooks/useRegisterToken';

const BcnHome: React.FC = () => {
  const { bcnProxyOnly } = useExt(AppExt).features;
  const { fetchToken } = useRegisterToken();
  const resetHomeState = useBcnHomeStore((state) => state.reset);
  const authGuard = useBcnAuthGuard({ mode: 'soft', enabled: bcnProxyOnly });

  useEffect(() => {
    if (!bcnProxyOnly) {
      fetchToken();
      return;
    }

    if (authGuard.isAuthenticated) {
      fetchToken();
      return;
    }

    if (authGuard.isUnauthenticated) {
      resetHomeState();
    }
  }, [
    authGuard.isAuthenticated,
    authGuard.isUnauthenticated,
    bcnProxyOnly,
    fetchToken,
    resetHomeState,
  ]);

  const handleEnterBcn = () => {
    if (bcnProxyOnly && !authGuard.isAuthenticated) return;
    window.open('/bcn/chat/list', '_blank');
  };

  return (
    <div className="h-full min-h-screen w-full overflow-y-auto bg-[#f5f7fa] scroll-smooth">
      <BcnHeader
        user={authGuard.user}
        isAuthenticated={!bcnProxyOnly || authGuard.isAuthenticated}
        isLoggingOut={authGuard.isLoggingOut}
        onLogin={authGuard.login}
        onLogout={authGuard.logout}
      />
      <div className="mx-auto max-w-[1200px] px-8 pb-8 pt-12">
        <HeroSection
          onEnterBcn={handleEnterBcn}
          disabled={bcnProxyOnly && !authGuard.isAuthenticated}
        />
        <div className="mt-16">
          <AccessSection />
        </div>
        <div className="mt-20">
          <ScenariosSection />
        </div>
        <div className="mt-12">
          <BcnFooter />
        </div>
      </div>

      <LoginPromptModal
        open={bcnProxyOnly && authGuard.promptOpen}
        closable
        onClose={() => authGuard.setPromptOpen(false)}
        onLogin={authGuard.login}
        loadingLoginUrl={authGuard.isLoadingLoginUrl}
      />
    </div>
  );
};

export default BcnHome;
