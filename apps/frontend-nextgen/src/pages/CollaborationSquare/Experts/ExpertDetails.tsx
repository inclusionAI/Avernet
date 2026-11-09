import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { useExpertDetails } from '@/hooks/useExpertDetails';
import { initSpaceContext, useSpaceContext } from '@/hooks/useSpaceContext';
import { sanitizeMarkdownHtml } from '@/shared/html/sanitizeHtml';
import { ArrowLeft, Bot, FileText, Plug, RefreshCw } from 'lucide-react';
import MarkdownIt from 'markdown-it';
import { useEffect, useState } from 'react';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true });

/** 能力卡栅格：与专家市场目录同节奏，间距收紧到 10px 保持详情页紧凑。 */
const SKILL_GRID = 'grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3';
/** 能力卡：与专家卡同描边规格，p-3 紧凑内边。 */
const SKILL_CARD = 'flex h-full min-w-0 flex-col rounded-2xl border border-divider bg-card p-3';
/** 声明 chip：模板声明的 MCP / 资源是短字符串，走流式 chip 不占整卡。 */
const DECLARATION_CHIP =
  'inline-flex min-w-0 items-center gap-1.5 rounded-lg border border-divider bg-card px-2.5 py-1.5 font-mono text-xs text-foreground';

/**
 * 紧凑 Markdown 文档样式（仓库无 typography 插件，`prose` 不生效）：
 * 沿用 EvolutionFactory/StepOutput 的 arbitrary-variants 方案，标题收紧、正文 13px，
 * 长文档区限高滚动，详情页不被单个身份文档撑开。
 */
const DOC_MARKDOWN_STYLES = [
  'break-words text-[13px] leading-6 text-foreground',
  '[&_a]:text-primary [&_a]:underline',
  '[&_blockquote]:border-l-2 [&_blockquote]:border-divider [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
  '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-xs',
  '[&_h1]:mb-2 [&_h1]:mt-1 [&_h1]:text-base [&_h1]:font-semibold',
  '[&_h2]:mb-2 [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold',
  '[&_h3]:mb-1.5 [&_h3]:mt-2.5 [&_h3]:text-[13px] [&_h3]:font-semibold',
  '[&_hr]:my-3 [&_hr]:border-divider',
  '[&_li]:my-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5',
  '[&_p]:mb-2 [&_p]:mt-0',
  '[&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-3 [&_pre]:text-xs',
].join(' ');

/** 文档阅读区的限高：超出在卡内滚动，保持页面紧凑；左右边距与头部卡 p-5 同节奏；滚动条走 app-scrollbar 细样式。 */
const DOC_BODY_SCROLL = 'app-scrollbar max-h-96 overflow-y-auto px-5 py-4';

/**
 * 详情加载骨架：逐区镜像真实详情——头部卡（名称 + 胶囊、描述、标签、border-t 底行）
 * + 一张带文件头的身份文档卡，loading→loaded 零跳版。
 */
function ExpertDetailsSkeleton() {
  return (
    <>
      {/* 读屏通道：骨架无文字，用 sr-only status 告知加载中（与目录加载态同约定）。 */}
      <p role="status" className="m-0 sr-only">
        正在加载专家详情…
      </p>
      <div className="rounded-2xl border border-divider bg-card p-5 pb-4">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <Skeleton.Line className="h-7 min-w-0 flex-1" />
          <Skeleton.Block className="h-5 w-[65px] shrink-0 rounded-full" />
        </div>
        <div className="mt-2.5 space-y-1.5">
          <Skeleton.Line className="h-[18px]" />
          <Skeleton.Line className="h-[18px] w-3/4" />
        </div>
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <Skeleton.Block className="h-6 w-16 rounded-md" />
          <Skeleton.Block className="h-6 w-12 rounded-md" />
          <Skeleton.Block className="h-6 w-14 rounded-md" />
        </div>
        <div className="mt-4 flex items-center justify-between border-t border-divider pt-3">
          <Skeleton.Block className="h-4 w-28 rounded-full" />
          <Skeleton.Block className="h-8 w-[96px] rounded-md" />
        </div>
      </div>
      <div className="overflow-hidden rounded-2xl border border-divider bg-card">
        <div className="flex items-center gap-2 border-b border-divider bg-muted/50 px-5 py-2">
          <Skeleton.Block className="h-4 w-4 rounded-md" />
          <Skeleton.Line className="h-4 w-24" />
        </div>
        <div className="space-y-2 px-5 py-4">
          <Skeleton.Line className="h-5 w-32" />
          <Skeleton.Line />
          <Skeleton.Line className="w-3/4" />
          <Skeleton.Line className="h-[18px] w-1/2" />
        </div>
      </div>
    </>
  );
}

/**
 * 专家详情页：与目录同一套卡片语言的紧凑版——头部卡是「专家卡展开态」，
 * 槽位与 ExpertCard 一一对应（名称行 + 分类胶囊、描述、标签 chip、border-t 底行计数 chip），
 * 主操作从「查看详情」就位为「创建我的 Bot」。身份介绍以「文件查看器」呈现
 * （文件头 + 限高滚动的紧凑 Markdown），三类模板声明（技能/MCP/资源）收紧为
 * 小卡与流式 chip，避免整页被长内容摊薄。
 */
