// @asset-migrated: teamclaw 自研资产
/** 轻量 markdown 渲染单元格：输出摘要、根节点产物等长文本按 markdown 格式渲染。
 *  内容较多时默认折叠（max-height 截断 + 渐变遮罩），点击「展开全部」查看全文，避免淹没其它字段。 */
import MarkdownIt from 'markdown-it';
import React, { useState } from 'react';
import styled from 'styled-components';
import { sanitizeMarkdownHtml } from './sanitizeHtml';
import { C } from './tokens';

const md = new MarkdownIt({ html: false, breaks: true, linkify: true });

const COLLAPSED_HEIGHT = 120;
const EXPANDED_MAX_HEIGHT = 320;

/** 产物/节点输出统一使用紧凑 Markdown View，保留标题、列表、分隔线等 Markdown 层级。 */
const MarkdownView = styled.div<{ $fontSize: number }>`
  color: ${C.textPrimary};
  font-size: ${(props) => props.$fontSize}px;
  line-height: 1.6;
  word-break: break-word;

  h1,
  h2,
  h3,
  h4,
  h5,
  h6 {
    color: ${C.textPrimary};
    font-weight: 650;
    line-height: 1.4;
    margin: 10px 0 5px;
  }

  h1 {
    font-size: ${(props) => props.$fontSize + 4}px;
    margin-top: 2px;
  }

  h2 {
    font-size: ${(props) => props.$fontSize + 2}px;
  }

  h3,
  h4,
  h5,
  h6 {
    font-size: ${(props) => props.$fontSize + 1}px;
  }

  p {
    margin: 5px 0;
  }

  ul,
  ol {
    margin: 5px 0;
    padding-left: 20px;
  }

  li {
    margin: 2px 0;
  }

  hr {
    border: 0;
    border-top: 1px solid ${C.border};
    margin: 10px 0;
  }

  strong {
    font-weight: 650;
  }

  code {
    border-radius: 3px;
    background: ${C.surfaceAlt};
    padding: 1px 3px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: ${(props) => Math.max(11, props.$fontSize - 1)}px;
  }

  pre {
    overflow-x: auto;
    border-radius: 5px;
    background: ${C.surfaceAlt};
    padding: 8px;
  }

  pre code {
    background: transparent;
    padding: 0;
  }

  blockquote {
    border-left: 3px solid ${C.primary};
    color: ${C.textSecondary};
    margin: 8px 0;
    padding-left: 10px;
  }
`;

export const MarkdownCell: React.FC<{ content: string | null | undefined; fontSize?: number }> = ({
  content,
  fontSize = 12,
}) => {
  const [expanded, setExpanded] = useState(false);
  if (!content || !content.trim()) {
    return <span style={{ color: C.textMuted }}>—</span>;
  }
  const html = sanitizeMarkdownHtml(md.render(content));
  // 折叠态：固定高度 + 底部渐变遮罩；展开态：限高滚动显示全文。
  const collapsed = !expanded;
  return (
    <div style={{ marginTop: 5, position: 'relative' }}>
      <MarkdownView
        $fontSize={fontSize}
        style={{
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
