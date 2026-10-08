/**
 * MobileNavContext - 移动端导航抽屉控制
 *
 * MainLayout 提供 openMobileNav 方法
 * 各页面可通过此 Context 打开左侧导航抽屉
 */
import React, { createContext, useCallback, useContext } from 'react';

interface MobileNavContextType {
  openMobileNav: () => void;
}

const MobileNavContext = createContext<MobileNavContextType | null>(null);

export function useMobileNav() {
  const context = useContext(MobileNavContext);
  if (!context) {
    throw new Error('useMobileNav must be used within MobileNavProvider');
  }
  return context;
}

interface MobileNavProviderProps {
  children: React.ReactNode;
  onOpenNav: () => void;
}

export function MobileNavProvider({
  children,
  onOpenNav,
}: MobileNavProviderProps) {
  const openMobileNav = useCallback(() => {
    onOpenNav();
  }, [onOpenNav]);

  return (
    <MobileNavContext.Provider value={{ openMobileNav }}>
      {children}
    </MobileNavContext.Provider>
  );
}
