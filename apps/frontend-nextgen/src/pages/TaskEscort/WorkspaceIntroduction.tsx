import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  Layers3,
  Plus,
  Search,
  ShieldCheck,
  Undo2,
} from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import './introduction.css';
import { WorkflowCreationDialog } from './parity/WorkflowCreationDialog';

const loop = [
  { icon: Layers3, code: 'OBSERVE', title: '观测运行', description: '运行轨迹 · 节点耗时' },
  { icon: Search, code: 'DIAGNOSE', title: '诊断问题', description: '异常证据 · 原因定位' },
  { icon: ShieldCheck, code: 'REVIEW', title: '审阅优化', description: '变更草稿 · 人工确认' },
  { icon: Check, code: 'VERIFY', title: '验证与复用', description: '验证效果 · 明确边界' },
];
const benefits = [
  {
    icon: Layers3,
    title: '看清每一次执行',
    description: '从运行状态下钻到节点轨迹，识别失败、阻塞与耗时瓶颈，不再只看最终结果。',
  },
  {
    icon: Search,
    title: '让诊断回到证据',
    description: '围绕异常运行查看原因与建议，把问题定位到具体节点，而不是盲目调整配置。',
  },
  {
    icon: ShieldCheck,
    title: '把变更留在可控范围',
    description: '先生成优化草稿，再审阅影响与适用边界。保留人工确认，不自动发布或绕过审批。',
  },
  {
    icon: BookOpen,
    title: '把有效解法变成经验',
    description: '应用建议不等于修复成功。验证效果后，再沉淀证据与边界，供相似场景参考。',
  },
];
const steps = [
  { title: '明确任务目标', description: '创建工作流，填写唯一标识与名称。' },
  { title: '配置执行节点', description: '进入编辑器，编排节点并审阅配置。保存与发布分别确认。' },
  { title: '了解护航过程', description: '有真实运行后，再查看运行轨迹、诊断与验证证据；新建草稿暂无运行记录。' },
];

/** A product introduction, not a synthetic dashboard or a source of workflow evidence. */
export function WorkspaceIntroduction({ context }: { context: TaskEscortParityContext }) {
  const titleId = useId();
  const benefitId = useId();
  const startId = useId();
  const unavailableId = useId();
  const creationScope = JSON.stringify([context.scopeKey, context.userId]);
  const creationReady = (context.creationReady ?? context.ready) && !!context.userId?.trim();
  const [openScope, setOpenScope] = useState<string | null>(null);
  useEffect(() => setOpenScope(null), [creationScope, creationReady]);

  return (
    <div className="task-escort-introduction">
      <PageHeader title="任务护航" description="围绕工作流的每一次执行，完成「观测 → 诊断 → 优化 → 验证」。" />
      <section className="escort-intro-hero" aria-labelledby={titleId}>
        <div className="escort-intro-copy">
          <div className="mb-5 flex items-center gap-2 text-xs text-primary">
            <ShieldCheck className="size-4" aria-hidden />
            工作流可靠性 · 从一次运行开始
          </div>
          <h2 id={titleId}>
            让任务执行有迹可循，
            <br />
            让每次改进有据可依。
          </h2>
          <p className="mt-4 max-w-lg text-[13px] leading-7 text-muted-foreground">
            把分散的运行记录，变成可审阅的改进线索。
            <br />
            通过工作流编排串联观测、诊断、优化与验证，让你看清异常发生在哪里，也掌握下一步如何改进。
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button
              disabled={!creationReady}
              aria-describedby={!creationReady ? unavailableId : undefined}
              onClick={() => setOpenScope(creationScope)}
            >
              <Plus className="size-4" aria-hidden />
              {context.workflow ? '新建工作流' : '创建第一个工作流'}
            </Button>
          </div>
          {!creationReady && (
            <p id={unavailableId} className="mt-3 text-xs text-muted-foreground">
              请先加载当前用户身份后再新建工作流。
            </p>
          )}
          <p className="mt-4 flex items-start gap-1.5 text-[11px] leading-5 text-muted-foreground">
            <Check className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            先审阅，再应用；验证之后，才成为可复用经验。
          </p>
        </div>
        <figure className="escort-intro-map">
          <figcaption className="mb-5 flex flex-wrap items-center justify-between gap-2 text-xs font-medium">
            一条可追溯的护航闭环
            <span className="text-[10px] font-normal text-muted-foreground">能力示意 · 非实时数据</span>
          </figcaption>
          <div className="escort-intro-loop">
            <ol className="escort-intro-loop-steps" aria-label="护航闭环">
              {loop.map(({ icon: Icon, code, title, description }, index) => (
                <li key={code} className="min-w-0 rounded-lg border border-border bg-card p-3.5">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <Icon className="size-4 text-primary" aria-hidden />
                    <span className="font-mono text-[10px] text-muted-foreground">
                      0{index + 1} / {code}
                    </span>
                  </div>
                  <h3 className="text-xs font-medium">{title}</h3>
                  <p className="mt-1 text-[10px] text-muted-foreground">{description}</p>
                </li>
              ))}
            </ol>
            <ArrowRight className="escort-intro-arrow escort-intro-arrow-first" aria-hidden />
            <ArrowDown className="escort-intro-arrow escort-intro-arrow-second" aria-hidden />
            <ArrowLeft className="escort-intro-arrow escort-intro-arrow-third" aria-hidden />
          </div>
          <p className="mt-4 flex items-start gap-2 border-t border-dashed border-primary/20 pt-3 text-[10px] leading-5 text-muted-foreground">
            <Undo2 className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden />
            验证证据 → 可复用经验 → 为下一次运行提供参考
          </p>
        </figure>
      </section>
      <section className="border-t border-border py-7" aria-labelledby={benefitId}>
        <div className="mb-6 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id={benefitId} className="text-base font-semibold">
            不止发现异常，更让改进形成闭环
          </h2>
          <p className="text-xs text-muted-foreground">机器整理线索，你掌握关键决策。</p>
        </div>
        <div className="escort-intro-benefits">
          {benefits.map(({ icon: Icon, title, description }, index) => (
            <article key={title}>
              <div className="mb-3 flex items-center gap-2 text-primary">
                <Icon className="size-4" aria-hidden />
                <span className="font-mono text-[10px] text-muted-foreground">0{index + 1}</span>
              </div>
              <h3 className="mb-2 text-xs font-medium">{title}</h3>
              <p className="text-[11px] leading-6 text-muted-foreground">{description}</p>
            </article>
          ))}
        </div>
      </section>
      <section className="escort-intro-start rounded-xl border border-border bg-muted/30 p-6" aria-labelledby={startId}>
        <div>
          <span className="text-[11px] text-primary">如何开始</span>
          <h2 id={startId} className="my-2 text-sm font-semibold">
            先从一个工作流开始
          </h2>
          <p className="text-[11px] leading-6 text-muted-foreground">新建草稿从零配置，不会自动部署或执行。</p>
        </div>
        <ol className="grid gap-5 sm:grid-cols-3">
          {steps.map(({ title, description }, index) => (
            <li key={title}>
              <span className="mb-2 block font-mono text-[10px] text-primary">0{index + 1}</span>
              <h3 className="mb-1 text-xs font-medium">{title}</h3>
              <p className="text-[11px] leading-6 text-muted-foreground">{description}</p>
            </li>
          ))}
        </ol>
      </section>
      {creationReady && openScope === creationScope && (
        <WorkflowCreationDialog key={creationScope} context={context} onClose={() => setOpenScope(null)} />
      )}
    </div>
  );
}
