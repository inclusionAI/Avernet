import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
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
    <div className="p-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>MD 身份文档</CardTitle>
            <p className="mt-1 text-xs text-[var(--color-muted)]">配置 Bot 的规则、人格、知识和输出约束。</p>
          </div>
          <Button
            disabled={!editable || loading}
            leftIcon={<Save className="size-4" />}
            onClick={() => void onSave(type, content)}
          >
            保存
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
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
        </CardContent>
      </Card>
    </div>
  );
}
