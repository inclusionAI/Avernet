import { notifyError } from '@/components/ui';
import { Button } from '@/components/ui/Button';
import { generateBotAvatarBatch } from '@/services/botWorkshop/botAvatarGenerator';
import { ImageUp } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

const MAX_AVATAR_SIZE = 2 * 1024 * 1024;
export interface BotAvatarEditorProps {
  value: string;
  onChange: (url: string) => void;
  seed: string;
  disabled?: boolean;
}
export default function AvatarEditor({ value, onChange, seed, disabled }: BotAvatarEditorProps) {
  const [batch, setBatch] = useState(0);
  const uploadRef = useRef<HTMLInputElement>(null);
  const variants = useMemo(() => generateBotAvatarBatch(seed, batch), [seed, batch]);

  const handleUpload = (file?: File) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      notifyError('请选择图片文件');
      return;
    }
    if (file.size > MAX_AVATAR_SIZE) {
      notifyError('头像图片不能超过 2 MB');
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => notifyError('头像读取失败，请重新选择');
    reader.onload = () => {
      if (typeof reader.result === 'string') onChange(reader.result);
    };
    reader.readAsDataURL(file);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <img
          src={value || variants[0]}
          alt="当前 Bot 头像"
          className="size-14 rounded-xl border border-border object-cover"
        />
        <div className="space-y-1">
          <input
            ref={uploadRef}
            type="file"
            accept="image/*"
            aria-label="上传 Bot 头像"
            className="sr-only"
            disabled={disabled}
            onChange={(event) => {
              handleUpload(event.target.files?.[0]);
              event.target.value = '';
            }}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            leftIcon={<ImageUp className="size-3.5" aria-hidden />}
            onClick={() => uploadRef.current?.click()}
          >
            上传图片
          </Button>
          <p className="text-[10px] text-muted-foreground">支持常见图片格式，最大 2 MB</p>
        </div>
      </div>
      <div className="grid grid-cols-4 gap-2">
        {variants.map((url, i) => (
          <Button
            key={url}
            variant={value === url ? 'default' : 'outline'}
            type="button"
            disabled={disabled}
            aria-label={`选择头像 ${i + 1}`}
            className="h-auto p-1"
            onClick={() => onChange(url)}
          >
            <img src={url} alt="" className="size-12" />
          </Button>
        ))}
      </div>
      <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => setBatch((n) => n + 1)}>
        换一批
      </Button>
    </div>
  );
}
