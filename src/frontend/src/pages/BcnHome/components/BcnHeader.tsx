/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * BcnHeader - 产品首页 sticky 顶栏
 *
 * 左：BCN logo（蓝圆角 + Bot 图标 + 双行文字）。
 * 中：锚点导航 pill 组（接入方式/特性/场景），IntersectionObserver 高亮当前 section + 平滑滚动。
 * 右：用户头像（花名首字，蓝底圆）+ 花名（取 useBcnIdentity；未登录降级为占位头像、不显示名字）。
 *
 * 注：本落地页为像素级还原设计稿，使用设计稿精确十六进制色、bespoke pill 控件，
 * 不套 lavender 色板 / Button whitelist（仅限本页的局部例外）。
 */

import { Bot } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { useBcnIdentity } from '../hooks/useBcnIdentity';

const NAV_ITEMS = [
  { label: '接入方式', href: '#access' },
  { label: 'Avernet', href: '#scenarios' },
];

const BcnHeader: React.FC = () => {
  const { nickName } = useBcnIdentity();
  const [activeSection, setActiveSection] = useState('#access');
  const avatarText = nickName?.slice(0, 1) || '用';

  useEffect(() => {
    const observers = NAV_ITEMS.map((item) => {
      const element = document.querySelector(item.href);
      if (!element) return null;
      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) setActiveSection(item.href);
          });
        },
        { rootMargin: '-120px 0px -55% 0px', threshold: 0.15 },
      );
      observer.observe(element);
      return observer;
    });
    return () => observers.forEach((o) => o?.disconnect());
  }, []);

  const scrollTo = (href: string) => {
    document
      .querySelector(href)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <header className="sticky top-0 z-30 border-b border-[#dbe4f0] bg-white/88 backdrop-blur-xl shadow-[0_10px_30px_-24px_rgba(29,78,216,0.35)]">
      <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-6 px-8 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[#1d4ed8] text-white shadow-sm">
            <Bot className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-semibold text-[#1a2332]">Avernet</p>
            <p className="text-xs tracking-[0.08em] text-[#8b95a5]">
              多智能体协作平台
            </p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <nav className="hidden items-center gap-2 rounded-full border border-[#e6edf7] bg-[#f8fbff] p-1.5 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.5)] md:flex">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.label}
                type="button"
                onClick={() => scrollTo(item.href)}
                className={`rounded-full px-4 py-2 text-sm font-medium transition-colors ${
                  activeSection === item.href
                    ? 'bg-white text-[#1d4ed8] shadow-sm'
                    : 'text-[#52606d] hover:bg-white hover:text-[#1d4ed8]'
                }`}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-3 rounded-full border border-[#e6edf7] bg-white px-3 py-2 shadow-sm">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#1d4ed8] text-sm font-semibold text-white">
              {avatarText}
            </div>
            {nickName && (
              <span className="text-sm font-semibold text-[#1a2332]">
                {nickName}
              </span>
            )}
          </div>
        </div>
      </div>
    </header>
  );
};

export default BcnHeader;
