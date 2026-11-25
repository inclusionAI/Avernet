// @asset-migrated: teamclaw 自研资产
/** 通用样式块：Empty / StatusTag / LabelValue / SectionCard / Segmented（antd 等效重绘，纯 styled-components）。 */
import React from 'react';
import { createGlobalStyle } from 'styled-components';
import { C, TASK_STATUS_TONES } from './tokens';

export const GlobalKeyframes = createGlobalStyle`
  @keyframes task-panel-spin {
    to {
      transform: rotate(360deg);
    }
  }
  @keyframes task-panel-dag-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.7; }
  }
  @keyframes task-panel-slide-in {
    from { transform: translateX(100%); }
    to { transform: translateX(0); }
  }
  @keyframes task-panel-rail-in {
    from { opacity: 0; transform: translateX(-12px); }
    to { opacity: 1; transform: translateX(0); }
  }
`;

export const Empty: React.FC<{ description: string; minHeight?: number }> = ({ description, minHeight = 200 }) => (
  <div
    style={{
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight,
      color: C.textSecondary,
      fontSize: 13,
      gap: 8,
      padding: 24,
    }}
  >
    <div
      style={{
        width: 48,
        height: 48,
        borderRadius: '50%',
        background: C.surfaceAlt,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 22,
        color: C.textMuted,
      }}
    >
      ∅
    </div>
    <div>{description}</div>
  </div>
);

export const StatusTag: React.FC<{ status: string }> = ({ status }) => {
  const tone = TASK_STATUS_TONES[status] ?? TASK_STATUS_TONES.DRAFTING;
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        fontSize: 12,
        fontWeight: 500,
        padding: '2px 8px',
        borderRadius: 4,
        color: tone.color,
        background: tone.bg,
        border: `1px solid ${tone.color}20`,
        whiteSpace: 'nowrap',
      }}
    >
      {tone.label}
    </span>
  );
};

export const Label: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 12, fontWeight: 600, color: C.textSecondary, marginBottom: 4 }}>{children}</div>
);
export const Value: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 14, color: C.textPrimary, lineHeight: 1.6, wordBreak: 'break-all' }}>{children}</div>
);

export const LabelValue: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => (
  <div style={{ marginBottom: 12 }}>
    <Label>{label}</Label>
    <Value>{value === null || value === '' ? <span style={{ color: C.textMuted }}>—</span> : value}</Value>
  </div>
);

export const SectionCard: React.FC<{ title: string; children: React.ReactNode; marginTop?: number }> = ({
  title,
  children,
  marginTop = 0,
}) => (
  <div
    style={{
      marginTop,
      padding: '12px 0',
      borderTop: marginTop ? `1px solid ${C.border}` : 'none',
    }}
  >
    <Label>{title}</Label>
    <div>{children}</div>
  </div>
);

export const Segmented: React.FC<{
  value: string;
  onChange: (v: string) => void;
  options: { label: string; value: string }[];
}> = ({ value, onChange, options }) => (
  <div
    style={{
      display: 'inline-flex',
      background: C.surfaceAlt,
      borderRadius: 6,
      padding: 2,
      gap: 2,
    }}
  >
    {options.map((o) => {
      const active = o.value === value;
      return (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          type="button"
          style={{
            border: 'none',
            cursor: 'pointer',
            padding: '4px 14px',
            fontSize: 12,
            borderRadius: 4,
            fontWeight: active ? 600 : 400,
            color: active ? C.primary : C.textSecondary,
            background: active ? C.surface : 'transparent',
            boxShadow: active ? '0 1px 3px rgba(0,0,0,0.08)' : 'none',
            transition: 'all 0.15s',
          }}
        >
          {o.label}
        </button>
      );
    })}
  </div>
);
