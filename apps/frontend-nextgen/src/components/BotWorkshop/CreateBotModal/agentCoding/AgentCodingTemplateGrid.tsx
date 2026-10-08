import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { WandSparkles } from 'lucide-react';
import { AgentCodingTemplateCard } from './AgentCodingTemplateCard';

interface Props {
  templates: AgentCodingTemplate[];
  selectedKey?: string;
  selectedVersionId?: string;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  onSelect: (template: AgentCodingTemplate) => void;
  onCreateTemplate?: () => void;
}

export function AgentCodingTemplateGrid({
  templates,
  selectedKey,
  selectedVersionId,
  disabled,
  loading,
  error,
  onRetry,
  onSelect,
  onCreateTemplate,
}: Props) {
  if (loading)
    return (
      <div className="flex min-h-[190px] items-center justify-center rounded-lg border border-border bg-muted/20 text-xs text-muted-foreground">
        正在加载模板…
      </div>
    );
  if (error)
    return (
      <div className="flex min-h-[190px] flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-5 text-center">
        <p className="m-0 text-xs text-destructive">{error}</p>
        <Button type="button" size="sm" variant="secondary" onClick={onRetry}>
          重试
        </Button>
      </div>
    );
  return (
    <div className="mt-4 space-y-3">
      {templates.length ? (
        <div className="grid w-full max-h-[240px] grid-cols-1 gap-3 overflow-y-auto overscroll-contain overlay-scrollbar md:grid-cols-2">
          {templates.map((template) => (
            <AgentCodingTemplateCard
              key={`${template.key}:${template.versionId}`}
              template={template}
              selected={selectedKey === template.key && selectedVersionId === template.versionId}
              disabled={disabled}
              onSelect={() => onSelect(template)}
            />
          ))}
        </div>
      ) : (
        <Empty
          icon={<WandSparkles className="size-4" />}
          title="暂无可用模板 Bot"
          description="请先在模板工厂创建模板"
          action={
            onCreateTemplate ? (
              <Button type="button" variant="secondary" size="sm" onClick={onCreateTemplate}>
                前往模板工厂
              </Button>
            ) : undefined
          }
        />
      )}
    </div>
  );
}
