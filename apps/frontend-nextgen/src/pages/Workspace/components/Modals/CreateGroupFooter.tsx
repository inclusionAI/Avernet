import { Button } from '@/components/ui';

export interface CreateGroupFooterProps {
  yamlValidationError?: string;
  creating: boolean;
  canSubmit: boolean;
  onClose: () => void;
  onSubmit: () => Promise<void>;
}

/** 发起协作底部操作栏：左侧承载 YAML 校验错误，右侧固定操作按钮。 */
export function CreateGroupFooter({
  yamlValidationError,
  creating,
  canSubmit,
  onClose,
  onSubmit,
}: CreateGroupFooterProps) {
  return (
    <div data-testid="create-group-modal-footer" className="col-span-full flex flex-wrap items-center gap-3 border-t border-border bg-muted/20 px-4 py-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:px-7">
      <div className="min-w-0 flex-1 empty:hidden">
        {yamlValidationError ? (
          <p role="alert" className="m-0 text-left text-xs text-destructive">
            {yamlValidationError}
          </p>
        ) : null}
      </div>
      <div className="ml-auto flex w-full shrink-0 items-center justify-end gap-3 sm:w-auto">
        <Button variant="outline" size="sm" className="flex-1 sm:flex-none" disabled={creating} onClick={onClose}>
          取消
        </Button>
        <Button size="sm" className="flex-1 sm:flex-none" loading={creating} disabled={creating || !canSubmit} onClick={() => void onSubmit()}>
          确认创建
        </Button>
      </div>
    </div>
  );
}
