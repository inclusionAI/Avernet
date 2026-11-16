// Coding Bot 已归档会话列表。数据来源由 capability host 提供，这里只做批量选择、
// 摘要预览和恢复操作；归档来源按 item.sessionSource 过滤，不解析 sessionKey。
// 版式与 TeamClaw 常规会话行同构：32px 单行、标题左缘对齐、右侧只展示相对时间或
// 恢复状态；消息数保留在摘要弹窗中展示，不在行内展示第二条元信息。
import { Button, Empty, IconButton } from '@/components/ui';
import type {
  CodingBotSessionArchiveItem,
  CodingBotSessionArchivePreviewResult,
  ConversationOrigin,
  ConversationSessionArchiveListState,
} from '@/domain/conversation/types';
import { ListChecks, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { ListErrorState } from '../../components/ListErrorState';
import { ConversationLoadMore } from './ConversationLoadMore';
import { ConversationSessionArchivePreviewDialog } from './ConversationSessionArchivePreviewDialog';
import { ConversationSessionArchiveRow } from './ConversationSessionArchiveRow';
import { ARCHIVE_RESTORE_LIMIT } from './ConversationSessionArchivePresentation';
import { ConversationSessionRowsSkeleton } from './ConversationSkeletons';

export interface ConversationSessionArchivesProps {
  origin: ConversationOrigin;
  /** Bot 行「会话ID检索」提交的条件；空串表示未过滤。 */
  searchKeyword: string;
  state: ConversationSessionArchiveListState;
  readOnly?: boolean;
  onLoadMore(): void;
  onRetry(): void;
  onRestore(input: { sessionKeys: string[]; focusRestored: boolean }): Promise<boolean>;
  onPreview(item: CodingBotSessionArchiveItem): Promise<CodingBotSessionArchivePreviewResult>;
}

export function ConversationSessionArchives({
  origin,
  searchKeyword,
  state,
  readOnly = false,
  onLoadMore,
  onRetry,
  onRestore,
  onPreview,
}: ConversationSessionArchivesProps) {
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [previewItem, setPreviewItem] = useState<CodingBotSessionArchiveItem | null>(null);
  const [preview, setPreview] = useState<CodingBotSessionArchivePreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    setSelectedKeys([]);
    setSelectionMode(false);
    setPreviewItem(null);
    setPreview(null);
    setPreviewError(null);
  }, [origin, state.availability, state.restoreAvailability, readOnly]);

  const scopedItems = useMemo(
    () =>
      state.items.filter((item) => {
        if (item.sessionSource !== origin) return false;
        if (
          item.restoreStatus === 'succeeded' &&
          !state.restoredItems.some((entry) => entry.archiveId === item.archiveId)
        ) {
          return false;
        }
        return true;
      }),
    [origin, state.items, state.restoredItems],
  );
  const items = useMemo(() => {
    const keyword = searchKeyword.trim().toLowerCase();
    if (!keyword) return scopedItems;
    return scopedItems.filter((item) => item.sessionKey.toLowerCase().includes(keyword));
  }, [searchKeyword, scopedItems]);
  const selectable = useMemo(
    () =>
      new Set(
        scopedItems
          .filter((item) => item.restoreStatus === 'archived' || item.restoreStatus === 'failed')
          .map((item) => item.sessionKey),
      ),
    [scopedItems],
  );
  const currentSelectedKeys = selectedKeys.filter((key) => selectable.has(key));
  const restoreSupported = state.restoreAvailability === 'supported' && !readOnly;
  const batchAvailable = restoreSupported && (selectable.size > 0 || selectionMode);

  const openPreview = async (item: CodingBotSessionArchiveItem) => {
    setPreviewItem(item);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreview(null);
    const result = await onPreview(item);
    setPreviewLoading(false);
    if (!result.ok) setPreviewError(result.message);
    else setPreview(result);
  };

  const submitRestore = async (keys: string[], focusRestored: boolean) => {
    const next = keys.filter((key) => selectable.has(key));
    if (!next.length || next.length > ARCHIVE_RESTORE_LIMIT) return;
    const accepted = await onRestore({ sessionKeys: next, focusRestored });
    if (accepted && !focusRestored) setSelectedKeys([]);
  };

  const emptyTitle = searchKeyword.trim() ? '没有匹配的归档会话' : '暂无归档会话';

  return (
    <section aria-label={`已归档会话：${origin}`} className="conversation-area-in">
      {scopedItems.length > 0 && batchAvailable && (
        <div className="sticky top-0 z-10 flex h-8 items-center justify-end gap-1.5 bg-background/95 px-3 backdrop-blur-sm">
          {selectionMode ? (
            <>
              <span className="truncate pr-1 text-[11px] text-content-soft">
                已选 {currentSelectedKeys.length} / {Math.min(ARCHIVE_RESTORE_LIMIT, selectable.size)}
              </span>
              <Button
                size="sm"
                disabled={!currentSelectedKeys.length || state.isSubmitting}
                loading={state.isSubmitting}
                onClick={() => void submitRestore(currentSelectedKeys, false)}
                className="h-6 rounded-md px-2 text-[11px]"
              >
                恢复选中
              </Button>
              <IconButton
                label="退出批量恢复"
                size="sm"
                icon={<X className="h-3.5 w-3.5" />}
                disabled={state.isSubmitting}
                onClick={() => {
                  setSelectionMode(false);
                  setSelectedKeys([]);
                }}
                className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              />
            </>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              disabled={state.isSubmitting}
              leftIcon={<ListChecks className="h-3.5 w-3.5" />}
              onClick={() => setSelectionMode(true)}
              className="h-6 gap-1 rounded-md px-2 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              批量恢复
            </Button>
          )}
        </div>
      )}
      {state.restoreError && (
        <div className="px-4 pb-2">
          <ListErrorState message={state.restoreError} />
        </div>
      )}
      {state.error && !state.loading && (
        <div className="px-4">
          <ListErrorState message={state.error} onRetry={onRetry} />
        </div>
      )}
      {state.loading && state.items.length === 0 && <ConversationSessionRowsSkeleton rows={3} />}
      {!state.loading && !state.error && state.availability === 'unsupported' && (
        <div className="px-4">
          <Empty
            compact
            size="sm"
            title="当前容器暂不支持归档"
            description="请升级或重启容器后再查看已归档会话。"
          />
        </div>
      )}
      {!state.loading && !state.error && state.availability !== 'unsupported' && items.length === 0 && (
        <div className="px-4">
          <Empty compact size="sm" title={emptyTitle} />
        </div>
      )}
      {items.length > 0 && (
        <div>
          {items.map((item) => (
            <ConversationSessionArchiveRow
              key={item.archiveId}
              item={item}
              selectionMode={selectionMode}
              restoreSupported={restoreSupported}
              checked={currentSelectedKeys.includes(item.sessionKey)}
              selectedCount={currentSelectedKeys.length}
              selectionLimit={ARCHIVE_RESTORE_LIMIT}
              submitting={state.isSubmitting}
              onCheckedChange={(checked) =>
                setSelectedKeys((keys) => {
                  const current = keys.filter((key) => selectable.has(key));
                  return checked
                    ? [...new Set([...current, item.sessionKey])]
                    : current.filter((key) => key !== item.sessionKey);
                })
              }
              onPreview={(target) => void openPreview(target)}
              onRestore={(target) => void submitRestore([target.sessionKey], true)}
            />
          ))}
        </div>
      )}
      {!state.loading && !state.error && state.nextCursor && (
        <ConversationLoadMore loading={state.isLoadingMore} onClick={onLoadMore} />
      )}
      {state.loadMoreError && <ListErrorState message={state.loadMoreError} onRetry={onLoadMore} />}
      <ConversationSessionArchivePreviewDialog
        item={previewItem}
        result={preview}
        loading={previewLoading}
        error={previewError}
        restoreSupported={restoreSupported}
        submitting={state.isSubmitting}
        onOpenChange={(open) => !open && setPreviewItem(null)}
        onRestore={async (target) => {
          await submitRestore([target.sessionKey], true);
        }}
      />
    </section>
  );
}
