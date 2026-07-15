/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * BcnHeader - 产品首页 sticky 顶栏。
 * 页面导航和视觉以 GitHub 最新 BcnHome 为基线，右侧叠加登录/用户菜单。
 */

import Button from '@/components/Button';
import type { BcnAuthUser } from '@/stores/bcnAuthStore';
import { LogOut } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';

const NAV_ITEMS = [
  { label: '接入方式', href: '#access' },
  { label: 'Avernet', href: '#scenarios' },
];

interface BcnHeaderProps {
  user: BcnAuthUser | null;
  isAuthenticated: boolean;
  isLoggingOut?: boolean;
  onLogin: () => void;
  onLogout: () => void;
}

const BcnHeader: React.FC<BcnHeaderProps> = ({
  user,
  isAuthenticated,
  isLoggingOut,
  onLogin,
  onLogout,
}) => {
  const [activeSection, setActiveSection] = useState('#access');
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const displayName = user?.name || user?.userId || '用户';
  const avatarText = displayName.slice(0, 1);

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

  useEffect(() => {
    if (!menuOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [menuOpen]);

  const scrollTo = (href: string) => {
    document
      .querySelector(href)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <header className="sticky top-0 z-30 border-b border-[#dbe4f0] bg-white/88 shadow-[0_10px_30px_-24px_rgba(29,78,216,0.35)] backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-6 px-8 py-4">
        <div className="flex items-center gap-3">
          <img
            src="/Avernet-logotitle.png"
            alt="Avernet"
            className="h-11 w-auto object-contain"
          />
        </div>

        <div className="flex items-center gap-4">
          <nav className="hidden items-center gap-2 rounded-full border border-[#e6edf7] bg-[#f8fbff] p-1.5 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.5)] md:flex">
            {NAV_ITEMS.map((item) => (
              <Button
                key={item.label}
                type="button"
                variant="default"
                ghost
                onClick={() => scrollTo(item.href)}
                className={`rounded-full px-4 py-2 text-sm font-medium transition-colors ${
                  activeSection === item.href
                    ? 'bg-white text-[#1d4ed8] shadow-sm'
                    : 'text-[#52606d] hover:bg-white hover:text-[#1d4ed8]'
                }`}
              >
                {item.label}
              </Button>
            ))}
          </nav>

          {!isAuthenticated ? (
            <Button
              type="button"
              onClick={onLogin}
              className="rounded-full bg-[#1d4ed8] px-5 text-sm font-semibold text-white hover:bg-[#1e40af]"
            >
              登录
            </Button>
          ) : (
            <div ref={menuRef} className="relative">
              <Button
                type="button"
                variant="default"
                soft
                onClick={() => setMenuOpen((open) => !open)}
                className="h-auto rounded-full border border-[#e6edf7] bg-white px-3 py-2 shadow-sm hover:bg-white"
              >
                {user?.avatar ? (
                  <img
                    src={user.avatar}
                    alt={displayName}
                    className="h-9 w-9 rounded-full object-cover"
                  />
                ) : (
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-[#1d4ed8] text-sm font-semibold text-white">
                    {avatarText}
                  </span>
                )}
                <span className="max-w-[120px] truncate text-sm font-semibold text-[#1a2332]">
                  {displayName}
                </span>
              </Button>

              {menuOpen && (
                <div className="absolute right-0 top-[calc(100%+8px)] w-44 rounded-2xl border border-[#e6edf7] bg-white p-2 shadow-lg">
                  <Button
                    type="button"
                    variant="danger"
                    ghost
                    fullWidth
                    loading={isLoggingOut}
                    leftIcon={<LogOut className="h-4 w-4" />}
                    onClick={onLogout}
                    className="justify-start rounded-xl px-3 py-2 text-sm"
                  >
                    退出登录
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
};

export default BcnHeader;
