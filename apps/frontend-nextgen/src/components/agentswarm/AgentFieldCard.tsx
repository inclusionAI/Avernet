import { CircleHelp, Copy, Plus, Trash2 } from 'lucide-react';
import { ModelSelector } from './ModelSelector';
import { YamlCodeEditor } from '@/pages/Workspace/components/Modals/YamlEditor';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs';
import { Textarea } from '@/components/ui/Textarea';
import { spawnName, type AgentDraft, type AgentRefDraft } from '@/domain/agentswarm/draft';
import { MANIFEST_DOCS_URL } from '@/domain/agentswarm/manifest';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { VerticalResizeHandle } from './VerticalResizeHandle';
import { useState } from 'react';
import {
  isSynthesized,
  readManifestInstructions,
  synthesizeManifest,
  syncManifestInstructions,
} from '@/domain/agentswarm/synthManifest';

/** The manifest editor opens at a readable height and can be dragged taller. */
const EDITOR_DEFAULT_HEIGHT = 260;
const EDITOR_MIN_HEIGHT = 160;
const EDITOR_MAX_HEIGHT = 640;

/** PRD §7: the middle column edits one agent under two tabs. */
export type AgentTab = 'basic' | 'capabilities';

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
  readOnly = false,
  onChange,
}: {
  agent: AgentDraft;
  synthesized: boolean;
  error?: string;
  /** A reference shows its definition's YAML; editing happens in 我的 Agent 库. */
  readOnly?: boolean;
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
        {readOnly ? null : synthesized ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() =>
              // Exactly the document the preview above shows — no comment header.
              // Anything extra would make the editable YAML differ from what the
              // platform actually generates, which is the thing this button is
              // meant to reveal.
              onChange({ config_manifest: synthesizeManifest(agent.instructions ?? '') })
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

      {readOnly ? (
        <>
          <pre className="max-h-[280px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 font-mono text-xs leading-5">
            {(agent.config_manifest ?? '').trim() || synthesizeManifest(agent.instructions ?? '')}
          </pre>
          <p className="text-xs leading-5 text-muted-foreground">
            来自定义库，只读。要修改请到「我的 Agent 库」编辑该定义，或改名为新名称另存一份。
          </p>
        </>
      ) : synthesized ? (
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

export interface AgentEditorProps {
  /** The roster: library refs and not-yet-saved new entries alike. */
  refs: AgentRefDraft[];
  index: number;
  tab: AgentTab;
  onTabChange: (tab: AgentTab) => void;
  /** Selecting an agent in the left nav. */
  onSelect: (index: number) => void;
  /** Whether validation messages should be shown yet. */
  showErrors: boolean;
  /** Names already used by another agent in the draft. */
  duplicateNames: Set<string>;
  /** The manifest error message, when this agent's manifest is the offender. */
  manifestError?: string;
  canRemove: boolean;
  onChange: (patch: Partial<AgentDraft>) => void;
  onDuplicate: () => void;
  onRemove: () => void;
  onAdd: () => void;
}

/**
 * Agent definitions, as PRD §7 lays them out: the left nav switches agents, the
 * middle column edits the selected one under 基本信息 and 身份与能力.
 *
 * The nav and the editor share one row so the list stays reachable while a long
 * editor scrolls — on narrow screens they stack.
 */
export function AgentEditor({
  refs,
  index,
  tab,
  onTabChange,
  onSelect,
  showErrors,
  duplicateNames,
  manifestError,
  canRemove,
  onChange,
  onDuplicate,
  onRemove,
  onAdd,
}: AgentEditorProps) {
  const activeRef = refs[Math.min(index, refs.length - 1)];
  // Both kinds edit the same body shape; a new entry is editable, a library one
  // is a resolved reference so only its alias is writable here.
  const agent = activeRef.body;
  const readOnly = activeRef.origin === 'library';
  const nameInvalid = showErrors && !agent.name.trim();
  const roleInvalid = showErrors && !agent.role.trim();
  const synthesized = isSynthesized(agent.config_manifest);

  return (
    <div className="grid min-w-0 gap-5 lg:grid-cols-[180px_minmax(0,1fr)]">
      <nav
        aria-label="执行 Agent 定义"
        className="flex min-w-0 flex-col gap-1 border-b border-border pb-4 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-4 lg:sticky lg:top-0 lg:self-start lg:max-h-[60vh] lg:overflow-y-auto"
      >
        {refs.map((item, itemIndex) => {
          const selected = itemIndex === index;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onSelect(itemIndex)}
              aria-current={selected ? 'true' : undefined}
              className={[
                'flex w-full flex-col gap-0.5 rounded-lg px-2.5 py-2 text-left',
                selected ? 'bg-primary/5 text-foreground' : 'hover:bg-accent',
              ].join(' ')}
            >
              <span className="truncate text-sm font-medium">
                {spawnName(item) || `Agent ${itemIndex + 1}`}
              </span>
              <span className="truncate text-xs text-muted-foreground">
                {item.origin === 'new' ? '新建 · 未保存' : `第 ${itemIndex + 1} 个 · 来自定义库`}
              </span>
            </button>
          );
        })}
        <Button type="button" size="sm" variant="ghost" onClick={onAdd}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          空白新建
        </Button>
      </nav>

      <div className="min-w-0 space-y-4">
        {/* Say where this entry comes from: a library ref is resolved at run
            time, so editing its body here would silently do nothing. */}
        <p className="m-0 rounded-lg bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
          {readOnly
            ? `来自定义库：本次运行引用该定义，修改定义请到下方「我的 Agent 库」。这里只可改本次运行的名称。`
            : `新建 Agent：提交时会先保存到「我的 Agent 库」，再以定义引用提交。`}
        </p>
        <Tabs value={tab} onValueChange={(next) => onTabChange(next as AgentTab)}>
          <TabsList aria-label="编辑 Agent 内容">
            <TabsTrigger value="basic">基本信息</TabsTrigger>
            <TabsTrigger value="capabilities">身份与能力</TabsTrigger>
          </TabsList>

          <TabsContent value="basic" className="space-y-3 pt-4">
            <div className="space-y-1.5">
              <Label htmlFor={`compute-name-${agent.key}`}>
                名称 <span className="text-destructive">*</span>
              </Label>
              <Input
                id={`compute-name-${agent.key}`}
                value={agent.name}
                placeholder="例如：greeter_bot"
                // Always editable: renaming a referenced entry detaches it, so
                // the user can fork a stored definition under a new name.

                aria-invalid={nameInvalid || duplicateNames.has(agent.name.trim()) || undefined}
                onChange={(event) => onChange({ name: event.target.value })}
              />
              <p className="text-xs leading-5 text-muted-foreground">
                {readOnly
                  ? '改名为新名称即脱离该定义，成为可编辑的新建 Agent；原名会继续引用定义库中的定义。'
                  : '标识符风格，运行内唯一；不进入人设。'}
              </p>
              {showErrors && duplicateNames.has(agent.name.trim()) ? (
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
                disabled={readOnly}
                aria-invalid={roleInvalid || undefined}
                onChange={(event) => onChange({ role: event.target.value })}
              />
              <p className="text-xs leading-5 text-muted-foreground">一句话职能描述；不进入人设。</p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor={`compute-instructions-${agent.key}`}>指令</Label>
              <Textarea
                id={`compute-instructions-${agent.key}`}
                disabled={readOnly}
                value={agent.instructions ?? ''}
                placeholder="例如：梳理本季度主要竞品的功能更新，只输出结构化要点。"
                onChange={(event) => {
                  // 指令 and the YAML's SOUL.md content are the same text, so the
                  // manifest follows this edit — carrying mcp/skills untouched.
                  const instructions = event.target.value;
                  onChange({
                    instructions,
                    config_manifest: syncManifestInstructions(agent.config_manifest, instructions),
                  });
                }}
              />
              <p className="text-xs leading-5 text-muted-foreground">
                具体行为要求，等同 YAML 中 SOUL.md 的 content；两处改任一处都会同步。
              </p>
            </div>
          </TabsContent>

          <TabsContent value="capabilities" className="space-y-4 pt-4">
            {readOnly ? (
              // A reference is the stored definition: its model is that
              // definition's, so showing a picker would promise an edit that
              // does not belong here. Rename or remove the entry to change what
              // it points at.
              <div className="space-y-1.5">
                <Label>独立模型</Label>
                <p className="m-0 rounded-md bg-muted px-3 py-2 text-xs">
                  {agent.model || '跟随执行默认模型'}
                </p>
              </div>
            ) : (
              <ModelSelector
                label="独立模型"
                inheritLabel="跟随执行默认模型"
                value={agent.model ?? null}
                onChange={(model) => onChange({ model })}
              />
            )}
            <ManifestField
              agent={agent}
              synthesized={synthesized}
              error={manifestError}
              readOnly={readOnly}
              onChange={(patch) => {
                // Editing the YAML's SOUL.md writes the same text back to 指令, so
                // the two fields never disagree about the persona.
                const nextManifest = patch.config_manifest;
                if (nextManifest === undefined) {
                  onChange(patch);
                  return;
                }
                const mirrored = readManifestInstructions(nextManifest);
                onChange(mirrored === null ? patch : { ...patch, instructions: mirrored });
              }}
            />
          </TabsContent>
        </Tabs>

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
    </div>
  );
}