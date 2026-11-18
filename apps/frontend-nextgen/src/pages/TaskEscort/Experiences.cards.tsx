import { ArrowRight, BookOpenCheck, CheckCircle2, Clock3, GitBranch } from 'lucide-react';
import type { Experience } from './types';
import { Badge, Button } from './ui';
export function ExperienceCard({ experience, onOpen }: { experience: Experience; onOpen: () => void }) {
  return (
    <article
      className="group flex min-h-56 flex-col rounded-xl border border-border bg-card p-5 transition-colors hover:border-primary/25"
      data-testid="experience-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <BookOpenCheck className="size-4" />
          </span>
          <Badge tone="outline" className="font-normal">
            {experience.category}
          </Badge>
        </div>
        {experience.verified ? (
          <Badge tone="success" className="gap-1 font-normal">
            <CheckCircle2 className="size-3" />
            已验证
          </Badge>
        ) : (
          <Badge tone="neutral" className="font-normal">
            待验证
          </Badge>
        )}
      </div>
      <h3 className="mt-5 text-sm font-semibold leading-6">{experience.title}</h3>
      <p className="mt-2 line-clamp-2 text-xs leading-6 text-muted-foreground">{experience.description}</p>
      <div className="mt-auto pt-5">
        <div className="flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <GitBranch className="size-3.5 shrink-0" />
            <span className="truncate">{experience.workflowName}</span>
          </span>
          <span className="inline-flex shrink-0 items-center gap-1">
            <Clock3 className="size-3" />
            复用 {experience.reuseCount} 次
          </span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="mt-3 h-7 w-full justify-between px-0 text-xs text-primary hover:bg-transparent"
          aria-label={`查看经验：${experience.title}`}
          onClick={onOpen}
        >
          查看详情
          <ArrowRight className="size-3.5" />
        </Button>
      </div>
    </article>
  );
}
