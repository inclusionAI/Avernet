import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import type { UseParticipantBindingResult } from '../../hooks/useParticipantBinding';
import { ParticipantBindingPanel } from './ParticipantBindingPanel';

export interface BindingSlotProps {
  visible: boolean;
  binding: UseParticipantBindingResult;
  botNameResolver?: (botId: string) => string | undefined;
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
export function BindingSlot({ visible, binding, botNameResolver }: BindingSlotProps) {
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
      {yamlValidation.validationError && !validated && (
        <p className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {yamlValidation.validationError}
        </p>
      )}
      {validated && (
        <>
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-background px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <span className="text-sm font-medium text-foreground">协同剧本</span>
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
          <span className="text-[11px] text-muted-foreground">
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
