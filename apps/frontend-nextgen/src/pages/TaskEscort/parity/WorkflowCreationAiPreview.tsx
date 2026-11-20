import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import { workflowAuthoringYaml } from '@/domain/taskEscortParity/workflowAuthoring';
import type { useWorkflowCreationAi } from '@/hooks/taskEscortParity/useWorkflowCreationAi';
import { Check, Sparkles } from 'lucide-react';
import { useId } from 'react';

interface Props {
  ai: ReturnType<typeof useWorkflowCreationAi>;
  fieldsValid: boolean;
  creating: boolean;
}
export function WorkflowCreationAiPreview({ ai, fieldsValid, creating }: Props) {
  const promptId = useId();
  const disclosureId = useId();
  return (
    <section aria-label="AI 工作流助手" className="space-y-3 rounded-lg border border-input bg-card p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Sparkles size={16} className="text-primary" aria-hidden />
        AI 工作流助手
      </h3>
      <div className="space-y-1.5">
        <label htmlFor={promptId} className="text-xs font-medium">
          描述你的工作流需求
        </label>
        <Textarea
          id={promptId}
          value={ai.prompt}
          rows={4}
          maxLength={4000}
          disabled={creating}
          onChange={(event) => ai.changePrompt(event.target.value)}
          aria-describedby={disclosureId}
          placeholder="说明工作流目标、步骤、输入输出，以及需要保留的配置…"
        />
      </div>
      <p id={disclosureId} className="text-xs leading-5 text-muted-foreground">
        来源工作流的完整 YAML
        和需求描述会发送到已配置的生成服务。生成和应用草稿均不会自动保存或部署；请审阅后单独点击创建。
      </p>
      {ai.source.loading && (
        <p role="status" className="text-xs text-muted-foreground">
          正在校验来源编辑权限与读取工作流…
        </p>
      )}
      {ai.source.error && (
        <p role="alert" className="text-xs text-destructive">
          {ai.source.error}
        </p>
      )}
      {!ai.source.loading && !ai.source.data && !ai.source.error && (
        <p className="text-xs text-muted-foreground">请选择有编辑权限的来源工作流后生成草稿。</p>
      )}
      {!fieldsValid && <p className="text-xs text-muted-foreground">请先填写有效的工作流 ID 和名称。</p>}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={
            !fieldsValid ||
            !ai.source.data ||
            ai.source.loading ||
            !!ai.source.error ||
            !ai.prompt.trim() ||
            ai.prompt.length > 4000 ||
            ai.busy ||
            creating
          }
          onClick={() => void ai.generate()}
        >
          <Sparkles size={14} aria-hidden />
          {ai.busy
            ? '生成中…'
            : ai.recoveryJob
            ? '恢复查询'
            : ai.validationRetry
            ? '重试校验'
            : ai.error
            ? '重试生成'
            : '生成 YAML 草稿'}
        </Button>
        {(ai.busy || ai.recoveryJob) && (
          <Button size="sm" variant="outline" onClick={ai.cancel}>
            取消生成
          </Button>
        )}
      </div>
      {ai.recoveryJob && (
        <p className="text-xs text-muted-foreground">
          提交或查询结果未确认，请恢复同一任务查询，不会重新派发。修改输入或取消仅停止接纳结果，上游任务仍可能运行。
        </p>
      )}
      <div aria-live="polite" aria-atomic="true" className="text-xs">
        {ai.busy && (
          <p role="status" className="text-muted-foreground">
            {ai.phase === 'validating'
              ? '正在校验 YAML 草稿…'
              : ai.job?.status === 'pending'
              ? '生成任务排队中…'
              : 'AI 正在生成工作流…'}
          </p>
        )}
        {ai.phase === 'cancelled' && (
          <p role="status" className="text-muted-foreground">
            已停止等待，不再接纳本次结果；远端任务取消以服务端确认为准。
          </p>
        )}
        {ai.error && (
          <p role="alert" className="text-destructive">
            {ai.error}
          </p>
        )}
      </div>
      {ai.candidate && (
        <section aria-label="AI 草稿预览" className="space-y-3 border-t border-input pt-3">
          <div>
            <h4 className="text-sm font-semibold">草稿待审阅</h4>
            {ai.job?.result?.summary && (
              <p className="mt-1 whitespace-pre-wrap text-xs leading-5">{ai.job.result.summary}</p>
            )}
            <p className="mt-1 text-xs text-muted-foreground">校验通过 · {ai.candidate.nodes.length} 个节点</p>
          </div>
          <pre
            aria-label="生成的 YAML"
            className="max-h-56 overflow-auto rounded-md border border-input bg-muted p-3 font-mono text-[11px] leading-5"
          >
            {workflowAuthoringYaml(ai.candidate)}
          </pre>
          <Button
            size="sm"
            variant={ai.appliedSpec ? 'outline' : 'default'}
            disabled={!!ai.appliedSpec || ai.busy || creating}
            onClick={ai.apply}
          >
            {ai.appliedSpec ? (
              <>
                <Check size={14} aria-hidden />
                已应用到创建表单
              </>
            ) : (
              '应用草稿到创建表单'
            )}
          </Button>
          <p className="text-xs text-muted-foreground">仅准备待创建草稿；确认后仍需点击“创建”。</p>
        </section>
      )}
    </section>
  );
}
