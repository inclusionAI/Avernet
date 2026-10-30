import { Button } from '@/components/ui/Button';
import type { Task } from '@/domain/evolution/types';
import { labelOf } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { CheckCircle2 } from 'lucide-react';
import { Section, typeTone } from './shared';

/** Display the existing creation response; no new creation or execution API. */
export function CreateResult({ task }: { task: Task }) {
  const plans: Record<string, string[]> = {
    diagnose: ['分析历史会话', '提取 Good/Bad Case 与诊断结论', '生成目标规划'],
    bench: ['加载已发布的评测模板', '执行 Bench 评测', '汇总得分与 Session'],
    bench_optimize: ['加载训练与测试评测模板', '按目标优化并回测', '汇总独立验证结果'],
    optimize: ['复用来源诊断及目标规划', '执行优化与验证', '汇总优化产物'],
    full:
      task.config?.inputMode === 'diagnose_goal'
        ? ['分析历史会话', '规划目标并优化', '验证进化结果']
        : ['根据一句话目标生成规划', '按规划优化', '验证进化结果'],
  };
  return (
    <div className="pb-8">
      <div className="py-12 text-center">
        <div className="mx-auto mb-5 flex h-[68px] w-[68px] items-center justify-center rounded-full border-2 border-success bg-success/10 text-success">
          <CheckCircle2 size={34} />
        </div>
        <h2 className="mb-2 text-[22px] font-semibold">任务创建成功</h2>
        <p className="mb-7 text-sm text-muted-foreground">
          「{task.task_name || labelOf(task.task_type)}」已提交，运行状态以服务端返回为准。
        </p>
        <div className="flex justify-center gap-3">
          <Button onClick={() => history.replace(`/evolution-factory/tasks/${encodeURIComponent(task.task_id)}`)}>
            查看任务详情
          </Button>
          <Button variant="outline" onClick={() => history.push('/evolution-factory')}>
            返回任务列表
          </Button>
        </div>
      </div>
      <div className="mx-auto max-w-[640px]">
        <Section title="执行计划">
          <p className="mb-4 text-xs text-muted-foreground">
            以下为任务类型的流程说明，不代表实时进度；实际步骤以任务详情为准。
          </p>
          <ol>
            {(plans[task.task_type] ?? []).map((title, index, items) => (
              <li key={title} className="relative flex gap-3.5 pb-[22px] last:pb-0">
                {index < items.length - 1 && (
                  <span aria-hidden="true" className="absolute bottom-0 left-[13px] top-7 w-px bg-divider" />
                )}
                <span
                  className={`relative flex h-[27px] w-[27px] shrink-0 items-center justify-center rounded-full text-xs ${typeTone(
                    task.task_type,
                  )}`}
                >
                  {index + 1}
                </span>
                <span className="pt-1 text-sm font-medium">{title}</span>
              </li>
            ))}
          </ol>
        </Section>
      </div>
    </div>
  );
}
