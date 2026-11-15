import { Button } from '@/components/ui/Button';
import type { TodoArtifact } from '@/domain/todos/models';
import { ArrowRight, Code2, FileText, Link2 } from 'lucide-react';
import { TodoInteractionActorHeader } from './TodoInteractionActorHeader';
import { artifactPresentation, type InteractionEntry } from './todoInteractionModel';

function ArtifactIcon({ kind }: { kind: 'file' | 'link' | 'code' }) {
  if (kind === 'file') return <FileText className="size-5" aria-hidden />;
  if (kind === 'link') return <Link2 className="size-5" aria-hidden />;
  return <Code2 className="size-5" aria-hidden />;
}

function ArtifactCard({ artifact }: { artifact: Exclude<TodoArtifact, { kind: 'text' }> }) {
  const presentation = artifactPresentation(artifact);
  return (
    <div className="group flex min-h-16 w-full items-start gap-3 rounded-xl border border-border bg-background px-4 py-3 text-left text-foreground transition-colors hover:border-foreground/20 hover:bg-muted/20">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground">
        <ArtifactIcon kind={presentation.icon} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block break-words text-sm font-semibold">{presentation.title}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{presentation.meta}</span>
        {presentation.details.map((detail) => (
          <span key={detail} className="mt-1 block break-all text-xs leading-5 text-muted-foreground">
            {detail}
          </span>
        ))}
      </span>
      <ArrowRight className="mt-2 size-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden />
    </div>
  );
}

interface TodoInteractionArtifactEntryProps {
  entry: Extract<InteractionEntry, { kind: 'artifact' }>;
  botId: string;
  botName: string;
  humanName: string;
  humanAvatarUrl?: string;
  busy: boolean;
  onOpenFile: (artifact: Extract<TodoArtifact, { kind: 'file' }>) => void;
}

export function TodoInteractionArtifactEntry({
  entry,
  botId,
  botName,
  humanName,
  humanAvatarUrl,
  busy,
  onOpenFile,
}: TodoInteractionArtifactEntryProps) {
  const actorBotId = entry.event?.actorId || botId;
  const createdAt = entry.event?.createdAt || entry.artifact.createdAt;
  return (
    <article className="min-w-0">
      <TodoInteractionActorHeader
        actor="bot"
        createdAt={createdAt}
        botId={actorBotId}
        botName={botName}
        humanName={humanName}
        humanAvatarUrl={humanAvatarUrl}
      />
      <div className="mt-3 pl-[52px]">
        {entry.artifact.kind === 'text' ? (
          <div>
            <p className="m-0 text-xs font-medium text-muted-foreground">{entry.artifact.artifactName}</p>
            <p className="m-0 mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-foreground">
              {entry.artifact.content.text}
            </p>
          </div>
        ) : entry.artifact.kind === 'file' ? (
          <Button
            variant="ghost"
            className="block h-auto w-full rounded-xl border-0 bg-transparent p-0 text-left hover:bg-transparent"
            aria-label={`打开产物 ${entry.artifact.content.fileName || entry.artifact.artifactName}`}
            disabled={busy}
            onClick={() => onOpenFile(entry.artifact as Extract<TodoArtifact, { kind: 'file' }>)}
          >
            <ArtifactCard artifact={entry.artifact} />
          </Button>
        ) : (
          <a
            href={artifactPresentation(entry.artifact).href || '#'}
            target="_blank"
            rel="noreferrer"
            className="block no-underline"
          >
            <ArtifactCard artifact={entry.artifact} />
          </a>
        )}
      </div>
    </article>
  );
}
