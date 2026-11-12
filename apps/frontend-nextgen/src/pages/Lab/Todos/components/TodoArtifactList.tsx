import { Card, Empty } from '@/components/ui';
import type { TodoArtifact } from '@/domain/todos/models';
import { ExternalLink, FileOutput } from 'lucide-react';

interface TodoArtifactListProps {
  artifacts: TodoArtifact[];
}

export function TodoArtifactList({ artifacts }: TodoArtifactListProps) {
  if (artifacts.length === 0) {
    return <Empty compact title="暂无产物" description="Bot 输出的文件和链接会汇总在这里。" />;
  }

  return (
    <section role="region" aria-label="待办产物" className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {artifacts.map((artifact) => (
        <Card key={`${artifact.artifactId ?? artifact.url}-${artifact.name}`} className="border-border/60 p-4 shadow-sm">
          <a
            href={artifact.url}
            target="_blank"
            rel="noreferrer"
            className="group flex items-start gap-3 text-foreground"
          >
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <FileOutput className="size-4" aria-hidden />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2 text-sm font-medium group-hover:text-primary">
                <span className="truncate">{artifact.name}</span>
                <ExternalLink className="size-3.5 shrink-0" aria-hidden />
              </span>
              <span className="mt-1 block truncate text-xs text-muted-foreground">{artifact.url}</span>
            </span>
          </a>
        </Card>
      ))}
    </section>
  );
}