export function ExpertDetails({ expertId, onBack }: { expertId: string; onBack: () => void }) {
  const [creating, setCreating] = useState(false);
  const spaces = useSpaceContext((state) => state.spaces);
  useEffect(() => {
    void initSpaceContext();
  }, []);
  const { expert, error, loading, reload } = useExpertDetails(expertId);

  return (
    <div className="space-y-4">
      <Button
        variant="ghost"
        size="sm"
        leftIcon={<ArrowLeft aria-hidden className="h-4 w-4" />}
        onClick={onBack}
      >
        返回专家列表
      </Button>
      {loading ? (
        <ExpertDetailsSkeleton />
      ) : error ? (
        <Card role="alert">
          <Empty
            title="专家详情加载失败"
            description={error}
            action={
              <Button onClick={reload} leftIcon={<RefreshCw aria-hidden className="h-4 w-4" />}>
                重新加载
              </Button>
            }
          />
        </Card>
      ) : (
        expert && (
          <>
            <header className="rounded-2xl border border-divider bg-card p-5 pb-4">
              {/* 头部行：与专家卡同槽位——专家名 + 右上分类胶囊。 */}
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-1">
                <h1 className="min-w-0 break-words text-xl font-semibold text-foreground">{expert.name}</h1>
                <Badge tone="primary" className="shrink-0">
                  {expert.category}
                </Badge>
              </div>
              {/* 描述：详情是读全文的地方，不截断。 */}
              <p className="m-0 mt-2 whitespace-pre-wrap break-words text-[13px] leading-6 text-muted-foreground">
                {expert.description}
              </p>
              {expert.tags.length > 0 && (
                <div className="mt-2.5 flex min-w-0 flex-wrap gap-1.5">
                  {expert.tags.map((tag) => (
                    <span key={tag} className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                      {tag}
                    </span>
                  ))}
                </div>
              )}
              {/* 底部行：与专家卡同槽位——分割线上方，左「已创建 N 个 Bot」计数 chip，右主操作。 */}
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-divider pt-3">
                <span className="flex min-w-0 items-center gap-1 text-[13px] text-muted-foreground">
                  <Bot aria-hidden className="h-4 w-4 shrink-0" />
                  <span>已创建 {expert.creation_count} 个 Bot</span>
                </span>
                <Button className="shrink-0" onClick={() => setCreating(true)}>
                  创建我的 Bot
                </Button>
              </div>
            </header>
            {expert.identity.length > 0 && (
              <section className="space-y-2.5" aria-label="专家身份介绍">
                <h2 className="text-lg font-semibold text-foreground">身份介绍</h2>
                {expert.identity.map((identity, index) => (
                  <article key={`${identity.type}-${index}`} className="overflow-hidden rounded-2xl border border-divider bg-card">
                    {/* 文件头：图标 + 文档名，左右边距与正文统一（px-5）。 */}
                    <div className="flex items-center gap-2 border-b border-divider bg-muted/50 px-5 py-2">
                      <FileText aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <h3 className="font-mono text-xs font-medium text-muted-foreground">{identity.type}</h3>
                    </div>
                    <div className={DOC_BODY_SCROLL}>
                      <div
                        className={`max-w-none ${DOC_MARKDOWN_STYLES}`}
                        dangerouslySetInnerHTML={{ __html: sanitizeMarkdownHtml(markdown.render(identity.content)) }}
                      />
                    </div>
                  </article>
                ))}
              </section>
            )}
            {expert.skills.length > 0 && (
              <section aria-label="专家技能" className="space-y-2.5">
                <h2 className="text-lg font-semibold text-foreground">专业技能</h2>
                <div className={SKILL_GRID}>
                  {expert.skills.map((skill) => (
                    <article key={skill.name} className={SKILL_CARD}>
                      <div className="flex min-w-0 items-center justify-between gap-2">
                        <h3 className="min-w-0 break-all text-[13px] font-medium text-foreground">{skill.name}</h3>
                        <Badge className="shrink-0">Local Skill</Badge>
                      </div>
                      <p className="m-0 mt-1.5 whitespace-pre-wrap break-words text-xs leading-[18px] text-muted-foreground">
                        {skill.description}
                      </p>
                    </article>
                  ))}
                </div>
              </section>
            )}
            {(expert.mcp ?? []).length > 0 && (
              <section aria-label="模板声明的 MCP" className="space-y-2.5">
                <h2 className="text-lg font-semibold text-foreground">模板声明的 MCP</h2>
                <p className="m-0 text-sm text-muted-foreground">创建时沿现有权限与用户授权处理，实际配置可在 Bot 管理中查看。</p>
                <div className="flex flex-wrap gap-2">
                  {expert.mcp?.map((mcp) => (
                    <span key={mcp.server_code} className={DECLARATION_CHIP}>
                      <Plug aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      {mcp.server_code}
                    </span>
                  ))}
                </div>
              </section>
            )}
            {Array.isArray(expert.resources) && (
              <section aria-label="模板声明的资源" className="space-y-2.5">
                <h2 className="text-lg font-semibold text-foreground">模板声明的资源</h2>
                <p className="m-0 text-sm text-muted-foreground">
                  创建时先清理工作区非保护项，再写入声明的资源；普通用户输出也可能被清理。资源不自动进入上下文或知识库。
                  创建后可在 Bot 详情的资源管理中查看实际文件。
                </p>
                {expert.resources.length === 0 ? (
                  <p className="m-0 text-sm text-muted-foreground">模板声明清理资源，不写入新文件。</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {expert.resources.map((resource) => (
                      <span key={resource.path} className={`${DECLARATION_CHIP} break-all`}>
                        <FileText aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        {resource.path}
                      </span>
                    ))}
                  </div>
                )}
              </section>
            )}
            <CreateBotModal
              scenario={creating ? 'cloud' : undefined}
              expert={expert}
              spaces={spaces.map((space) => ({
                id: String(space.spaceId),
                name: space.spaceName,
                ownership: space.spaceType === 'PERSONAL' ? 'personal' : 'team',
                canCreate: true,
              }))}
              creating={false}
              onClose={() => {
                setCreating(false);
                reload();
              }}
              onSubmit={async () => {}}
            />
          </>
        )
      )}
    </div>
  );
}
