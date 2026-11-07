import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import type { CommunityTopic } from '@/domain/community/types';
import { ArrowLeft } from 'lucide-react';
import { useEffect, useState } from 'react';

export interface EditTopicModalProps {
  open: boolean;
  topic: CommunityTopic | null;
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (draft: { title: string; body: string }) => Promise<boolean>;
}

/** 编辑主题标题与正文；主题类型和投票配置保持不可变。 */
export function EditTopicModal({
  open,
  topic,
  saving,
  onOpenChange,
  onSave,
}: EditTopicModalProps) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && topic) {
      setTitle(topic.title);
      setBody(topic.body);
      setError(null);
    }
  }, [open, topic]);

  if (!open || !topic) return null;

  const validate = (nextTitle: string, nextBody: string) => {
    if (!nextTitle.trim()) return '请输入主题标题';
    if (!nextBody.trim()) return '请输入主题正文';
    return null;
  };

  const submit = async () => {
    const nextError = validate(title, body);
    setError(nextError);
    if (nextError) return;
    if (await onSave({ title, body })) onOpenChange(false);
  };

  return (
    <section className="app-scrollbar flex h-full flex-col overflow-y-auto" aria-label="编辑主题">
      <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-divider bg-background/95 px-4 py-4 backdrop-blur sm:px-6 lg:px-10">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => onOpenChange(false)}
          aria-label="返回主题详情"
        >
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <h2 className="m-0 text-lg font-semibold text-foreground">编辑主题</h2>
        <div className="ml-auto flex items-center gap-3">
          <Button variant="outline" size="sm" className="px-4 text-xs" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button size="sm" className="px-4 text-xs" loading={saving} onClick={() => void submit()}>
            保存
          </Button>
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-4 px-4 py-6 sm:px-6 lg:px-10">
        <Input
          className="h-11 rounded-lg border-border bg-background px-3 text-base font-semibold"
          value={title}
          onChange={(event) => {
            setTitle(event.target.value);
            setError(null);
          }}
          maxLength={80}
          aria-label="主题标题"
          aria-invalid={!!error && !title.trim()}
        />
        <Textarea
          className="min-h-[260px] rounded-lg border-border bg-background px-3 py-4 text-base leading-relaxed"
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
            setError(null);
          }}
          maxLength={4000}
          variant={error && !body.trim() ? 'error' : 'default'}
          aria-label="正文"
          aria-invalid={!!error && !body.trim()}
        />
        {error ? <p className="m-0 text-xs text-destructive">{error}</p> : null}
      </div>
    </section>
  );
}
