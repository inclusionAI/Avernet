import { Button } from '@/components/ui/Button';
import * as bottts from '@dicebear/bottts';
import { createAvatar } from '@dicebear/core';
import { useMemo, useState } from 'react';
export interface BotAvatarEditorProps {
  value: string;
  onChange: (url: string) => void;
  seed: string;
  disabled?: boolean;
}
export default function AvatarEditor({ value, onChange, seed, disabled }: BotAvatarEditorProps) {
  const [batch, setBatch] = useState(0);
  const variants = useMemo(
    () =>
      Array.from({ length: 8 }, (_, i) =>
        createAvatar(bottts, {
          seed: `${seed}-${batch * 8 + i}`,
          size: 80,
          backgroundColor: ['b6e3f4', 'c0aede', 'd1d4f9', 'ffd5dc', 'ffdfbf'],
        }).toDataUri(),
      ),
    [seed, batch],
  );
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-4 gap-2">
        {variants.map((url, i) => (
          <Button
            key={url}
            variant={value === url ? 'default' : 'outline'}
            disabled={disabled}
            aria-label={`选择头像 ${i + 1}`}
            className="h-auto p-1"
            onClick={() => onChange(url)}
          >
            <img src={url} alt="" className="size-12" />
          </Button>
        ))}
      </div>
      <Button variant="outline" disabled={disabled} onClick={() => setBatch((n) => n + 1)}>
        换一批
      </Button>
    </div>
  );
}
