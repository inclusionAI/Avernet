// 空间管理独立页（split-admin-space-ticket-pages）：原 /admin 单页「空间管理」Tab 拆为独立路由 /space-admin，
// 页面体原样复用 AdminSpacesView，不占导航位——入口见 SpaceSwitcher 弹层标题行「空间管理」设置 icon。
// Open Core（getAdminSections.spaces=false）直达时 replace 回落 /ticket-center，
// 与原单页「隐藏 Tab 深链回落」语义同构（阿里云形态仅暴露通知中心）。
import { getCapabilities } from '@/capabilities';
import { history } from '@umijs/max';
import { useEffect } from 'react';
import { AdminSpacesView } from '../Admin/Spaces';

function SpaceAdminPage() {
  const { spaces } = getCapabilities().getAdminSections().value;

  useEffect(() => {
    if (!spaces) history.replace('/ticket-center');
  }, [spaces]);

  if (!spaces) return null;

  return (
    <div className="flex h-full flex-col">
      {/* 内容区容器与原 /admin 单页一致：白底继承 AppShell，灰底内边距滚动区 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-6 pb-10">
        <AdminSpacesView />
      </div>
    </div>
  );
}

export default SpaceAdminPage;
