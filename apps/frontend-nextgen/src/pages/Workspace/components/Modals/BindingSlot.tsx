import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import { FileCheck2 } from 'lucide-react';
import type { ReactNode } from 'react';
import type { UseParticipantBindingResult } from '../../hooks/useParticipantBinding';
import { ParticipantBindingPanel } from './ParticipantBindingPanel';
import CollaborationValidationNotices from './CollaborationValidationNotices';

export interface BindingSlotProps {
  visible: boolean;
  binding: UseParticipantBindingResult;
  botNameResolver?: (botId: string) => string | undefined;
  summaryAction?: ReactNode;
}

export function YamlValidateButton({ yaml, binding }: { yaml: string; binding: UseParticipantBindingResult }) {
  const {
    yamlValidation: { isValidating },
    handleValidate,
  } = binding;

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      disabled={isValidating || !yaml.trim()}
      onClick={() => void handleValidate(yaml)}
      className={cn('rounded-lg border text-xs', 'border-border text-foreground hover:border-primary/30')}
    >
      {isValidating ? '校验中...' : '校验 YAML'}
    </Button>
  );
}

/** 「自定义协作」流程预览 / 角色绑定面板插槽。 */
export function BindingSlot({ visible, binding, botNameResolver, summaryAction }: BindingSlotProps) {
  const {
    yamlValidation,
    participantBindings,
    activeParticipantKey,
    setActiveParticipantKey,
    handleUnbind,
  } = binding;

  if (!visible) return null;
  const validated = yamlValidation.isValidated;

  return (
    <div className="space-y-6">
      {validated && (
        <>
          <CollaborationValidationNotices warnings={yamlValidation.warnings} />
          <div className={cn('grid grid-cols-1 gap-5', summaryAction && 'sm:grid-cols-2 sm:gap-4')}>
            <div className="min-w-0">
              <span className="mb-2 block text-xs font-medium text-foreground">协同剧本</span>
              <div className="flex h-8 min-w-0 items-center justify-between gap-2 rounded-lg border border-input bg-muted/30 px-3">
                <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                  <FileCheck2 className="size-4 shrink-0 text-success" aria-hidden />
                  已解析 {yamlValidation.participantDefinitions.length} 个角色
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 shrink-0 border-0 px-1 text-xs text-primary"
                  onClick={() => {
                    binding.reset();
                  }}
                >
                  重新编辑
                </Button>
              </div>
            </div>
            {summaryAction && <div className="min-w-0">{summaryAction}</div>}
          </div>
        </>
      )}
      {validated && yamlValidation.participantDefinitions.length > 0 && (
        <ParticipantBindingPanel
          definitions={yamlValidation.participantDefinitions}
          bindings={participantBindings}
          activeKey={activeParticipantKey || yamlValidation.participantDefinitions[0].key}
          onActiveKeyChange={setActiveParticipantKey}
          botNameResolver={botNameResolver}
          onUnbind={handleUnbind}
        />
      )}
    </div>
  );
}

export default BindingSlot;
