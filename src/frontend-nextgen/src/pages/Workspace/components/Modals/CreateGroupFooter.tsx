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
    <div data-testid="create-group-modal-footer" className="flex items-center gap-4 border-t border-border px-6 py-4">
      <div className="min-w-0 flex-1">
        {yamlValidationError ? (
          <p role="alert" className="m-0 text-left text-xs text-destructive">
            {yamlValidationError}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center justify-end gap-2">
        <Button variant="secondary" size="md" disabled={creating} onClick={onClose}>
          取消
        </Button>
        <Button size="md" loading={creating} disabled={creating || !canSubmit} onClick={() => void onSubmit()}>
          确认创建
        </Button>
      </div>
    </div>
  );
}
