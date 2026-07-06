/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * BcnHome - BCN 产品首页（开源专属落地页）
 *
 * 开源构建下挂载于根路由 `/`（layout:false 独立壳），对内构建路由不透出
 * （内部 `/` → 工作台 redirect /home，本页代码仍在仓内）。
 *
 * 结构：sticky 顶栏 / 产品介绍 Hero / 接入方式（双命令卡）/ 产品特性 / 团队场景 / 页脚。
 * 【进入 BCN】→ 新标签页打开我的协作 /bcn/chat/list（BCN 路由保持现状）。
 *
 * 注：本落地页为像素级还原设计稿，使用设计稿精确十六进制色与 bespoke 控件，
 * 不套 lavender 色板 / Button whitelist（仅限本页的局部例外，详见各子组件注释）。
 */

import React, { useEffect } from 'react';
import AccessSection from './components/AccessSection';
import BcnFooter from './components/BcnFooter';
import BcnHeader from './components/BcnHeader';
import HeroSection from './components/HeroSection';
import ScenariosSection from './components/ScenariosSection';
import { useRegisterToken } from './hooks/useRegisterToken';

const BcnHome: React.FC = () => {
  const { fetchToken } = useRegisterToken();

  // 挂载即尝试拉取注册 token（登录态成功注入；未登录显示占位）
  useEffect(() => {
    fetchToken();
  }, [fetchToken]);

  const handleEnterBcn = () => {
    window.open('/bcn/chat/list', '_blank');
  };

  return (
    <div className="h-full min-h-screen w-full overflow-y-auto bg-[#f5f7fa] scroll-smooth">
      <BcnHeader />
      <div className="mx-auto max-w-[1200px] px-8 py-12">
        <HeroSection onEnterBcn={handleEnterBcn} />
        <div className="mt-16">
          <AccessSection />
        </div>
        <div className="mt-20">
          <ScenariosSection />
        </div>
        <div className="mt-20">
          <BcnFooter />
        </div>
      </div>
    </div>
  );
};

export default BcnHome;
