// 空间管理视图：搜索 + 创建空间 + 卡片网格 + 空间详情 Drawer（含 SpaceMemberList）。
// 视觉对齐 PRD：工具条右对齐，搜索框紧挨创建空间按钮；无类型筛选（PRD 无全部/团队/个人筛选）。
import { SpaceCard } from '@/components/Admin/SpaceCard';
import { SpaceCreateForm } from '@/components/Admin/SpaceCreateForm';
import { SpaceMemberList } from '@/components/Admin/SpaceMemberList';
import { Button, Drawer, DrawerContent, Empty, Input, Skeleton } from '@/components/ui';
import { useAdmin } from '@/hooks/useAdmin';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { extractFriendlyErrorMessage } from '@/utils/requestErrorHandler';
import { Plus, Search, X } from 'lucide-react';
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
    createTeamSpace,
    openSpaceDetail,
    closeSpaceDetail,
    deleteSpace,
    addMember,
    removeMember,
    updateRole,
    requestJoin,
    fetchList,
  } = useAdmin();
  const currentSpaceId = useSpaceContext((s) => s.currentSpaceId);
  const [createOpen, setCreateOpen] = useState(false);

  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="mx-auto max-w-[1200px]">
      {/* §4.1 精简版 PageHeader：左 h1＋副文案，右操作区（搜索 + 唯一主按钮「创建空间」） */}
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">空间管理</h1>
          <p className="mt-1 text-xs text-muted-foreground">管理你已加入及可申请的团队空间</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search
              size={14}
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              className="h-9 w-64 pl-8 pr-7"
              placeholder="搜索空间名称..."
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
          <Button variant="primary" leftIcon={<Plus size={14} />} onClick={() => setCreateOpen(true)}>
            创建空间
          </Button>
        </div>
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton.Card key={i} />
          ))}
        </div>
      ) : error ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <div className="min-w-0 text-sm text-destructive">{extractFriendlyErrorMessage(error)}</div>
          <Button size="sm" variant="primary" onClick={() => void fetchList()}>
            重试
          </Button>
        </div>
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
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {items.map((space) => (
            <SpaceCard
              key={space.spaceId || space.spaceCode}
              space={space}
              isCurrent={space.spaceId === currentSpaceId}
              onOpenDetail={(s) => void openSpaceDetail(s)}
              onRequestJoin={(s, reason) => void requestJoin(s.spaceId, reason)}
            />
          ))}
        </div>
      )}

      {/* 分页：按钮居左，「共 N 条」右对齐 */}
      {total > pageSize && (
        <div className="mt-6 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" disabled={pageNo <= 1} onClick={() => changePage(pageNo - 1)}>
              上一页
            </Button>
            <Button size="sm" variant="ghost" disabled={pageNo >= pageCount} onClick={() => changePage(pageNo + 1)}>
              下一页
            </Button>
          </div>
          <span className="text-xs tabular-nums text-muted-foreground">
            {pageNo} / {pageCount} · 共 {total} 条
          </span>
        </div>
      )}

      {/* 创建团队空间 Modal */}
      <SpaceCreateForm open={createOpen} onOpenChange={setCreateOpen} onSubmit={createTeamSpace} />

      {/* 空间详情 Drawer：成员管理 */}
      <Drawer open={!!currentSpace} onOpenChange={(o) => !o && closeSpaceDetail()}>
        <DrawerContent side="right" size="md" className="w-[640px]">
          <div className="flex h-full flex-col">
            {currentSpace && (
              <SpaceMemberList
                space={currentSpace}
                members={members}
                loading={membersLoading}
                onAddMember={addMember}
                onRemoveMember={removeMember}
                onUpdateRole={updateRole}
                onRequestJoin={(s, reason) => void requestJoin(s.spaceId, reason)}
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
