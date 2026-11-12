import { Empty } from '@/components/ui';
import type { TodoArtifact } from '@/domain/todos/models';
import { ArrowRight, FileText } from 'lucide-react';

interface TodoArtifactListProps {
  artifacts: TodoArtifact[];
}

export function TodoArtifactList({ artifacts }: TodoArtifactListProps) {
  if (artifacts.length === 0) {
    return <Empty compact title="暂无产物" description="Bot 输出的文件和链接会汇总在这里。" />;
  }

  return (
    <section role="region" aria-label="待办产物" className="space-y-3">
      {artifacts.map((artifact) => (
        <a
          key={`${artifact.artifactId ?? artifact.url}-${artifact.name}`}
          href={artifact.url}
          target="_blank"
          rel="noreferrer"
          className="group flex min-h-[88px] items-center gap-4 rounded-2xl border border-border bg-background px-5 py-4 text-foreground transition-colors hover:border-foreground/20 hover:bg-muted/20"
        >
          <span className="flex size-[52px] shrink-0 items-center justify-center rounded-2xl bg-muted text-foreground">
            <FileText className="size-6" aria-hidden />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-base font-semibold group-hover:text-primary">{artifact.name}</span>
            <span className="mt-1 block truncate text-sm text-muted-foreground">{artifact.url}</span>
          </span>
          <ArrowRight
            className="size-6 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
            aria-hidden
          />
        </a>
      ))}
    </section>
  );
}
