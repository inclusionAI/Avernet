import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import { useMemo, type ReactNode } from 'react';
import type { UseParticipantBindingResult } from '../../hooks/useParticipantBinding';
import { CollaborationFlowPreview } from './CollaborationFlowPreview';
import { buildCollaborationBindingViews } from './collaborationGraphLayout';
import { ParticipantBindingPanel } from './ParticipantBindingPanel';

export interface BindingSlotProps {
  visible: boolean;
  yaml: string;
  leaderOptions: Array<{ id: string; name: string; current?: boolean }>;
  binding: UseParticipantBindingResult;
}

/** 「自定义协作」YAML 校验按钮 / 流程预览 / 角色绑定面板插槽。 */
export function BindingSlot({ visible, yaml, leaderOptions, binding }: BindingSlotProps) {
  const {
    yamlValidation,
    participantBindings,
    activeParticipantKey,
    boundCount,
    handleValidate,
    setActiveParticipantKey,
  } = binding;

  const bindingViews = useMemo(
    () =>
      buildCollaborationBindingViews(
        yamlValidation.participantDefinitions,
        Object.fromEntries(Object.entries(participantBindings).map(([k, v]) => [k, v ? [v] : []])),
        (botId) => leaderOptions.find((b) => b.id === botId)?.name,
      ),
    [yamlValidation.participantDefinitions, participantBindings, leaderOptions],
  );

  const showFlowPlaceholder = yamlValidation.isValidated && !binding.graph;
  const flowPreview: ReactNode = binding.graph ? (
    <CollaborationFlowPreview
      graph={binding.graph}
      initialNodes={binding.summary?.initial_nodes ?? []}
      bindingViews={bindingViews}
      highlightedBinding={activeParticipantKey || undefined}
    />
  ) : null;

  if (!visible) return null;
  const validated = yamlValidation.isValidated;

  return (
    <div className="mt-3 space-y-3">
      {/* 校验前：展示校验按钮 */}
      {!validated && (
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={yamlValidation.isValidating || !yaml.trim()}
            onClick={() => void handleValidate(yaml)}
            className={cn('rounded-lg border text-xs', 'border-border text-foreground hover:border-primary/30')}
          >
            {yamlValidation.isValidating ? '校验中...' : '校验 YAML'}
          </Button>
        </div>
      )}
      {yamlValidation.validationError && !validated && (
        <p className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {yamlValidation.validationError}
        </p>
      )}
      {/* 校验后：展示流程预览 + 角色绑定 + 重新编辑按钮 */}
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
          {flowPreview}
          {showFlowPlaceholder && (
            <div className="flex h-[120px] items-center justify-center rounded-xl border border-dashed border-border bg-muted/30 text-center text-xs text-muted-foreground">
              此次校验未返回流程预览数据
            </div>
          )}
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
        />
      )}
    </div>
  );
}

export default BindingSlot;
