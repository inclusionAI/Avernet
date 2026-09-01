import React, { useState } from 'react';

export interface AvatarProps {
  name: string;
  src?: string;
  size?: number /* 默认 32 */;
}

export function Avatar({ name, src, size = 32 }: AvatarProps): JSX.Element {
  const style: React.CSSProperties = { width: size, height: size };
  // 图片加载失败（404/跨域/失效 URL）回退首字母占位，避免破图。antwork 照片对部分工号会 404。
  const [broken, setBroken] = useState(false);
  if (src && !broken) {
    return (
      <img
        src={src}
        alt={name}
        onError={() => setBroken(true)}
        className="shrink-0 rounded-full object-cover"
        style={style}
      />
    );
  }
  return (
    <span
      aria-label={name}
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-primary/10 font-medium text-primary"
      style={{ ...style, fontSize: Math.max(10, Math.round(size * 0.4)) }}
    >
      {name.charAt(0)}
    </span>
  );
}
