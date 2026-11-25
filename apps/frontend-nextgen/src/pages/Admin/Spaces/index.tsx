// 空间管理视图：搜索 + 创建空间 + 卡片网格 + 空间详情 Drawer（含 SpaceMemberList）。
// 视觉对齐 PRD：工具条右对齐，搜索框紧挨创建空间按钮；无类型筛选（PRD 无全部/团队/个人筛选）。
import { SpaceCard } from '@/components/Admin/SpaceCard';
import { SpaceCreateForm } from '@/components/Admin/SpaceCreateForm';
import { SpaceMemberList } from '@/components/Admin/SpaceMemberList';
import { Button, Drawer, DrawerContent, DrawerTitle, Empty, Input, Pagination, Skeleton } from '@/components/ui';
import { Card } from '@/components/ui/Card';
import { useAdmin } from '@/hooks/useAdmin';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { extractFriendlyErrorMessage } from '@/utils/requestErrorHandler';
import { useNavigate } from '@umijs/max';
import { ArrowLeft, Plus, Search, X } from 'lucide-react';
import { useState } from 'react';

export function AdminSpacesView() {
  const {
    keyword,
    pageNo,
    pageSize,
    items,
    total,
    loading,
    error,
    currentSpace,
    members,
    membersLoading,
    onKeywordChange,
    changePage,
    changePageSize,
    createTeamSpace,
    openSpaceDetail,
    closeSpaceDetail,
    deleteSpace,
    addMembers,
    addMembersLoading,
    addMembersDisabledReason,
    removeMember,
    updateRole,
    requestJoin,
    fetchList,
  } = useAdmin();
  const currentSpaceId = useSpaceContext((s) => s.currentSpaceId);
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <div className="flex min-h-full flex-col overflow-hidden rounded-xl bg-card">
      <div className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="size-5" aria-label="返回" onClick={() => navigate(-1)}>
            <ArrowLeft size={16} aria-hidden />
          </Button>
          <h1 className="text-base font-medium leading-6">空间管理</h1>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="relative">
            <Search
              size={14}
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              className="h-8 w-full sm:w-[359px] pl-8 pr-7 text-xs"
              placeholder="搜索空间名称"
              value={keyword}
              onChange={(e) => onKeywordChange(e.target.value)}
              aria-label="搜索空间"
            />
            {keyword && (
              <Button
                variant="ghost"
                size="icon"
                aria-label="清空搜索"
                className="absolute right-0.5 top-1/2 h-6 w-6 -translate-y-1/2 text-muted-foreground"
                onClick={() => onKeywordChange('')}
              >
                <X size={14} />
              </Button>
            )}
          </div>
          <Button
            variant="primary"
            className="h-8 px-3 text-sm"
            leftIcon={<Plus size={14} />}
            onClick={() => setCreateOpen(true)}
          >
            创建团队空间
          </Button>
        </div>
      </div>

      <div className="flex-1 p-5">
        {/* 列表 */}
        {loading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton.Card key={i} />
            ))}
          </div>
        ) : error ? (
          <Card className="flex items-center justify-between gap-3 border-destructive/30 bg-destructive/5 p-4 shadow-none">
            <div className="min-w-0 text-sm text-destructive">{extractFriendlyErrorMessage(error)}</div>
            <Button size="sm" variant="primary" onClick={() => void fetchList()}>
              重试
            </Button>
          </Card>
        ) : items.length === 0 ? (
          <Empty
            title="暂无空间"
            description="可创建团队空间或搜索加入"
            action={
              keyword ? (
                <Button size="sm" variant="primary" onClick={() => onKeywordChange('')}>
                  清除搜索
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {items.map((space) => (
              <SpaceCard
                key={space.spaceId || space.spaceCode}
                space={space}
                isCurrent={space.spaceId === currentSpaceId}
                onOpenDetail={(s) => void openSpaceDetail(s)}
                onRequestJoin={(s, reason) => requestJoin(s.spaceId, reason)}
              />
            ))}
          </div>
        )}

        {/* 分页：统一 Pagination 组件，含每页条数选择与跳页输入 */}
        {total > 0 && (
          <Pagination
            className="mt-6"
            current={pageNo}
            pageSize={pageSize}
            total={total}
            onChange={changePage}
            onPageSizeChange={changePageSize}
            showQuickJumper
          />
        )}
      </div>

      {/* 创建团队空间 Modal */}
      <SpaceCreateForm open={createOpen} onOpenChange={setCreateOpen} onSubmit={createTeamSpace} />

      {/* 空间详情 Drawer：成员管理 */}
      <Drawer open={!!currentSpace} onOpenChange={(o) => !o && closeSpaceDetail()}>
        <DrawerContent
          side="right"
          size="lg"
          className="w-[680px] max-w-full"
          bodyClassName="p-0"
          aria-describedby={undefined}
        >
          <DrawerTitle className="sr-only">{currentSpace?.spaceName ?? '空间详情'}</DrawerTitle>
          <div className="flex h-full flex-col">
            {currentSpace && (
              <SpaceMemberList
                space={currentSpace}
                members={members}
                loading={membersLoading}
                onAddMembers={addMembers}
                addMembersLoading={addMembersLoading}
                addMembersDisabledReason={addMembersDisabledReason}
                onRemoveMember={removeMember}
                onUpdateRole={updateRole}
                onRequestJoin={(s, reason) => requestJoin(s.spaceId, reason)}
                onDeleteSpace={(spaceId) => void deleteSpace(spaceId)}
              />
            )}
          </div>
        </DrawerContent>
      </Drawer>
    </div>
  );
}

export default AdminSpacesView;
