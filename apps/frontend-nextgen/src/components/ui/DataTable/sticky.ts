export type StickySide = 'left' | 'right';

/**
 * 固定列定位 + 不透明垫底（表头与数据单元格共用）。
 * position:sticky 自身即为定位上下文，内层绝对定位层以本格为包含块。
 */
export function stickyPositionClass(sticky: StickySide): string {
  return `sticky ${sticky === 'left' ? 'left-0' : 'right-0'} z-10 bg-card`;
}

/**
 * 滚动遮挡阴影层：贴容器边缘一侧画黑色轻渐变，表达「该列浮于内容之上、
 * 边缘外仍有被遮列内容」。显隐由滚动容器写入的 CSS 变量驱动（滚到尽头即消失）。
 * 注意：Tailwind 类必须为完整字面量，禁止模板变量拼接类名。
 */
export function stickyShadowClass(sticky: StickySide): string {
  const edge = sticky === 'left' ? 'right-0' : 'left-0';
  const direction = sticky === 'left' ? 'bg-gradient-to-l' : 'bg-gradient-to-r';
  const opacity =
    sticky === 'left' ? 'opacity-[var(--sticky-left-shadow,0)]' : 'opacity-[var(--sticky-right-shadow,0)]';
  return [
    'pointer-events-none absolute top-0 bottom-0 w-2.5',
    edge,
    direction,
    'from-black/10 to-transparent',
    opacity,
    'transition-opacity duration-200',
  ].join(' ');
}
