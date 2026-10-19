import { Button } from '@/components/ui/Button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { BotIdentityFile } from '@/domain/botAdvancedConfig';
import { Save } from 'lucide-react';
import { useEffect, useState } from 'react';

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
  useEffect(() => {
    setLoading(true);
    void getFile(type)
      .then(setContent)
      .finally(() => setLoading(false));
  }, [getFile, type]);
  return (
    <div className="flex min-h-full flex-col bg-background">
      <div className="flex items-center justify-between gap-4 border-b border-border bg-card px-8 py-5 lg:px-10">
        <div>
          <h2 className="m-0 text-base font-semibold">MD 身份文档</h2>
          <p className="m-0 mt-1 text-sm text-muted-foreground">配置 Bot 的规则、人格、知识和输出约束。</p>
        </div>
        <Button
          size="sm"
          disabled={!editable || loading}
          leftIcon={<Save className="size-4" />}
          onClick={() => void onSave(type, content)}
        >
          保存
        </Button>
      </div>
      <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 gap-5 px-6 py-6 lg:px-10">
        <aside className="hidden w-52 shrink-0 rounded-xl border border-border bg-card p-3 md:block">
          <p className="mb-2 px-2 text-xs font-medium text-muted-foreground">文档列表</p>
          {(files.length ? files : [{ type: 'RULES', exists: false }]).map((file) => (
            <Button
              key={file.type}
              variant="ghost"
              className={`mb-1 w-full justify-start px-2 text-sm font-normal ${type === file.type ? 'bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary' : ''}`}
              onClick={() => setType(file.type)}
            >
              <span className="truncate">{file.type}.MD</span>
              {file.exists ? <span className="ml-auto size-1.5 rounded-full bg-success" aria-label="已配置" /> : null}
            </Button>
          ))}
        </aside>
        <div className="flex min-w-0 flex-1 flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between gap-3 border-b border-border pb-3">
            <h3 className="m-0 text-base font-semibold">{type}.MD</h3>
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
          </div>
          <Textarea
            value={content}
            disabled={!editable || loading}
            onChange={(event) => setContent(event.target.value)}
            className="min-h-[430px] flex-1 resize-none border-0 bg-muted/20 font-mono text-xs leading-6 shadow-none focus-visible:ring-0"
            placeholder={`# ${type}`}
          />
        </div>
      </div>
    </div>
  );
}
