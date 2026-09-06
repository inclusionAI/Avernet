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
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="m-0 text-sm font-semibold">MD 身份文档</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">配置 Bot 的规则、人格、知识和输出约束。</p>
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
      <div className="space-y-4 px-5 py-4">
        <Select value={type} onValueChange={setType}>
          <SelectTrigger className="w-52" aria-label="文档类型">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(files.length ? files : [{ type: 'RULES', exists: false }]).map((file) => (
              <SelectItem key={file.type} value={file.type}>
                {file.type}
                {file.exists ? ' · 已配置' : ''}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Textarea
          value={content}
          disabled={!editable || loading}
          onChange={(event) => setContent(event.target.value)}
          className="min-h-[430px] font-mono text-xs"
          placeholder={`# ${type}`}
        />
      </div>
    </div>
  );
}
