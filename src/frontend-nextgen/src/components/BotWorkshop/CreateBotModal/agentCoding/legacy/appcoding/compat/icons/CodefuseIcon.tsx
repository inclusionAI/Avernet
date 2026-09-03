import React from 'react';

export interface SvgIconProps extends React.SVGProps<SVGSVGElement> {
  size?: number | string;
}

export function CodefuseIcon({ size = 16, className, ...props }: SvgIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 1024 1024"
      fill="currentColor"
      className={className}
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      <path d="M896 249.6c-108.8 19.2-262.4 64-339.2 198.4 0 0-89.6 134.4-76.8 352h217.6c0-76.8 6.4-160 38.4-224 19.2-32 38.4-57.6 70.4-83.2 25.6-25.6 57.6-38.4 89.6-51.2v-192z" />
      <path d="M454.4 627.2h-38.4c-57.6-6.4-102.4-51.2-108.8-108.8 0-32 6.4-64 32-83.2 25.6-25.6 51.2-38.4 89.6-38.4s70.4 12.8 96 38.4c32-51.2 70.4-96 115.2-128-57.6-51.2-128-83.2-211.2-83.2C262.4 224 128 352 128 512s134.4 288 300.8 288h12.8c-6.4-64 0-121.6 12.8-172.8zM896 678.4V480c-25.6 12.8-44.8 19.2-64 32-25.6 19.2-44.8 44.8-64 76.8-12.8 25.6-25.6 57.6-25.6 83.2l153.6 6.4z" />
    </svg>
  );
}
