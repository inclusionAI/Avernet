import { Button, Popover, PopoverContent, PopoverTrigger, Segmented } from '@/components/ui';
import type { CollaborationTemplate } from '@/services/workspace/collaborationTemplateService';
import { cn } from '@/utils/cn';
import { Check, ChevronDown, Loader2 } from 'lucide-react';
import { useState } from 'react';
import type { TemplateMode } from '../../hooks/useCollaborationTemplates';

export interface CollaborationTemplatePickerProps {
  mode: TemplateMode;
  templates: CollaborationTemplate[];
  selectedTemplateId: string | null;
  selectedTemplate: CollaborationTemplate | null;
  loadingTemplates: boolean;
  loadingYaml: boolean;
  tagLabel: (tag: string) => string;
  onModeChange: (mode: TemplateMode) => void;
  onSelect: (template: CollaborationTemplate) => void;
}

const MODE_OPTIONS: Array<{ value: TemplateMode; label: string }> = [
  { value: 'free', label: '自由编辑' },
  { value: 'template', label: '模板' },
];

/** 自定义协作 YAML 区的模式开关 + 模板下拉选择器。 */
export function CollaborationTemplatePicker(props: CollaborationTemplatePickerProps) {
  const {
    mode,
    templates,
    selectedTemplateId,
    selectedTemplate,
    loadingTemplates,
    loadingYaml,
    tagLabel,
    onModeChange,
    onSelect,
  } = props;
  const [open, setOpen] = useState(false);
  const isTemplate = mode === 'template';

  return (
    <div className="mb-2 flex items-center gap-2">
      <Segmented<TemplateMode> value={mode} options={MODE_OPTIONS} onChange={onModeChange} className="shrink-0" />
      {isTemplate && (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="secondary"
              aria-label="选择协作模板"
              className="h-8 min-w-[180px] flex-1 justify-between rounded-lg border-border bg-background px-2.5"
            >
              <span className="flex min-w-0 flex-1 items-center gap-1.5">
                {selectedTemplate ? (
                  <>
                    <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
                      {selectedTemplate.name}
                    </span>
                    {selectedTemplate.tags.slice(0, 2).map((tag) => (
                      <span
                        key={tag}
                        className="max-w-[60px] truncate rounded-full border border-border px-1.5 py-0.5 text-[10px] leading-none text-muted-foreground"
                      >
                        {tagLabel(tag)}
                      </span>
                    ))}
                  </>
                ) : loadingTemplates ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
                    <span className="flex-1 truncate text-xs text-muted-foreground">加载模板...</span>
                  </>
                ) : (
                  <span className="flex-1 truncate text-xs text-muted-foreground">选择模板</span>
                )}
              </span>
              <ChevronDown
                className={cn('h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
                aria-hidden
              />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-[280px] p-1">
            {templates.length === 0 ? (
              <div className="px-3 py-4 text-center text-xs text-muted-foreground">
                {loadingTemplates ? '加载中...' : '暂无可选模板'}
              </div>
            ) : (
              <div className="app-scrollbar max-h-72 space-y-0.5 overflow-y-auto" onWheel={(e) => e.stopPropagation()}>
                {templates.map((template) => {
                  const active = template.id === selectedTemplateId;
                  return (
                    <Button
                      key={template.id}
                      type="button"
                      role="option"
                      aria-selected={active}
                      variant="ghost"
                      className={cn(
                        'h-auto w-full flex-col items-start gap-1.5 rounded-md border-0 px-2.5 py-2 text-left',
                        active ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-muted',
                      )}
                      onClick={() => {
                        onSelect(template);
                        setOpen(false);
                      }}
                    >
                      <div className="flex w-full items-center gap-1.5">
                        <span className="min-w-0 flex-1 truncate text-xs font-medium">{template.name}</span>
                        <div className="flex shrink-0 items-center gap-1">
                          {template.tags.slice(0, 2).map((tag) => (
                            <span
                              key={tag}
                              className="max-w-[64px] truncate rounded-full border border-border px-1.5 py-0.5 text-[10px] leading-none text-muted-foreground"
                            >
                              {tagLabel(tag)}
                            </span>
                          ))}
                        </div>
                        {active && <Check className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />}
                      </div>
                      {template.description && (
                        <div className="line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                          {template.description}
                        </div>
                      )}
                    </Button>
                  );
                })}
              </div>
            )}
          </PopoverContent>
        </Popover>
      )}
      {isTemplate && loadingYaml && (
        <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
          加载内容...
        </span>
      )}
    </div>
  );
}

export default CollaborationTemplatePicker;
