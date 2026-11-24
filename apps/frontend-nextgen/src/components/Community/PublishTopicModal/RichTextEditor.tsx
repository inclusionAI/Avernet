import { Button } from '@/components/ui/Button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { DOMSerializer } from '@tiptap/pm/model';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Bold, Code, Italic, Link, List, ListOrdered, Minus, Quote, Redo, Strikethrough, Undo } from 'lucide-react';
import MarkdownIt from 'markdown-it';
import { useEffect, useRef, useState } from 'react';
import { communityEditorMarkdown } from './richText';

const markdown = new MarkdownIt({ html: false, breaks: true });
const extensions = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3] },
    underline: false,
    codeBlock: false,
    link: { openOnClick: false, autolink: false, linkOnPaste: false, isAllowedUri: (url) => /^https?:\/\//i.test(url) },
  }),
];

export function RichTextEditor({
  value,
  onChange,
  invalid,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
  disabled?: boolean;
}) {
  const emitted = useRef(value);
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState('');
  const editor = useEditor({
    extensions,
    immediatelyRender: true,
    shouldRerenderOnTransaction: true,
    content: markdown.render(value),
    editable: !disabled,
    editorProps: {
      attributes: {
        role: 'textbox',
        'aria-label': '正文',
        'aria-multiline': 'true',
        'aria-describedby': 'community-body-limit',
        class: 'markdown-body min-h-[280px] px-4 py-4 outline-none',
      },
      handlePaste(view, event) {
        event.preventDefault();
        const text = event.clipboardData?.getData('text/plain');
        if (text) view.dispatch(view.state.tr.insertText(text));
        return true;
      },
    },
    onUpdate({ editor: current }) {
      const root = document.createElement('div');
      root.append(DOMSerializer.fromSchema(current.schema).serializeFragment(current.state.doc.content));
      emitted.current = communityEditorMarkdown(root);
      onChange(emitted.current);
    },
  });
  useEffect(() => {
    if (!editor) return;
    if (editor.isEditable !== !disabled) editor.setEditable(!disabled, false);
    editor.view.dom.setAttribute('aria-invalid', String(invalid || value.length > 4000));
    if (value !== emitted.current) {
      editor.commands.setContent(markdown.render(value), { emitUpdate: false });
      emitted.current = value;
    }
  }, [editor, value, invalid, disabled]);
  if (!editor) return null;
  const chain = () => editor.chain().focus();
  const controls = [
    { label: '加粗', mark: 'bold', Icon: Bold, click: () => chain().toggleBold().run() },
    { label: '斜体', mark: 'italic', Icon: Italic, click: () => chain().toggleItalic().run() },
    { label: '删除线', mark: 'strike', Icon: Strikethrough, click: () => chain().toggleStrike().run() },
    { label: '行内代码', mark: 'code', Icon: Code, click: () => chain().toggleCode().run() },
    { label: '无序列表', mark: 'bulletList', Icon: List, click: () => chain().toggleBulletList().run() },
    { label: '有序列表', mark: 'orderedList', Icon: ListOrdered, click: () => chain().toggleOrderedList().run() },
    { label: '引用', mark: 'blockquote', Icon: Quote, click: () => chain().toggleBlockquote().run() },
    {
      label: '插入链接',
      mark: 'link',
      Icon: Link,
      click: () => {
        setLink(link === null ? String(editor.getAttributes('link').href ?? '') : null);
        setError('');
      },
    },
    { label: '分割线', Icon: Minus, click: () => chain().setHorizontalRule().run() },
    { label: '撤销', Icon: Undo, click: () => chain().undo().run() },
    { label: '重做', Icon: Redo, click: () => chain().redo().run() },
  ];
  return (
    <div
      className={`flex flex-1 flex-col rounded-lg border bg-background ${
        invalid || value.length > 4000 ? 'border-destructive' : 'border-border'
      }`}
    >
      <div
        role="toolbar"
        aria-label="正文格式"
        className="flex flex-wrap items-center gap-1 border-b border-divider px-3 py-2"
      >
        <Select
          value={String(editor.isActive('heading') ? editor.getAttributes('heading').level : 0)}
          onValueChange={(value) => {
            const level = Number(value) as 0 | 1 | 2 | 3;
            if (level) chain().setHeading({ level }).run();
            else chain().setParagraph().run();
          }}
          disabled={disabled}
        >
          <SelectTrigger aria-label="段落样式" className="h-9 w-28 shrink-0">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="0">正文</SelectItem>
            {[1, 2, 3].map((level) => (
              <SelectItem key={level} value={String(level)}>
                标题 {level}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <TooltipProvider delayDuration={200}>
          {controls.map(({ label, mark, Icon, click }) => (
            <Tooltip key={label}>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={label}
                  aria-pressed={mark ? editor.isActive(mark) : undefined}
                  className="aria-pressed:bg-muted aria-pressed:text-primary"
                  disabled={disabled}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={click}
                >
                  <Icon className="size-4" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{label}</TooltipContent>
            </Tooltip>
          ))}
        </TooltipProvider>
      </div>
      {link !== null && (
        <div className="flex flex-wrap items-center gap-2 border-b p-2">
          <input
            aria-label="链接地址"
            value={link}
            disabled={disabled}
            placeholder="https://"
            onChange={(e) => setLink(e.target.value)}
          />
          <Button
            size="sm"
            disabled={disabled}
            onClick={() => {
              let href: string;
              try {
                const url = new URL(link);
                if (!['https:', 'http:'].includes(url.protocol)) throw new Error();
                href = url.href;
              } catch {
                setError('请输入有效的 HTTP 或 HTTPS 链接');
                return;
              }
              if (editor.state.selection.empty && !editor.isActive('link'))
                chain()
                  .insertContent({ type: 'text', text: href, marks: [{ type: 'link', attrs: { href } }] })
                  .run();
              else chain().extendMarkRange('link').setLink({ href }).run();
              setLink(null);
            }}
          >
            应用链接
          </Button>
          {error && <span role="alert">{error}</span>}
        </div>
      )}
      <div className="relative flex-1">
        {editor.isEmpty && (
          <span aria-hidden className="pointer-events-none absolute left-4 top-4 text-sm text-muted-foreground">
            输入正文内容...
          </span>
        )}
        <EditorContent editor={editor} />
      </div>
      <p id="community-body-limit" className="m-0 px-4 py-2 text-right text-xs text-muted-foreground">
        {value.length}/4000{value.length > 4000 && '，正文超出长度限制'}
      </p>
    </div>
  );
}
