import { CollapsibleUserText } from '@/components/Workspace/ChatPanel/CollapsibleUserText';
import { UserMessagePresentationHost } from '@/components/Workspace/UserMessagePresentationHost';
import { aixUiPlugin, fileRefPlugin, MarkdownRenderer } from '@tc-chat/ui/es/MarkdownRender';
import { markdownPresets } from '@tc-chat/ui/es/MessageRenderer';

// Match Bubble's full Markdown preset, including file references, for multimodal text blocks.
const EXTENSIONS = [...markdownPresets.full, aixUiPlugin, fileRefPlugin];

export function UserMessageText({ content, collapsible, forceExpanded }: {
  content: string;
  collapsible: boolean;
  forceExpanded: boolean;
}) {
  return (
    <UserMessagePresentationHost content={content}>
      {(text) => collapsible ? (
        <CollapsibleUserText content={text} forceExpanded={forceExpanded} />
      ) : (
        <MarkdownRenderer content={text} extensions={EXTENSIONS} />
      )}
    </UserMessagePresentationHost>
  );
}
