// 管理后台入口（单页 `/admin` + 顶部下划线 tab：空间管理 / 工单中心）。
// 消费 `?tab=spaces|work-orders` query 切主 tab（PRD 单页意图，视觉规格 §1）。
// 视觉对齐 PRD：主 tab 为下划线式，位于白色 header 条上；内容区灰底带内边距。
// 形态级 Tab 可见性经 `getAdminSections` capability 解析：Open Core（阿里云部署）隐藏【空间管理】、
// 仅留【工单中心】；internal overlay 两 Tab 均在。隐藏 Tab 的深链（?tab=spaces）回落首个可见 Tab。
import { getCapabilities } from '@/capabilities';
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

const AdminPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  // 形态级 Tab 可见性：Open Core（阿里云部署）默认 { spaces:false, workOrders:true }；
  // internal overlay 覆盖为全 true（两 Tab 均在，与改造前一致）。
  const { spaces, workOrders } = getCapabilities().getAdminSections().value;
  const visibleTabs = TAB_OPTIONS.filter((t) => (t.value === 'spaces' ? spaces : workOrders));
  // 默认 Tab = 首个可见 Tab（Open Core 落 work-orders，internal 落 spaces——维持现状）；
  // 两 Tab 均隐藏的理论边界回落 work-orders（workOrders 两形态恒 true，不触发）。
  const defaultTab = visibleTabs[0]?.value ?? 'work-orders';

  const raw = searchParams.get('tab');
  const tabVisible = visibleTabs.some((t) => t.value === raw);
  const tab: AdminTab = tabVisible ? (raw as AdminTab) : defaultTab;

  useEffect(() => {
    // 深链回落：raw 缺省/非法/指向被隐藏 Tab 时，写回首可见 Tab（replace 保持链接可分享，不暴露隐藏 Tab）。
    if (!tabVisible) {
      setSearchParams({ tab: defaultTab }, { replace: true });
    }
  }, [tabVisible, defaultTab, setSearchParams]);

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
        <UnderlineTabs<AdminTab> value={tab} options={visibleTabs} onChange={changeTab} />
      </div>
      {/* 内容区灰底 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-6 pb-10">
        {tab === 'spaces' ? <AdminSpacesView /> : <AdminWorkOrdersView />}
      </div>
    </div>
  );
};

export default AdminPage;
