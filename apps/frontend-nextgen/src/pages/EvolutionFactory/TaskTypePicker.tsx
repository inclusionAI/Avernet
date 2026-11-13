import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { taskTypes, type TaskType } from '@/domain/evolution/types';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { history } from '@umijs/max';
import { ChevronRight, Info } from 'lucide-react';
import { TaskTypeIcon } from './TaskTypeIcon';

const hints: Record<TaskType, string> = {
  diagnose: '只诊断不修改 · 适合周期性体检',
  bench: '产出可作为 Bench 优化任务的输入',
  bench_optimize: '使用已发布的训练与测试评测模板',
  optimize: '需关联已完成诊断及目标规划的任务',
  full: '自动规划',
};
const descriptions = ['定位问题、量化能力水平', '基于诊断结论定向优化', '按目标规划、优化与验证'];

export function TaskTypePicker({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent className="max-w-[680px] gap-0 p-0" aria-describedby="evolution-task-type-help">
        <ModalHeader className="justify-center border-b border-divider px-5 py-4">
          <ModalTitle className="text-base">新建任务</ModalTitle>
        </ModalHeader>
        <div className="flex flex-col gap-[15px] px-5 pb-[34px] pt-3">
          <p
            id="evolution-task-type-help"
            className="m-0 flex items-start gap-2 rounded bg-primary/5 px-3 py-2 text-xs leading-[18px] text-primary"
          >
            <Info aria-hidden className="mt-px size-4 shrink-0 text-primary" />
            请选择任务类型 —— 三种分类构成「诊断 → 优化 → 验证」的进化工作流
          </p>
          {['诊断类', '优化类', '全流程类'].map((group, index) => (
            <section key={group} className="space-y-[15px]" aria-label={group}>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="m-0 text-[13px] font-medium text-foreground">{group}</h2>
                <span className="text-xs text-muted-foreground">{descriptions[index]}</span>
              </div>
              <div className="space-y-2">
                {taskTypes
                  .filter((type) => type.group === group)
                  .map((type) => {
                    return (
                      <Button
                        key={type.type}
                        variant="ghost"
                        type="button"
                        aria-label={type.label}
                        className="h-auto min-h-[67px] w-full justify-between gap-3 whitespace-normal rounded-lg border border-border px-3 py-3 text-left hover:border-primary hover:bg-primary/5"
                        onClick={() => {
                          Tracert.click(Points.evolution.createEntry);
                          history.push(`/evolution-factory/new?type=${type.type}`);
                        }}
                      >
                        <TaskTypeIcon type={type.type} />
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                            <span className="text-[13px] font-medium text-foreground">{type.label}</span>
                            <span className="text-[11px] font-normal text-muted-foreground">{hints[type.type]}</span>
                          </span>
                          <span className="mt-1 block text-xs font-normal leading-[18px] text-muted-foreground">
                            {type.description}
                          </span>
                        </span>
                        <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                      </Button>
                    );
                  })}
              </div>
            </section>
          ))}
        </div>
      </ModalContent>
    </Modal>
  );
}
