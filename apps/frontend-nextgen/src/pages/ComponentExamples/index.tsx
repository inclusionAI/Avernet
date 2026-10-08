import { ContentCard, PageHeader } from '@/components/Common';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Empty,
  Input,
  Modal,
  ModalContent,
  ModalTitle,
  ModalTrigger,
  Segmented,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  Spin,
  Textarea,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { history } from '@umijs/max';
import { AlertTriangle, Check, HelpCircle, Plus, RefreshCw, Trash2 } from 'lucide-react';
import React, { useState } from 'react';

const ComponentExamplesPage: React.FC = () => {
  const [segment, setSegment] = useState<'default' | 'loading' | 'empty'>('default');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('enabled');
  const simulateLoading = () => {
    setLoading(true);
    window.setTimeout(() => setLoading(false), 1000);
  };

  return (
    <div className="app-scrollbar h-full overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto max-w-[1200px] space-y-6">
        <PageHeader
          eyebrow="Design system"
          title="组件使用案例"
          description="以下组件由旧项目中可复用的原子抽象迁移并按新版 Token、可访问性和状态规范重写。页面代码只组合这些白名单组件。"
          actions={
            <Button variant="secondary" onClick={() => history.push('/workspace')}>
              返回工作台
            </Button>
          }
        />
        <ContentCard>
          <div className="grid gap-5 p-5 lg:grid-cols-2">
            <section>
              <h2 className="mt-0 text-base font-semibold">Button / Badge</h2>
              <div className="flex flex-wrap items-center gap-2">
                <Button leftIcon={<Plus className="h-4 w-4" />}>主要操作</Button>
                <Button variant="secondary">次要操作</Button>
                <Button variant="ghost">低频操作</Button>
                <Button variant="destructive" leftIcon={<Trash2 className="h-4 w-4" />}>
                  删除
                </Button>
                <Button loading>保存中</Button>
              </div>
              <div className="mt-4 flex gap-2">
                <Badge tone="primary">运行中</Badge>
                <Badge tone="success">已完成</Badge>
                <Badge tone="warning">待确认</Badge>
                <Badge tone="error">失败</Badge>
              </div>
            </section>
            <section>
              <h2 className="mt-0 text-base font-semibold">Input / Segmented</h2>
              <Input aria-label="案例搜索" placeholder="搜索组件名称" />
              <Segmented
                className="mt-3"
                value={segment}
                options={[
                  { value: 'default', label: '默认态' },
                  { value: 'loading', label: '加载态' },
                  { value: 'empty', label: '空态' },
                ]}
                onChange={setSegment}
              />
            </section>
          </div>
        </ContentCard>
        <ContentCard>
          <div className="grid gap-5 p-5 lg:grid-cols-3">
            <section className="space-y-3">
              <h2 className="mt-0 text-base font-semibold">Select / Textarea</h2>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger aria-label="组件状态">
                  <SelectValue placeholder="选择状态" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="enabled">启用</SelectItem>
                  <SelectItem value="disabled">停用</SelectItem>
                </SelectContent>
              </Select>
              <Textarea aria-label="组件备注" placeholder="输入组件备注" rows={3} />
            </section>
            <section className="space-y-3">
              <h2 className="mt-0 text-base font-semibold">Tooltip</h2>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="ghost" size="icon" aria-label="查看提示">
                      <HelpCircle aria-hidden className="size-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Tooltip 必须支持 hover 与键盘 focus。</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </section>
            <section className="space-y-3">
              <h2 className="mt-0 text-base font-semibold">Modal</h2>
              <Modal>
                <ModalTrigger asChild>
                  <Button>打开 Modal</Button>
                </ModalTrigger>
                <ModalContent>
                  <ModalTitle>组件规范</ModalTitle>
                  <p className="m-0 text-sm text-muted-foreground">Modal 使用统一焦点管理和语义 Token。</p>
                </ModalContent>
              </Modal>
            </section>
          </div>
        </ContentCard>
        <div className="grid gap-6 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>标准卡片</CardTitle>
                <CardDescription>普通内容卡片使用边框，不滥用阴影。</CardDescription>
              </div>
              <Badge tone="success">
                <Check className="mr-1 h-3 w-3" />
                规范
              </Badge>
            </CardHeader>
            <CardContent>
              <p className="m-0 text-sm leading-6 text-[var(--color-muted)]">
                卡片内边距、标题层级和操作区域均来自统一组件。
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              {segment === 'loading' ? (
                <Spin tip="正在加载数据" />
              ) : segment === 'empty' ? (
                <Empty compact title="暂无数据" description="空态需要说明原因并提供下一步。" />
              ) : (
                <div className="space-y-3">
                  <Skeleton.ListItem />
                  <Skeleton.Line className="w-4/5" />
                  <Skeleton.Line className="w-2/3" />
                </div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>操作反馈</CardTitle>
                <CardDescription>局部操作应显示 loading 并阻止重复提交。</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <Button loading={loading} leftIcon={<RefreshCw className="h-4 w-4" />} onClick={simulateLoading}>
                模拟刷新
              </Button>
              <div className="mt-4 flex gap-2 rounded-lg bg-[var(--color-warning-soft)] p-3 text-sm text-[var(--color-warning)]">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>危险操作应接入 ConfirmDialog，本骨架暂不提供真实删除动作。</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
};

export default ComponentExamplesPage;
