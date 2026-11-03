import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
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
    boundCount,
    setActiveParticipantKey,
    handleUnbind,
  } = binding;

  if (!visible) return null;
  const validated = yamlValidation.isValidated;

  return (
    <div className="mt-3 space-y-3">
      {validated && (
        <>
          <CollaborationValidationNotices warnings={yamlValidation.warnings} />
          <div className={cn('grid grid-cols-1 gap-3', summaryAction && 'sm:grid-cols-2')}>
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-background px-3 py-2">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-foreground">协同剧本</span>
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                  已解析 {yamlValidation.participantDefinitions.length} 个角色
                </span>
              </div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  binding.reset();
                }}
              >
                重新编辑
              </Button>
            </div>
            {summaryAction && <div className="min-w-0">{summaryAction}</div>}
          </div>
          <span className="text-xs text-muted-foreground">
            已绑定 {boundCount} / {yamlValidation.participantDefinitions.length} 个角色
          </span>
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
