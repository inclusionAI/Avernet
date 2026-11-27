import { Button } from '@/components/ui/Button';
import { ModalFooter } from '@/components/ui/Modal';
import type { TodoExecutor, TodoOwner } from '@/domain/todos/models';

interface TodoDimaImportActionsProps {
  resultCount: number;
  importedCount: number;
  duplicateCount: number;
  failedCount: number;
  submitting: boolean;
  spaceId?: number;
  selectedCount: number;
  executor?: TodoExecutor;
  owner?: TodoOwner;
  onClose: () => void;
  onSubmit: () => void;
}

export function TodoDimaImportActions({
  resultCount,
  importedCount,
  duplicateCount,
  failedCount,
  submitting,
  spaceId,
  selectedCount,
  executor,
  owner,
  onClose,
  onSubmit,
}: TodoDimaImportActionsProps) {
  return (
    <>
      {resultCount > 0 ? (
        <div className="flex flex-wrap gap-4 rounded-xl border border-border bg-muted/15 px-4 py-3 text-xs">
          <span>本次导入结果</span>
          <span className="text-success">成功 {importedCount}</span>
          <span className="text-warning">重复 {duplicateCount}</span>
          <span className={failedCount ? 'text-destructive' : 'text-muted-foreground'}>失败 {failedCount}</span>
        </div>
      ) : null}
      <ModalFooter>
        <Button variant="outline" onClick={onClose}>关闭</Button>
        <Button
          loading={submitting}
          disabled={!spaceId || selectedCount === 0 || !executor || !owner}
          onClick={onSubmit}
        >
          导入 {selectedCount ? `${selectedCount} 个` : ''}待办
        </Button>
      </ModalFooter>
    </>
  );
}
