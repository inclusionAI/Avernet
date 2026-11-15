import { useMemo, useState } from 'react';
import { Bot } from 'lucide-react';
import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import { imageAvatarSource } from '@/utils/avatarSource';

interface BotDisplayAvatarProps {
  botId: string;
  name: string;
  avatarUrl?: string;
  size?: number;
}

function AvatarImage({ botId, name, avatarUrl, size = 32 }: BotDisplayAvatarProps) {
  const fallback = useMemo(() => resolveBotAvatarUrl(undefined, botId), [botId]);
  const [attempt, setAttempt] = useState(0);
  const candidates = avatarUrl && avatarUrl !== fallback ? [avatarUrl, fallback] : [fallback];
  const src = candidates[attempt];
  const style = { width: size, height: size };
  if (!src) return (
    <span role="img" aria-label={name} style={style}
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
      <Bot aria-hidden="true" style={{ width: size * 0.6, height: size * 0.6 }} />
    </span>
  );
  return <img src={src} alt={name} style={style}
    className="shrink-0 rounded-full object-cover"
    onError={() => setAttempt((current) => current + 1)} />;
}

export function BotDisplayAvatar({ botId, avatarUrl, ...props }: BotDisplayAvatarProps) {
  const id = botId || 'bot';
  const src = imageAvatarSource(avatarUrl);
  return <AvatarImage key={JSON.stringify([id, src])} {...props} botId={id} avatarUrl={src} />;
}
