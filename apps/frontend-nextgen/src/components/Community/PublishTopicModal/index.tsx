import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { ArrowLeft } from 'lucide-react';
import { useEffect, useState } from 'react';

interface PublishTopicModalProps {
  open: boolean;
  publishing: boolean;
  onOpenChange: (open: boolean) => void;
  onPublish: (title: string, body: string) => Promise<boolean>;
}

/**
 * 发布主题编辑视图。虽沿用历史组件名以保持调用点/测试稳定，但 UI 是社区内容区内的
 * inline editor，不再是居中弹窗。
 */
export function PublishTopicModal({ open, publishing, onOpenChange, onPublish }: PublishTopicModalProps) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [errors, setErrors] = useState<{ title?: string; body?: string }>({});

  useEffect(() => {
    if (!open) {
      setTitle('');
      setBody('');
      setErrors({});
    }
  }, [open]);

  const handleSubmit = async () => {
    const nextErrors = {
      ...(!title.trim() ? { title: '请输入主题标题' } : {}),
      ...(!body.trim() ? { body: '请输入主题正文' } : {}),
    };
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    if (await onPublish(title, body)) onOpenChange(false);
  };

  if (!open) return null;

  return (
    <section className="flex h-full flex-col" aria-label="发布主题编辑区">
      <header className="flex items-center gap-2">
        <Button variant="ghost" size="icon" onClick={() => onOpenChange(false)} aria-label="返回社区列表">
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <h2 className="m-0 text-lg font-semibold text-foreground">发布主题</h2>
        <div className="ml-auto flex items-center gap-3">
          <Button variant="outline" size="sm" className="px-4 text-xs" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            size="sm"
            className="bg-foreground px-4 text-xs text-background hover:opacity-90"
            loading={publishing}
            onClick={() => void handleSubmit()}
          >
            发布
          </Button>
        </div>
      </header>

      <div className="mt-4">
        <Input
          className="h-10 rounded-lg border-border bg-card px-3 text-base font-semibold"
          value={title}
          onChange={(event) => {
            setTitle(event.target.value);
            setErrors((value) => ({ ...value, title: undefined }));
          }}
          placeholder="主题"
          maxLength={80}
          aria-label="主题标题"
          aria-invalid={!!errors.title}
        />
        {errors.title && <p className="m-0 mt-2 text-xs text-destructive">{errors.title}</p>}
      </div>

      <div className="mt-4">
        <Textarea
          className="min-h-[280px] rounded-lg border-border bg-card px-3 py-3 text-sm leading-relaxed"
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
            setErrors((value) => ({ ...value, body: undefined }));
          }}
          placeholder="输入正文内容..."
          maxLength={4000}
          variant={errors.body ? 'error' : 'default'}
          aria-label="正文"
          aria-invalid={!!errors.body}
        />
        {errors.body && <p className="m-0 mt-2 text-xs text-destructive">{errors.body}</p>}
      </div>
    </section>
  );
}
