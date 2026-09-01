// @asset-migrated: teamclaw 自研资产
/** 轻量 markdown 渲染单元格：输出摘要、根节点产物等长文本按 markdown 格式渲染。
 *  内容较多时默认折叠（max-height 截断 + 渐变遮罩），点击「展开全部」查看全文，避免淹没其它字段。 */
import MarkdownIt from 'markdown-it';
import React, { useState } from 'react';
import { C } from './tokens';

const md = new MarkdownIt({ html: false, breaks: true, linkify: true });

const COLLAPSED_HEIGHT = 120;
const EXPANDED_MAX_HEIGHT = 320;

export const MarkdownCell: React.FC<{ content: string | null | undefined }> = ({ content }) => {
  const [expanded, setExpanded] = useState(false);
  if (!content || !content.trim()) {
    return <span style={{ color: C.textMuted }}>—</span>;
  }
  const html = md.render(content);
  // 折叠态：固定高度 + 底部渐变遮罩；展开态：限高滚动显示全文。
  const collapsed = !expanded;
  return (
    <div style={{ marginTop: 5, position: 'relative' }}>
      <div
        style={{
          color: C.textPrimary,
          fontSize: 12,
          lineHeight: 1.6,
          wordBreak: 'break-word',
          maxHeight: collapsed ? COLLAPSED_HEIGHT : EXPANDED_MAX_HEIGHT,
          overflow: 'auto',
          position: 'relative',
        }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {collapsed && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            bottom: 0,
            height: 40,
            border: 0,
            background: `linear-gradient(to bottom, transparent, ${C.surfaceRaised})`,
            color: C.primary,
            fontSize: 11,
            fontWeight: 600,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'center',
            paddingBottom: 2,
          }}
        >
          展开全部 ▾
        </button>
      )}
      {!collapsed && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          style={{
            marginTop: 6,
            border: 0,
            background: 'transparent',
            color: C.primary,
            fontSize: 11,
            fontWeight: 600,
            cursor: 'pointer',
            padding: 0,
          }}
        >
          收起 ▴
        </button>
      )}
    </div>
  );
};
