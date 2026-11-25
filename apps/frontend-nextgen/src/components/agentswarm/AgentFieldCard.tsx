import { CircleHelp, Copy, Trash2 } from 'lucide-react';
import { ModelSelector } from './ModelSelector';
import { YamlCodeEditor } from '@/pages/Workspace/components/Modals/YamlEditor';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';
import type { AgentDraft } from '@/domain/agentswarm/draft';
import { MANIFEST_DOCS_URL, MANIFEST_TEMPLATE } from '@/domain/agentswarm/manifest';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { VerticalResizeHandle } from './VerticalResizeHandle';
import { useState } from 'react';
import { isSynthesized, synthesizeManifest } from '@/domain/agentswarm/synthManifest';

/** The manifest editor opens at a readable height and can be dragged taller. */
const EDITOR_DEFAULT_HEIGHT = 260;
const EDITOR_MIN_HEIGHT = 160;
const EDITOR_MAX_HEIGHT = 640;

export interface AgentFieldCardProps {
  agent: AgentDraft;
  index: number;
  /** Whether validation messages should be shown yet. */
  showErrors: boolean;
  /** This agent's name is already used by another agent in the draft. */
  duplicate: boolean;
  /** The manifest error message, when this agent's manifest is the offender. */
  manifestError?: string;
  canRemove: boolean;
  onChange: (patch: Partial<AgentDraft>) => void;
  onDuplicate: () => void;
  onRemove: () => void;
}

/** One agent's definition, collapsed to "name · role" until opened. */

/**
 * The agent's manifest.
 *
 * A blank manifest is not an error: the platform synthesizes an identity-only
 * document from `instructions`, so the blank state renders that document as a
 * read-only preview instead of an empty box. Writing one is an explicit opt-in.
 */
function ManifestField({
  agent,
  synthesized,
  error,
  onChange,
}: {
  agent: AgentDraft;
  synthesized: boolean;
  error?: string;
  onChange: (patch: Partial<AgentDraft>) => void;
}) {
  const [editorHeight, setEditorHeight] = useState(EDITOR_DEFAULT_HEIGHT);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1">
          <Label htmlFor={`compute-manifest-${agent.key}`}>能力配置 YAML</Label>
          {/* 语法与各类目的限制都在那份文档里；一个图标比在 440px 面板里
              重复一遍更好维护，也不会随平台演进过期。 */}
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <a
                  href={MANIFEST_DOCS_URL}
                  target="_blank"
                  rel="noreferrer"
                  aria-label="查看配置清单的完整语法"
                  className="inline-flex items-center text-muted-foreground hover:text-primary"
                >
                  <CircleHelp className="h-3.5 w-3.5" />
                </a>
              </TooltipTrigger>
              <TooltipContent>查看配置清单的完整语法</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </span>
        {synthesized ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() =>
              // 自写的起点是模板，不是合成的 identity-only 预览：那个形状里
              // 没有 mcp/skills 的字段可循，用户只能对着空白猜。
              onChange({ config_manifest: MANIFEST_TEMPLATE })
            }
          >
            改为自定义 YAML
          </Button>
        ) : (
          <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ config_manifest: '' })}>
            改为平台生成
          </Button>
        )}
      </div>

      {synthesized ? (
        <>
          <pre className="max-h-[200px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 font-mono text-xs leading-5">
            {synthesizeManifest(agent.instructions ?? '')}
          </pre>
          <p className="text-xs leading-5 text-muted-foreground">
            未填写 YAML 时，平台按上方「指令」生成以上 SOUL.md；名称与角色不进入人设。
          </p>
        </>
      ) : (
        <>
          <YamlCodeEditor
            value={agent.config_manifest ?? ''}
            onChange={(manifest) => onChange({ config_manifest: manifest })}
            height={editorHeight}
          />
          {/* 清单可能很长，固定 200px 读不下；高度给用户自己调。 */}
          <VerticalResizeHandle
            height={editorHeight}
            onHeightChange={setEditorHeight}
            min={EDITOR_MIN_HEIGHT}
            max={EDITOR_MAX_HEIGHT}
            label="调整配置清单编辑器高度"
          />
          <p className="text-xs leading-5 text-muted-foreground">已填写 YAML 时原样提交，「指令」不再参与人设。</p>
        </>
      )}

      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function AgentFieldCard({
  agent,
  index,
  showErrors,
  duplicate,
  manifestError,
  canRemove,
  onChange,
  onDuplicate,
  onRemove,
}: AgentFieldCardProps) {
  const nameInvalid = showErrors && !agent.name.trim();
  const roleInvalid = showErrors && !agent.role.trim();
  const synthesized = isSynthesized(agent.config_manifest);

  return (
    <details className="rounded-lg border border-border bg-card p-3">
      <summary className="cursor-pointer break-words text-sm font-medium">
        {agent.name || `Agent ${index + 1}`} · {agent.role || '待填写角色'}
      </summary>
      <div className="mt-4 space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor={`compute-name-${agent.key}`}>
            名称 <span className="text-destructive">*</span>
          </Label>
          <Input
            id={`compute-name-${agent.key}`}
            value={agent.name}
            placeholder="例如：greeter_bot"
            aria-invalid={nameInvalid || duplicate || undefined}
            onChange={(event) => onChange({ name: event.target.value })}
          />
          <p className="text-xs leading-5 text-muted-foreground">标识符风格，运行内唯一；不进入人设。</p>
          {duplicate ? (
            <p role="alert" className="text-xs text-destructive">
              同次运行的 Agent 名称不能重复
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`compute-role-${agent.key}`}>
            角色 <span className="text-destructive">*</span>
          </Label>
          <Input
            id={`compute-role-${agent.key}`}
            value={agent.role}
            placeholder="例如：负责竞品动态调研"
            aria-invalid={roleInvalid || undefined}
            onChange={(event) => onChange({ role: event.target.value })}
          />
          <p className="text-xs leading-5 text-muted-foreground">一句话职能描述；不进入人设。</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`compute-instructions-${agent.key}`}>指令</Label>
          <Textarea
            id={`compute-instructions-${agent.key}`}
            value={agent.instructions ?? ''}
            placeholder="例如：梳理本季度主要竞品的功能更新，只输出结构化要点。"
            onChange={(event) => onChange({ instructions: event.target.value })}
          />
          <p className="text-xs leading-5 text-muted-foreground">
            具体行为要求。未填写 YAML 时，这段文字会成为该 Agent 的 SOUL.md 内容。
          </p>
        </div>

        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">身份与能力配置</summary>
          <div className="mt-3 space-y-3">
            <ModelSelector
              label="独立模型"
              inheritLabel="跟随运行默认模型"
              value={agent.model ?? null}
              onChange={(model) => onChange({ model })}
            />
            <ManifestField agent={agent} synthesized={synthesized} error={manifestError} onChange={onChange} />
          </div>
        </details>

        <div className="flex gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={onDuplicate}>
            <Copy className="mr-1 h-3.5 w-3.5" />
            复制
          </Button>
          <Button type="button" size="sm" variant="ghost" disabled={!canRemove} onClick={onRemove}>
            <Trash2 className="mr-1 h-3.5 w-3.5" />
            移除
          </Button>
        </div>
      </div>
    </details>
  );
}