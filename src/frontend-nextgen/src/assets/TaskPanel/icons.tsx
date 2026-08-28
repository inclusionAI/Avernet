// @asset-migrated: teamclaw 自研资产
/**
 * 内联 SVG 图标（照 PRD demo lucide 等效形，但不用 lucide 依赖，保资产白名单）。
 * 所有图标接收 React.SVGProps，尺寸/颜色由 style 传入。
 */
import React from 'react';
import { C } from './tokens';

type P = React.SVGProps<SVGSVGElement> & { size?: number; color?: string };

const Svg: React.FC<P & { children: React.ReactNode }> = ({ size = 16, color, children, ...rest }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke={color ?? 'currentColor'}
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    {...rest}
  >
    {children}
  </svg>
);

export const CheckCircle: React.FC<P> = (p) => (
  <Svg {...p} fill="none" stroke="none">
    <circle cx="12" cy="12" r="10" fill={p.color ?? C.success} />
    <path d="m7 12 3.2 3.2L17 8.5" fill="none" stroke="#fff" strokeWidth="2.4" />
  </Svg>
);
export const Loading: React.FC<P & { spin?: boolean }> = ({ spin = true, ...p }) => (
  <svg
    width={p.size ?? 16}
    height={p.size ?? 16}
    viewBox="0 0 1024 1024"
    fill={p.color ?? C.warning}
    style={{ animation: spin ? 'task-panel-spin 1s linear infinite' : undefined, ...(p.style ?? {}) }}
    {...p}
  >
    <path d="M988 548c-19.9 0-36-16.1-36-36 0-59.4-11.6-117-34.6-171.3a440.45 440.45 0 00-94.3-139.9 437.71 437.71 0 00-139.9-94.3C629 83.6 571.4 72 512 72c-19.9 0-36-16.1-36-36s16.1-36 36-36c69.1 0 136.2 13.5 199.3 40.3C772.3 66 827 103 874 150c47 47 83.9 101.8 109.7 162.7 26.7 63.1 40.2 130.2 40.2 199.3.1 19.9-16 36-35.9 36z" />
  </svg>
);
export const CloseCircle: React.FC<P> = (p) => (
  <Svg {...p} fill={p.color ?? C.danger} stroke="none">
    <path d="M512 64C264.6 64 64 264.6 64 512s200.6 448 448 448 448-200.6 448-448S759.4 64 512 64zm127.98 274.82h-.04l-.08.06L512 466.75 384.14 338.88c-.04-.05-.06-.06-.08-.06a.12.12 0 00-.07 0c-.03 0-.05.01-.09.05l-45.02 45.02a.2.2 0 00-.05.09.12.12 0 000 .07v.02a.27.27 0 00.06.06L466.75 512 338.88 639.86c-.05.04-.06.06-.06.08a.12.12 0 000 .07c0 .03.01.05.05.09l45.02 45.02a.2.2 0 00.09.05.12.12 0 00.07 0c.02 0 .04-.01.08-.05L512 557.25l127.86 127.87c.04.04.06.05.08.05a.12.12 0 00.07 0c.03 0 .05-.01.09-.05l45.02-45.02a.2.2 0 00.05-.09.12.12 0 000-.07v-.02a.27.27 0 00-.05-.06L557.25 512l127.87-127.86c.04-.04.05-.06.05-.08a.12.12 0 000-.07c0-.03-.01-.05-.05-.09l-45.02-45.02a.2.2 0 00-.09-.05.12.12 0 00-.07 0z" />
  </Svg>
);
export const Clock: React.FC<P> = (p) => (
  <Svg {...p} color={p.color ?? C.textMuted}>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 6v6l4 2" />
  </Svg>
);
export const MinusCircle: React.FC<P> = (p) => (
  <Svg {...p} color={p.color ?? C.textMuted}>
    <circle cx="12" cy="12" r="10" />
    <path d="M8 12h8" />
  </Svg>
);
export const ExternalLink: React.FC<P> = (p) => (
  <Svg {...p} color={p.color ?? C.textSecondary}>
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    <path d="M15 3h6v6" />
    <path d="M10 14 21 3" />
  </Svg>
);
export const ChevronRight: React.FC<P> = (p) => (
  <Svg {...p} color={p.color ?? C.primary}>
    <path d="m9 18 6-6-6-6" />
  </Svg>
);
export const Info: React.FC<P> = (p) => (
  <Svg {...p} color={p.color ?? C.textSecondary}>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 16v-4" />
    <path d="M12 8h.01" />
  </Svg>
);
export const Close: React.FC<P> = (p) => (
  <Svg {...p} color={p.color ?? C.textSecondary}>
    <path d="M18 6 6 18" />
    <path d="m6 6 12 12" />
  </Svg>
);
export const Users: React.FC<P> = (p) => (
  <Svg {...p} color={p.color ?? C.primary}>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </Svg>
);
export const PanelToggle: React.FC<P> = (p) => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" {...p}>
    <rect x="1.5" y="2" width="11" height="10" rx="2" fill="#1D2129" />
    <line x1="9" y1="2" x2="9" y2="12" stroke="#fff" strokeWidth="1.2" />
  </svg>
);

/** 节点状态 → icon（F3） */
/** 节点状态圆点（对齐 PRD demo 图1：实心圆点 + 浅描边） */
export function NodeStatusDot({ status, size = 12 }: { status: string; size?: number }) {
  const color =
    status === 'done'
      ? C.success
      : status === 'running'
      ? C.warning
      : status === 'failed'
      ? C.danger
      : status === 'skipped'
      ? C.textMuted
      : C.textMuted; // pending
  return (
    <span
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        background: color,
        border: `2px solid ${color}33`,
        boxShadow: '0 0 0 2px #fff',
        flexShrink: 0,
      }}
    />
  );
}

/** running 时叠加呼吸环 */
export function NodeStatusPulse({ status, size = 12 }: { status: string; size?: number }) {
  if (status !== 'running') return <NodeStatusDot status={status} size={size} />;
  return (
    <span
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
      }}
    >
      <NodeStatusDot status={status} size={size} />
      <span
        style={{
          position: 'absolute',
          inset: 0,
          borderRadius: '50%',
          border: `2px solid ${C.warning}`,
          animation: 'task-panel-dag-pulse 1.5s ease-in-out infinite',
        }}
      />
    </span>
  );
}

export function NodeStatusIcon({ status, size = 20 }: { status: string; size?: number }) {
  const label =
    status === 'done'
      ? '已完成'
      : status === 'running'
      ? '执行中'
      : status === 'failed'
      ? '执行失败'
      : status === 'skipped'
      ? '已跳过'
      : '待执行';

  const icon =
    status === 'done' ? (
      <CheckCircle size={size} color={C.success} />
    ) : status === 'running' ? (
      <Loading size={Math.round(size * 0.8)} color={C.warning} />
    ) : status === 'failed' ? (
      <CloseCircle size={size} color={C.danger} />
    ) : status === 'skipped' ? (
      <MinusCircle size={size} color={C.textMuted} />
    ) : (
      <NodeStatusDot status={status} size={size} />
    );

  return React.cloneElement(icon, { 'aria-label': label, role: 'img' });
}

export const ArrowLeft: React.FC<P> = (p) => (
  <svg
    width={p.size ?? 16}
    height={p.size ?? 16}
    viewBox="0 0 24 24"
    fill="none"
    stroke={p.color ?? 'currentColor'}
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M19 12H5M12 19l-7-7 7-7" />
  </svg>
);
