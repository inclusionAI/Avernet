import React from 'react';

export interface AvatarProps {
  name: string;
  src?: string;
  size?: number /* 默认 32 */;
}

export function Avatar({ name, src, size = 32 }: AvatarProps): JSX.Element {
  const style: React.CSSProperties = { width: size, height: size };
  if (src) {
    return <img src={src} alt={name} className="rounded-full object-cover" style={style} />;
  }
  return (
    <span
      aria-label={name}
      className="inline-flex items-center justify-center rounded-full bg-primary/10 font-medium text-primary"
      style={{ ...style, fontSize: Math.max(10, Math.round(size * 0.4)) }}
    >
      {name.charAt(0)}
    </span>
  );
}
