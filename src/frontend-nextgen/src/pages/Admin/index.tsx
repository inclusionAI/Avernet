// 管理后台入口（单页 `/admin` + 顶部下划线 tab：空间管理 / 工单中心）。
// 消费 `?tab=spaces|work-orders` query 切主 tab（PRD 单页意图，视觉规格 §1）。
// 视觉对齐 PRD：主 tab 为下划线式，位于白色 header 条上；内容区灰底带内边距。
import { UnderlineTabs } from '@/components/Admin/Tabs';
import { useSearchParams } from '@umijs/max';
import { useEffect } from 'react';
import { AdminSpacesView } from './Spaces';
import { AdminWorkOrdersView } from './WorkOrders';

type AdminTab = 'spaces' | 'work-orders';

const TAB_OPTIONS: { value: AdminTab; label: string }[] = [
  { value: 'spaces', label: '空间管理' },
  { value: 'work-orders', label: '工单中心' },
];

function isTab(v: string | null): v is AdminTab {
  return v === 'spaces' || v === 'work-orders';
}

const AdminPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get('tab');
  const tab: AdminTab = isTab(raw) ? raw : 'spaces';

  useEffect(() => {
    // 缺省本地 tab 为 spaces 时补上 query，保持链接可分享
    if (!isTab(raw)) setSearchParams({ tab: 'spaces' }, { replace: true });
  }, [raw, setSearchParams]);

  // 当前空间由全局空间上下文承载：AppShell 进入「管理」区域时 initSpaceContext() 已初始化，
  // 切换器 switchSpaceContext() 写 localStorage，刷新/重进均从 localStorage 还原（spec AC51/T4.2）。
  // 此处不复位空间，以免覆盖用户刚切换的选择。

  const changeTab = (next: AdminTab) => {
    setSearchParams({ tab: next }, { replace: true });
  };

  return (
    <div className="flex h-full flex-col bg-muted">
      {/* 主 tab frosted header 条：白磨砂底 + 发丝下边线 */}
      <div className="z-10 border-b border-border bg-background/80 px-6 backdrop-blur-md">
        <UnderlineTabs<AdminTab> value={tab} options={TAB_OPTIONS} onChange={changeTab} />
      </div>
      {/* 内容区灰底 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-6 pb-10">
        {tab === 'spaces' ? <AdminSpacesView /> : <AdminWorkOrdersView />}
      </div>
    </div>
  );
};

export default AdminPage;
