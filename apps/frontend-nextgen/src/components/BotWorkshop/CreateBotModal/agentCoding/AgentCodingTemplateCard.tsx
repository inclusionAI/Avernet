import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import { BookOpen, Bot, Check, Code2, DraftingCompass, Sparkles, Terminal } from 'lucide-react';
import type { KeyboardEvent } from 'react';

interface Props {
  template: AgentCodingTemplate;
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
}

function iconFor(template: AgentCodingTemplate) {
  const type = template.templateType.toLowerCase().replace(/[\s_-]/g, '');
  if (type === 'applicationcoding') return Code2;
  if (type.includes('architect')) return DraftingCompass;
  if (type.includes('personal')) return Terminal;
  if (type.includes('general')) return Sparkles;
  // 旧版模板市场统一使用 Sparkles 图标。
  return template.source === 'market' ? Sparkles : Bot;
}

export function AgentCodingTemplateCard({ template, selected, disabled, onSelect }: Props) {
  const Icon = iconFor(template);
  const tags = template.capabilityTags;
  // figma 稿 node 2041:48753:官方模板也展示负责人小区片(如「义钧」),口径放宽为有 ownerName 即展示。
  const showOwner = Boolean(template.ownerName);
  const showWhitelistRibbon = template.templateReleaseStage === 'whitelist';

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onSelect();
    }
  };

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      aria-pressed={selected}
      onClick={disabled ? undefined : onSelect}
      onKeyDown={disabled ? undefined : handleKeyDown}
      className={`group relative flex min-h-[90px] w-full cursor-pointer overflow-hidden rounded-xl border p-2.5 text-left transition-colors ${
        selected ? 'border-primary/40 bg-primary/5' : 'border-border bg-card hover:border-primary/40 hover:bg-muted/30'
      } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
    >
      {/* figma 稿 node 2041:48753 / 2041:50013 模板卡:高 90,标题 14/600,描述 12,标签 11;
          选中态右上角标;使用手册 hover 浮出。色板按方案 A 走语义 token。 */}
      {selected ? (
        <span
          aria-hidden
          className="absolute right-1.5 top-1.5 flex size-3 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground"
        >
          <Check className="size-2" strokeWidth={3} />
        </span>
      ) : null}
      <div className="flex w-full min-w-0 flex-col">
        {/* 第一行：图标、名称、负责人、使用手册 */}
        <div className="flex min-h-5 w-full min-w-0 items-center gap-2 pr-4">
          <span
            className={`flex size-5 shrink-0 items-center justify-center rounded-md ${
              selected ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'
            }`}
          >
            <Icon aria-hidden className="size-3" />
          </span>
          <span
            className={`min-w-0 flex-1 truncate text-sm font-semibold leading-5 ${
              selected ? 'text-primary' : 'text-foreground'
            }`}
          >
            {template.name}
          </span>
          {showOwner ? (
            <span className="max-w-[96px] shrink-0 truncate text-[10px] font-medium leading-[18px] text-muted-foreground">
              {template.ownerName}
            </span>
          ) : null}
          {template.manualUrl ? (
            <a
              href={template.manualUrl}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${template.name} 使用手册`}
              onMouseDown={(event) => event.stopPropagation()}
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
              className="inline-flex size-[18px] shrink-0 items-center justify-center rounded text-primary opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100 hover:bg-primary/10"
            >
              <BookOpen aria-hidden className="size-3" />
            </a>
          ) : null}
        </div>

        {/* 第二行：描述(稿 12px 文本二级) */}
        <div className="mt-2 block min-h-[14px] w-full truncate text-xs leading-[18px] text-foreground/70">
          {template.description || ' '}
        </div>

        {/* 第三行：能力标签(稿小标签 11px h18);官方与市场模板统一展示 */}
        <div className="mt-2 flex h-[18px] w-full min-w-0 flex-nowrap items-center gap-1 overflow-hidden">
          {tags.slice(0, 6).map((tag) => (
            <span
              key={tag}
              className={`inline-flex h-[18px] shrink-0 items-center whitespace-nowrap rounded-md border px-1.5 py-[2px] text-[11px] font-medium leading-[12px] ${
                selected
                  ? 'border-primary/20 bg-primary/5 text-primary'
                  : 'border-primary/15 bg-primary/5 text-primary/80'
              }`}
            >
              {tag}
            </span>
          ))}
        </div>
      </div>

      {showWhitelistRibbon ? (
        <span
          aria-label="白名单阶段"
          className="pointer-events-none absolute bottom-2 -right-6 flex h-[17px] w-[88px] rotate-[-42deg] items-center justify-center border border-amber-300/70 bg-amber-100/95 text-[9px] font-medium leading-none tracking-[0.02em] text-amber-800/85 shadow-[0_-1px_4px_rgba(180,83,9,0.08)]"
        >
          白名单
        </span>
      ) : null}
    </div>
  );
}
