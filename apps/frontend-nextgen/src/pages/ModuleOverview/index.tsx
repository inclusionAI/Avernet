import { ContentCard, PageHeader } from '@/components/Common';
import { Button, Empty } from '@/components/ui';
import { Card } from '@/components/ui/Card';
import { getNavigationItem } from '@/shell/navigation';
import { getRouteMeta } from '@/shell/routeMeta';
import { history, useLocation } from '@umijs/max';
import { ArrowRight, Construction, Plus } from 'lucide-react';
import React from 'react';

const ModuleOverviewPage: React.FC = () => {
  const location = useLocation();
  const item = getNavigationItem(location.pathname);
  const routeMeta = getRouteMeta(location.pathname);
  const title = routeMeta?.title ?? item?.label ?? '功能模块';

  return (
    <div className="app-scrollbar h-full overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto max-w-[1440px] space-y-6">
        <PageHeader
          eyebrow="初版交互骨架"
          title={title}
          description={item?.description ?? '模块页面正在接入'}
          actions={<Button leftIcon={<Plus className="h-4 w-4" />}>创建{title.replace('工坊', '')}</Button>}
        />
        <Card className="flex flex-wrap items-center gap-2 p-3 shadow-none">
          <Button variant="secondary">全部</Button>
          <Button variant="ghost">我创建的</Button>
          <Button variant="ghost">最近更新</Button>
          <div className="ml-auto">
            <Button variant="ghost" rightIcon={<ArrowRight className="h-4 w-4" />} onClick={() => history.back()}>
              返回上一页
            </Button>
          </div>
        </Card>
        <ContentCard>
          <Empty
            title={`${title}页面骨架已就绪`}
            description="App Shell、页头、工具栏、内容容器和状态反馈均已统一。后续业务数据应通过 Hook → Service → Gateway 接入。"
            icon={<Construction className="h-5 w-5" />}
            action={
              <Button variant="secondary" onClick={() => history.push('/components')}>
                查看组件案例
              </Button>
            }
          />
        </ContentCard>
      </div>
    </div>
  );
};

export default ModuleOverviewPage;
