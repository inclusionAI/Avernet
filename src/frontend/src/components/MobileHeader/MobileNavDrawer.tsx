/**
 * MobileNavDrawer - 移动端左侧导航抽屉
 *
 * 样式规范详见 docs/移动端体验规范.md §4.1
 */
import { useExt } from '@/capabilities';
import { Drawer, DrawerContent } from '@/components/ui/drawer';
import { AppExt, buildMenus } from '@/shell';
import { cn } from '@/utils/utils';
import { history } from '@umijs/max';
import {
  MessageCircle,
  Monitor,
  PackageOpen,
  ScrollText,
  Smartphone,
  SquareTerminal,
} from 'lucide-react';
import React, { useMemo } from 'react';

export type TabId =
  | 'assistant'
  | 'market'
  | 'capability'
  | 'cron'
  | 'privatechat'
  | 'servicebot'
  | 'groupchat'
  | 'expertmarket'
  | 'notify'
  | 'robot'
  | 'release-notes';

interface NavItem {
  id: TabId;
  icon: React.ElementType;
  label: string;
  path: string;
  disabled?: boolean;
  type?: 'divider';
  external?: boolean;
}

/**
 * 移动端导航菜单项
 *
 * ⚠️ 重要：当 MainLayout 中的 TABS 更新时，需要同步更新此数组
 * 规范：与 MainLayout 的菜单顺序和分组保持一致
 * - 上方菜单：个人助手、智能专家、我的协作
 * - 分隔符
 * - 下方菜单：Bot 广场、能力市场、定时任务
 */
interface NavItemWithBeta extends NavItem {
  beta?: boolean;
}

// 移动端导航菜单与桌面 MainLayout 共用模块注册表（AppExt.modules），
// 组件内经 buildMenus 生成（见 useMemo navItems），消除原「手动同步 TABS」的双维护。
// 注：原 capability「能力管理」项随此迁移下线（桌面侧本就注释掉）。

/**
 * 产品获取链接「元信息」（label/icon）。
 * 真实 href 由 AppExt.resources.productLinks 注入：开源默认 null → 该项不渲染；
 * 内部经 src/internal/resources.ts extend 真实地址（TUI / 移动端 / 桌面端）。
 */
const PRODUCT_LINK_META: {
  label: string;
  icon: React.ElementType;
  key: 'tui' | 'mobile' | 'desktop';
}[] = [
  { label: 'TUI', icon: SquareTerminal, key: 'tui' },
  { label: '移动端', icon: Smartphone, key: 'mobile' },
  { label: '桌面端', icon: Monitor, key: 'desktop' },
];

interface MobileNavDrawerProps {
  open: boolean;
  onClose: () => void;
  activeTab?: TabId | null;
  onOpenReleaseNotes?: () => void;
}

