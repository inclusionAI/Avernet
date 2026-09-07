// 工单详情 JSON 负载展示块：纯展示组件，不消费 Service/Store、不弹 toast（复制反馈用图标切换）。
// - 着色只走语义 token：键=primary / 字符串=success / 数字=warning / null·bool=destructive，标点回落 text-muted-foreground 基调；
// - 长文（> JSON_COLLAPSED_LINES 行）默认折叠：底部渐隐遮罩 + 头部「展开」；展开后限高滚动，可「收起」。
// 外部用 key=itemId 挂载，切换工单时自动重置折叠态。
import { IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Check, ChevronDown, ChevronUp, Copy } from 'lucide-react';
import { Fragment, useMemo, useState } from 'react';

/** 折叠阈值（行数）：超过则默认折叠并提供展开/收起 */
const JSON_COLLAPSED_LINES = 12;

/** 键 / 值 token 语义色（对 textContent 无副作用，纯 span 包裹） */
const TOKEN_CLS = {
  key: 'text-primary font-medium',
  string: 'text-success',
  number: 'text-warning',
  keyword: 'text-destructive',
} as const;

interface JsonToken {
  text: string;
  cls?: string;
}

// 文本级 tokenizer：字符串（含转义）整体命中，命中后跟冒号视为键；溢出片段走 gap。
// 只做切片不重排，textContent 与原文逐字节一致（含换行与空格），供既有测试 textContent 断言复用。
const JSON_TOKEN_RE = /("(?:\\.|[^"\\])*")(\s*:)?|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;

function tokenizeJson(raw: string): JsonToken[] {
  const tokens: JsonToken[] = [];
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = JSON_TOKEN_RE.exec(raw)) !== null) {
    if (m.index > lastIndex) {
      tokens.push({ text: raw.slice(lastIndex, m.index) });
    }
    if (m[1] !== undefined) {
      if (m[2] !== undefined) {
        tokens.push({ text: m[1], cls: TOKEN_CLS.key });
        tokens.push({ text: m[2] }); // 冒号（含缩进空格）保持基调色
      } else {
        tokens.push({ text: m[1], cls: TOKEN_CLS.string });
      }
    } else if (m[0] === 'true' || m[0] === 'false' || m[0] === 'null') {
      tokens.push({ text: m[0], cls: TOKEN_CLS.keyword });
    } else {
      tokens.push({ text: m[0], cls: TOKEN_CLS.number });
    }
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < raw.length) {
    tokens.push({ text: raw.slice(lastIndex) });
  }
  return tokens;
}

export interface JsonBlockProps {
  /** pretty JSON 文本（WorkOrder.contentRaw，mapper 已 JSON.stringify(obj, null, 2)） */
  raw: string;
}

export function JsonBlock({ raw }: JsonBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const tokens = useMemo(() => tokenizeJson(raw), [raw]);
  const lineCount = useMemo(() => raw.split('\n').length, [raw]);
  const collapsible = lineCount > JSON_COLLAPSED_LINES;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(raw);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // 复制失败不打断查看流程（无剪贴板权限/非安全上下文）
    }
  };

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border bg-muted/40 py-0.5 pl-3 pr-1">
        <span className="text-xs font-medium text-muted-foreground">JSON</span>
        <div className="flex items-center">
          <IconButton
            label="复制 JSON"
            size="sm"
            onClick={handleCopy}
            icon={
              copied ? (
                <Check aria-hidden className="size-3.5 text-success" />
              ) : (
                <Copy aria-hidden className="size-3.5" />
              )
            }
          />
          {collapsible && (
            <IconButton
              label={expanded ? '收起' : '展开'}
              size="sm"
              onClick={() => setExpanded((v) => !v)}
              icon={
                expanded ? (
                  <ChevronUp aria-hidden className="size-3.5" />
                ) : (
                  <ChevronDown aria-hidden className="size-3.5" />
                )
              }
            />
          )}
        </div>
      </div>
      <div className="relative">
        <pre
          className={cn(
            'app-scrollbar m-0 whitespace-pre-wrap break-all px-4 py-3 font-mono text-xs leading-5 text-muted-foreground',
            expanded ? 'max-h-96 overflow-y-auto' : collapsible ? 'max-h-56 overflow-hidden' : undefined,
          )}
        >
          {tokens.map((token, i) =>
            token.cls ? (
              <span key={i} className={token.cls}>
                {token.text}
              </span>
            ) : (
              <Fragment key={i}>{token.text}</Fragment>
            ),
          )}
        </pre>
        {collapsible && !expanded && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-card to-transparent" />
        )}
      </div>
    </div>
  );
}

export default JsonBlock;
