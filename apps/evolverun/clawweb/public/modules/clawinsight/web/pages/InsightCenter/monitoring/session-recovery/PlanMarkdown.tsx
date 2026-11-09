import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function PlanMarkdown({ text }: { text: string }) {
  // Plain legacy prose gets paragraph breaks at existing sentence boundaries only.
  // Authored Markdown and multiline content retain their original structure.
  const markdown = /[\n`*#\[\]<>~]/.test(text) ? text : text.replace(/([。；])(?=\S)/g, '$1\n\n');
  return <div className="sr-plan-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ node, ...props }) => { void node; return <a {...props} target="_blank" rel="noreferrer" />; } }}>{markdown}</ReactMarkdown></div>;
}