export function MobileNavDrawer({
  open,
  onClose,
  activeTab,
  onOpenReleaseNotes,
}: MobileNavDrawerProps) {
  // 与桌面共用模块注册表：开源形态下内部模块未注入 → 自动消失。
  const appModules = useExt(AppExt).modules;
  // 产品获取链接走 resources 契约：href 为 null 的项不渲染（开源默认全 null）。
  const { productLinks, customerServiceRobotUrl } = useExt(AppExt).resources;
  const productItems = PRODUCT_LINK_META.map((m) => ({
    label: m.label,
    icon: m.icon,
    href: productLinks?.[m.key] ?? null,
  })).filter(
    (p): p is { label: string; icon: React.ElementType; href: string } =>
      Boolean(p.href),
  );
  const navItems = useMemo<NavItemWithBeta[]>(() => {
    const items: NavItemWithBeta[] = [];
    let prevGroup: string | undefined;
    for (const m of buildMenus(appModules)) {
      if (prevGroup && m.group !== prevGroup) {
        items.push({ type: 'divider' } as NavItemWithBeta);
      }
      items.push({
        id: m.id as TabId,
        icon: m.icon as React.ElementType,
        label: m.label,
        path: m.path,
        beta: m.beta,
      });
      prevGroup = m.group;
    }
    return items;
  }, [appModules]);

  const handleNavClick = (item: NavItemWithBeta) => {
    if (item.external) {
      // 外部链接：直接跳转
      window.location.href = item.path;
    } else {
      // 内部路由：使用 history
      history.push(item.path);
    }
    onClose();
  };

  return (
    <Drawer open={open} onOpenChange={onClose}>
      <DrawerContent
        position="left"
        width={260}
        title="菜单"
        overlay
        className="p-0"
      >
        <nav className="flex flex-col py-2">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;

            if (item.type === 'divider') {
              return (
                <div key={item.id} className="my-2 border-t border-slate-100" />
              );
            }

            return (
              <React.Fragment key={item.id}>
                <button
                  type="button"
                  onClick={() => handleNavClick(item)}
                  disabled={item.disabled}
                  data-aspm-click="ca114852.da193957"
                  data-aspm-desc="MobileNavDrawer-导航项"
                  data-aspm-param={``}
                  data-aspm-expo
                  className={cn(
                    'flex items-center gap-3 px-4 py-3.5 text-left transition-colors active:bg-slate-100',
                    isActive
                      ? 'bg-lavender-50 text-lavender-600'
                      : 'text-slate-700 hover:bg-slate-50',
                    item.disabled && 'opacity-50 cursor-not-allowed',
                  )}
                >
                  <Icon
                    size={20}
                    className={
                      isActive ? 'text-lavender-600' : 'text-slate-500'
                    }
                  />
                  <span className="text-sm font-medium">{item.label}</span>
                  {/* Beta 标签 */}
                  {item.beta && (
                    <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-600 font-medium">
                      Beta
                    </span>
                  )}
                  {isActive && !item.beta && (
                    <div className="ml-auto w-2 h-2 rounded-full bg-lavender-500" />
                  )}
                </button>
              </React.Fragment>
            );
          })}

          {/* 产品获取（无可用链接时整段不渲染，如开源形态 resources.productLinks 全为 null） */}
          {productItems.length > 0 && (
            <div className="px-4 py-3.5">
              <div className="flex items-center gap-3 text-slate-700">
                <PackageOpen size={20} className="text-slate-500" />
                <span className="text-sm font-medium">产品获取</span>
              </div>
              <div className="mt-2 ml-8 space-y-1">
                {productItems.map((item) => {
                  const Icon = item.icon;
                  return (
                    <a
                      key={item.label}
                      href={item.href}
                      target="_blank"
                      rel="noreferrer"
                      data-aspm-click="ca114852.da193959"
                      data-aspm-desc="MobileNavDrawer-产品链接"
                      data-aspm-param={``}
                      data-aspm-expo
                      className="flex items-center gap-2 rounded-lg px-3 py-2 text-slate-600 hover:bg-slate-50"
                    >
                      <Icon size={16} />
                      <span className="text-sm">{item.label}</span>
                    </a>
                  );
                })}
              </div>
            </div>
          )}

          {/* 版本发布说明 */}
          <button
            type="button"
            onClick={() => {
              onOpenReleaseNotes?.();
              onClose();
            }}
            data-aspm-click="ca114852.da193960"
            data-aspm-desc="MobileNavDrawer-版本发布说明按钮"
            data-aspm-param={``}
            data-aspm-expo
            className="flex items-center gap-3 px-4 py-3.5 text-left transition-colors active:bg-slate-100 text-slate-700 hover:bg-slate-50 w-full"
          >
            <ScrollText size={20} className="text-slate-500" />
            <span className="text-sm font-medium">版本发布说明</span>
          </button>

          {/* 答疑机器人：链接走 resources 契约，开源默认 null 时不渲染 */}
          {customerServiceRobotUrl && (
            <a
              href={customerServiceRobotUrl}
              data-aspm-click="ca114852.da193961"
              data-aspm-desc="MobileNavDrawer-答疑机器人链接"
              data-aspm-param={``}
              data-aspm-expo
              className="flex items-center gap-3 px-4 py-3.5 text-left transition-colors active:bg-slate-100 text-slate-700 hover:bg-slate-50"
            >
              <MessageCircle size={20} className="text-slate-500" />
              <span className="text-sm font-medium">答疑机器人</span>
            </a>
          )}
        </nav>
      </DrawerContent>
    </Drawer>
  );
}
