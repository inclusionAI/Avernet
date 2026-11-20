import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { workflowAuthoringSignature } from '@/domain/taskEscortParity/workflowAuthoring';
import { useEditorAiDraft } from '@/hooks/taskEscortParity/useEditorAiDraft';
import { useEffect, useId, useRef } from 'react';
import { EditorAiPreview } from './EditorAiPreview';

export function EditorAiPanel({
  context,
  yaml,
  spec,
  blockedReason,
  onApply,
  onClose,
}: {
  context: TaskEscortParityContext;
  yaml: string;
  spec: EditorSpec;
  blockedReason?: string;
  onApply: (spec: EditorSpec) => void;
  onClose: () => void;
}) {
  const ai = useEditorAiDraft(context, yaml, spec, !!blockedReason, onApply);
  const promptId = useId(),
    disclosureId = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    input.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && !event.isComposing) {
        event.preventDefault();
        close.current();
      }
    };
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('keydown', escape);
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  const unavailable = !ai.capability?.available;
  const busy = !!ai.phase;
  return (
    <aside
      role="region"
      aria-label="AI 工作流助手"
      className="min-w-0 space-y-4 rounded-lg border border-input bg-background p-5 xl:w-[420px]"
    >
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="m-0 text-base font-semibold">
            <span aria-hidden className="mr-2 text-primary">
              ✦
            </span>
            AI 工作流助手
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">基于当前草稿生成修改候选</p>
        </div>
        <Button aria-label="关闭 AI 助手" variant="ghost" size="sm" onClick={onClose}>
          关闭
        </Button>
      </header>
      <section aria-label="当前上下文" className="rounded-lg border border-input bg-card p-3">
        <h3 className="m-0 text-xs font-semibold">当前工作流</h3>
        <p className="mt-2 break-all font-mono text-xs">{spec.id}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {spec.nodes.length} 个节点 · 基于当前编辑内容生成（含未保存 YAML）
        </p>
      </section>
      <div className="rounded-lg bg-muted p-2 text-center text-xs font-medium">修改现有流程</div>
      {blockedReason && (
        <p role="status" className="rounded-md bg-muted p-3 text-xs">
          {blockedReason}
        </p>
      )}
      {unavailable && (
        <p role="status" className="text-xs text-muted-foreground">
          {ai.capability?.reason ?? '正在检查 AI 修改能力…'}
        </p>
      )}
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          void ai.generate();
        }}
      >
        <label htmlFor={promptId} className="block text-xs font-semibold">
          描述对现有流程的修改
        </label>
        <p className="text-xs leading-5 text-muted-foreground">
          基于当前节点和配置修改，不重新创建整个工作流。示例仅填入需求，实际变更由生成服务返回。
        </p>
        <div className="flex flex-wrap gap-2">
          {['调整现有人工确认节点的超时配置，并说明影响', '检查现有节点依赖，按我的要求调整执行顺序'].map((example) => (
            <Button
              key={example}
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              className="h-auto whitespace-normal text-left text-xs"
              onClick={() => {
                ai.changePrompt(example);
                input.current?.focus();
              }}
            >
              {example}
            </Button>
          ))}
        </div>
        <Textarea
          ref={input}
          id={promptId}
          value={ai.prompt}
          onChange={(event) => ai.changePrompt(event.target.value)}
          rows={4}
          disabled={busy}
          aria-describedby={disclosureId}
          placeholder="说明目标、要保留的配置，以及允许调整的节点…"
        />
        <p
          id={disclosureId}
          className="rounded-md border border-input bg-muted p-3 text-xs leading-5 text-muted-foreground"
        >
          生成时会将当前工作流的完整 YAML（包括未保存草稿）和需求描述发送到已配置的 AI
          生成服务。生成和应用草稿均不会自动保存或发布，需另行操作。
        </p>
        <div aria-live="polite" aria-atomic="true">
          {ai.error && (
            <p role="alert" className="text-xs text-destructive">
              {ai.error}
            </p>
          )}
          {ai.notice && (
            <p role="status" className="text-xs text-muted-foreground">
              {ai.notice}
            </p>
          )}
          {ai.phase === 'generate' && (
            <p role="status" className="text-xs text-muted-foreground">
              正在生成草稿，可取消。{ai.job ? `任务状态：${ai.job.status}` : '正在提交当前草稿…'}
            </p>
          )}
        </div>
        <Button
          type="submit"
          size="sm"
          className="w-full"
          aria-describedby={disclosureId}
          disabled={!!blockedReason || unavailable || !ai.prompt.trim() || busy}
        >
          {ai.phase === 'generate' ? '生成中…' : ai.error ? '重试生成' : ai.stale ? '重新生成草稿' : '生成 YAML 草稿'}
        </Button>
        {busy && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="w-full"
            disabled={ai.phase === 'cancel'}
            onClick={() => void ai.cancel()}
          >
            {ai.phase === 'cancel' ? '取消中…' : '取消生成'}
          </Button>
        )}
      </form>
      {ai.candidate && (
        <EditorAiPreview
          key={`${ai.candidate.job.draftId}:${
            ai.candidate.spec ? workflowAuthoringSignature(ai.candidate.spec) : 'unvalidated'
          }`}
          ai={ai}
          blocked={!!blockedReason}
        />
      )}
    </aside>
  );
}
