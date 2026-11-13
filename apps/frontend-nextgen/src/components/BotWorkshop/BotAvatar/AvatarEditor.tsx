import { notifyError } from '@/components/ui';
import { Button } from '@/components/ui/Button';
import { generateBotAvatarBatch } from '@/services/botWorkshop/botAvatarGenerator';
import type { AvatarEditorProps } from '@/extensions/contracts/avatar';
import { ImageUp, RefreshCw } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

const MAX_AVATAR_SIZE = 2 * 1024 * 1024;
// props 契约已上移至 src/extensions/contracts/avatar.ts（collab-form Task 17 源切片类型完备），
// 本文件反向 import 契约注解，签名漂移在 typecheck 阶段失配。
export default function AvatarEditor({ value, onChange, seed, disabled, hideUpload, extraActions }: AvatarEditorProps) {
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
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <img
          src={value || variants[0]}
          alt="当前 Bot 头像"
          className="size-14 rounded-xl border border-border object-cover"
        />
        <p className="m-0 text-[10px] leading-4 text-muted-foreground">
          支持从下方候选里挑选，或上传/生成新头像；
          <br />
          支持常见图片格式，最大 2 MB
        </p>
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
      {/* 动作行：换一批 / 上传图片 / 宿主追加入口 同行等宽展示；nowrap + 紧凑内边距保证单行。 */}
      <div className="flex gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          className="flex-1 whitespace-nowrap px-2"
          leftIcon={<RefreshCw className="size-3.5" aria-hidden />}
          onClick={() => setBatch((n) => n + 1)}
        >
          换一批
        </Button>
        {hideUpload ? null : (
          <>
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
              className="flex-1 whitespace-nowrap px-2"
              leftIcon={<ImageUp className="size-3.5" aria-hidden />}
              onClick={() => uploadRef.current?.click()}
            >
              上传图片
            </Button>
          </>
        )}
        {extraActions}
      </div>
    </div>
  );
}
