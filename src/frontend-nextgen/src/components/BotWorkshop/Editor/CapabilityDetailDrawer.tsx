import { Button } from '@/components/ui/Button';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import { useBotCapabilityDetail } from '@/hooks/useBotCapabilityDetail';
import type { CapabilityDetailTarget } from '@/services/botWorkshop/botCapabilityDetailService';
import { FileText } from 'lucide-react';
import MarkdownIt from 'markdown-it';
import { useMemo } from 'react';
import { SkillParameters } from './SkillParameters';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true });

/**
 * 剥离 SKILL.md 顶部 YAML frontmatter（--- … ---）。直接喂 markdown-it 时收尾的 ---
 * 会把整块 frontmatter 变成 setext 标题（整段加粗 + 顶部横线）；旧详情样式将其剥掉，
 * 仅把 name/description 展示为 Name/Description 表（name 为 code chip）。
 */
export function splitFrontmatter(content: string): {
  meta: { name: string; description: string } | null;
  body: string;
} {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(content);
  if (!m) return { meta: null, body: content };
  const meta = { name: '', description: '' };
  let lastKey: 'name' | 'description' | null = null;
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^(name|description):\s*(.*)$/.exec(line);
    if (kv) {
      lastKey = kv[1] as 'name' | 'description';
      meta[lastKey] = kv[2].trim();
    } else if (lastKey && /^\s+\S/.test(line) && !/^\s*-\s/.test(line)) {
      // YAML 折行续行（缩进行）并入上一个 name/description 值；列表项（tags 等）不并入
      meta[lastKey] = `${meta[lastKey]} ${line.trim()}`;
    } else {
      lastKey = null; // 其它 key（argument-hint/tags/适用…）不进表
    }
  }
  return { meta, body: content.slice(m[0].length) };
}

/** markdown 表格单元格转义：竖线/换行会破坏表格结构 */
function cell(text: string): string {
  return text.replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
}

export function CapabilityDetailDrawer({
  botId,
  ownerId,
  target,
  onClose,
  editable = false,
}: {
  botId?: string;
  ownerId?: string;
  target?: CapabilityDetailTarget;
  onClose: () => void;
  editable?: boolean;
}) {
  const { detail, loading, error, retry } = useBotCapabilityDetail(botId, target, ownerId);
  // 技能文档 = frontmatter 的 Name/Description 表（旧详情样式）+ 剥离 frontmatter 后的正文
  const docHtml = useMemo(() => {
    if (!detail?.content) return '';
    const { meta, body } = splitFrontmatter(detail.content);
    const name = meta?.name || detail.name || '';
    const description = meta?.description || detail.description || '暂无描述';
    const table = `| Name | Description |\n| --- | --- |\n| \`${cell(name)}\` | ${cell(description)} |`;
    return markdown.render(`${table}\n\n${body}`);
  }, [detail?.content, detail?.name, detail?.description]);
  return (
    <Drawer
      open={Boolean(target)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent size="full" className="max-w-full">
        <DrawerHeader>
          <DrawerTitle className="text-lg font-semibold">{target?.kind === 'mcp' ? 'MCP' : 'Skill'} 详情</DrawerTitle>
        </DrawerHeader>
        {loading ? (
          <Spin tip="加载能力详情…" />
        ) : error ? (
          <Empty
            title="详情加载失败"
            description={error}
            action={
              <Button variant="outline" onClick={retry}>
                重试
              </Button>
            }
          />
        ) : detail ? (
          <div className="space-y-6">
            {/* 头部：图标块 + 名称 */}
            <div className="flex items-center gap-4">
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-primary/10">
                <FileText className="size-6 text-primary" aria-hidden />
              </div>
              <h2 className="m-0 min-w-0 flex-1 truncate text-base font-semibold text-foreground">
                {detail.name || target?.name}
              </h2>
            </div>

            <section className="space-y-2">
              <h3 className="m-0 text-sm text-muted-foreground">描述</h3>
              <p className="m-0 text-sm leading-6 text-foreground">{detail.description || '暂无描述'}</p>
            </section>

            <section className="space-y-3">
              <h3 className="m-0 text-sm text-muted-foreground">{target?.kind === 'mcp' ? 'MCP 文档' : '技能文档'}</h3>
              {detail.content ? (
                <div
                  className="markdown-body skill-doc break-words text-sm leading-6 [&_pre]:overflow-x-auto"
                  dangerouslySetInnerHTML={{ __html: docHtml }}
                />
              ) : (
                <Empty compact title="暂无说明文档" />
              )}
            </section>
            {target?.kind === 'skill' && botId && detail.content ? (
              <SkillParameters
                botId={botId}
                skillId={target.id}
                ownerId={ownerId}
                content={detail.content}
                editable={editable}
              />
            ) : null}
            {target?.kind === 'mcp' ? (
              <section className="space-y-3">
                <h3 className="text-sm font-semibold">工具（{detail.tools.length}）</h3>
                {detail.tools.map((tool, index) => (
                  <details key={`${tool.name}:${index}`} className="rounded-lg border border-border p-3">
                    <summary className="text-sm font-medium">{tool.name || '未命名工具'}</summary>
                    <p className="whitespace-pre-wrap text-xs text-muted-foreground">
                      {tool.description || '暂无描述'}
                    </p>
                    {tool.parameters ? (
                      <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">{tool.parameters}</pre>
                    ) : null}
                  </details>
                ))}
              </section>
            ) : null}
          </div>
        ) : null}
      </DrawerContent>
    </Drawer>
  );
}
