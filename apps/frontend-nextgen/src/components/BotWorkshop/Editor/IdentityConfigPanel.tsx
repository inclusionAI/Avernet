import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { BotIdentityFile } from '@/domain/botAdvancedConfig';
import { MarkdownRenderer } from '@tc-chat/ui/es/MarkdownRender';
import { Eye, RefreshCw, Save, SquarePen } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

// 设计稿 artboard-049:工具栏为 32×32 白底描边按钮,圆角 10、阴影 0 1px 2px rgba(0,0,0,0.05)。
const toolButtonClass = 'rounded-[10px] text-muted-foreground shadow-[0_1px_2px_0_rgba(0,0,0,0.05)]';

export function IdentityConfigPanel({
  files,
  editable,
  getFile,
  onSave,
}: {
  files: BotIdentityFile[];
  editable: boolean;
  getFile: (type: string) => Promise<string>;
  onSave: (type: string, content: string) => Promise<void>;
}) {
  const [type, setType] = useState('RULES');
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  // 文档默认以预览态呈现(设计稿稿面即预览),编辑入口收进预览卡片右上角工具栏。
  const [mode, setMode] = useState<'preview' | 'edit'>('preview');
  // 只读访客无论如何都停留在预览态。
  const activeMode = editable ? mode : 'preview';
  const loadContent = useCallback(() => {
    setLoading(true);
    return getFile(type)
      .then(setContent)
      .finally(() => setLoading(false));
  }, [getFile, type]);
  useEffect(() => {
    void loadContent();
  }, [loadContent]);
  return (
    <div className="flex min-h-full flex-col bg-background">
      <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-divider bg-card px-8 lg:px-10">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold">MD 身份文档</h2>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">配置 Bot 的规则、人格、知识和输出约束。</p>
        </div>
      </div>
      {/* 顶边不加 padding:让分割线从 header 底边笔直落下(与 title 的 bottom border 同色同粗、连成一体)。 */}
      {/* 左右留白收敛(artboard-049 实测:侧栏左边距远小于右侧 40px 的对话留白,走道仅 20px)。 */}
      <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 px-4 lg:px-6">
        {/* artboard-049:文件列表不带卡片边框,右侧预览/操作区同理,两侧仅靠分割线分界。 */}
        {/* 文档列表宽度对齐设计稿侧边栏的 128px(artboard-049);顶边 pt-4 使首行与右侧工具栏(y=117)齐平。 */}
        <aside className="hidden w-32 shrink-0 pt-4 md:block">
          {(files.length ? files : [{ type: 'RULES', exists: false }]).map((file) => (
            <Button
              key={file.type}
              variant="ghost"
              // 选中态对齐设计稿 artboard-049(Frame 1912056273):灰底 rgba(9,9,11,0.1)、圆角 6,文字保持前景色,不再用品牌蓝。
              className={`mb-1 w-full justify-start rounded-md px-2 text-sm font-normal ${
                type === file.type ? 'bg-foreground/10 hover:bg-foreground/10' : ''
              }`}
              onClick={() => setType(file.type)}
            >
              <span className="truncate">{file.type}.MD</span>
              {file.exists ? <span className="ml-auto size-1.5 rounded-full bg-success" aria-label="已配置" /> : null}
            </Button>
          ))}
        </aside>
        {/* 分割线与 header 底边(1px border-border)同色同粗,self-stretch 从 header 底边直落页底;紧贴列表右缘(artboard-049 侧栏描边位置)。 */}
        <div aria-hidden className="hidden w-px shrink-0 self-stretch bg-divider md:block" />
        {/* 分割线右侧 20px 走道(md:ml-5)对齐设计稿侧栏右缘(188)到预览卡左缘(208)的间距。 */}
        <div className="flex min-w-0 flex-1 flex-col gap-3 pt-4 pb-6 md:ml-5">
          {/* artboard-049:工具栏与内容卡之间没有分割线,仅靠 12px 间距(gap-3)过渡。 */}
          <div className="flex items-center justify-between gap-3">
            <h3 className="m-0 text-base font-semibold">{type}.MD</h3>
            <div className="flex items-center gap-2">
              <Select value={type} onValueChange={setType}>
                <SelectTrigger className="h-8 w-36 md:hidden" aria-label="文档类型">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(files.length ? files : [{ type: 'RULES', exists: false }]).map((file) => (
                    <SelectItem key={file.type} value={file.type}>
                      {file.type}.MD
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <IconButton
                label="预览"
                icon={<Eye className="size-4" />}
                variant="secondary"
                className={toolButtonClass}
                onClick={() => setMode('preview')}
              />
              <IconButton
                label="刷新"
                icon={<RefreshCw className="size-4" />}
                variant="secondary"
                className={toolButtonClass}
                disabled={loading}
                onClick={() => void loadContent()}
              />
              <IconButton
                label="编辑"
                icon={<SquarePen className="size-4" />}
                variant="secondary"
                className={toolButtonClass}
                disabled={!editable || loading}
                onClick={() => setMode('edit')}
              />
              <IconButton
                label="保存"
                icon={<Save className="size-4" />}
                variant="secondary"
                className={toolButtonClass}
                disabled={!editable}
                onClick={() => void onSave(type, content)}
              />
            </div>
          </div>
          {activeMode === 'edit' ? (
            <Textarea
              value={content}
              disabled={!editable || loading}
              onChange={(event) => setContent(event.target.value)}
              className="min-h-[430px] flex-1 resize-none border-0 bg-muted/20 font-mono text-xs leading-6 shadow-none focus-visible:ring-0"
              placeholder={`# ${type}`}
            />
          ) : (
            /* 预览内容卡对齐设计稿 artboard-049(Rectangle 26 Copy 23):白底 + 1px #D8D8D8 描边 + 圆角 8。 */
            <div className="app-scrollbar min-h-[430px] flex-1 overflow-y-auto rounded-lg border border-[#D8D8D8] bg-background px-4 py-3 dark:border-zinc-700 dark:bg-muted/20">
              {content ? (
                <MarkdownRenderer content={content} />
              ) : (
                <p className="m-0 text-sm text-muted-foreground">
                  {loading ? '加载中…' : `暂无 ${type}.MD 内容。`}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
