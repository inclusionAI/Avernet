import { UserMessagePresentationHost } from '@/components/Workspace/UserMessagePresentationHost';
import { parseDebugChatMessageContent } from '@/services/botWorkshop/debugChatFiles';
import { FileText } from 'lucide-react';

function DebugMessageContent({ content }: { content: string }) {
  const parsed = parseDebugChatMessageContent(content);
  return (
    <div className="space-y-1.5">
      {parsed.fileNames.map((name, index) => (
        <span
          key={`${name}-${index}`}
          className="flex w-fit max-w-full items-center gap-1.5 rounded-lg border border-border bg-muted px-2 py-1 text-xs"
        >
          <FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{name}</span>
        </span>
      ))}
      {parsed.text ? <p className="m-0 whitespace-pre-wrap text-xs leading-5">{parsed.text}</p> : null}
    </div>
  );
}

export function DebugUserMessage({ content }: { content: string }) {
  return (
    <UserMessagePresentationHost content={content}>
      {(text) => <DebugMessageContent content={text} />}
    </UserMessagePresentationHost>
  );
}
