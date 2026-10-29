import { sanitizeMarkdownHtml } from '@/assets/TaskPanel/sanitizeHtml';
import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import { Button } from '@/components/ui/Button';
import { useExpertDetails } from '@/hooks/useExpertDetails';
import { initSpaceContext, useSpaceContext } from '@/hooks/useSpaceContext';
import MarkdownIt from 'markdown-it';
import { useEffect, useState } from 'react';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true });

export function ExpertDetails({ expertId, onBack }: { expertId: string; onBack: () => void }) {
  const [creating, setCreating] = useState(false);
  const spaces = useSpaceContext((state) => state.spaces);
  useEffect(() => {
    void initSpaceContext();
  }, []);
  const { expert, error, loading, reload } = useExpertDetails(expertId);

  return (
    <div className="space-y-5">
      <Button variant="ghost" onClick={onBack}>
        返回专家列表
      </Button>
      {loading ? (
        <p role="status">正在加载专家详情…</p>
      ) : error ? (
        <div role="alert" className="rounded-xl border border-input p-6">
          <p className="mb-3 text-destructive">专家详情加载失败：{error}</p>
          <Button variant="outline" onClick={reload}>
            重试详情
          </Button>
        </div>
      ) : (
        expert && (
          <>
            <header className="rounded-xl border border-input bg-card p-6">
              <span className="text-sm text-primary">{expert.category}</span>
              <h1 className="mt-2 text-2xl font-semibold text-foreground">{expert.name}</h1>
              <p className="mt-3 whitespace-pre-wrap break-words text-muted-foreground">{expert.description}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {expert.tags.map((tag) => (
                  <span key={tag} className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                    {tag}
                  </span>
                ))}
              </div>
              <p className="mt-4 text-sm text-muted-foreground">已创建 {expert.creation_count} 个 Bot</p>
              <Button className="mt-4" onClick={() => setCreating(true)}>
                创建我的 Bot
              </Button>
            </header>
            {expert.identity.length > 0 && (
              <section className="space-y-4" aria-label="专家身份介绍">
                <h2 className="text-lg font-semibold text-foreground">身份介绍</h2>
                {expert.identity.map((identity, index) => (
                  <article key={`${identity.type}-${index}`} className="rounded-xl border border-input bg-card p-5">
                    <h3 className="mb-3 text-sm font-medium text-muted-foreground">{identity.type}</h3>
                    <div
                      className="prose max-w-none break-words text-foreground"
                      dangerouslySetInnerHTML={{ __html: sanitizeMarkdownHtml(markdown.render(identity.content)) }}
                    />
                  </article>
                ))}
              </section>
            )}
            {expert.skills.length > 0 && (
              <section aria-label="专家技能" className="space-y-3">
                <h2 className="text-lg font-semibold text-foreground">专业技能</h2>
                {expert.skills.map((skill) => (
                  <article key={skill.name} className="rounded-xl border border-input bg-card p-5">
                    <h3 className="font-medium text-foreground">{skill.name}</h3>
                    <span className="text-xs text-muted-foreground">Local Skill</span>
                    <p className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">{skill.description}</p>
                  </article>
                ))}
              </section>
            )}
            {(expert.mcp ?? []).length > 0 && (
              <section aria-label="模板声明的 MCP" className="space-y-3">
                <h2 className="text-lg font-semibold text-foreground">模板声明的 MCP</h2>
                <p className="text-sm text-muted-foreground">创建时沿现有权限与用户授权处理，实际配置可在 Bot 管理中查看。</p>
                {expert.mcp?.map((mcp) => (
                  <article key={mcp.server_code} className="rounded-xl border border-input bg-card p-5">
                    <h3 className="font-medium text-foreground">{mcp.server_code}</h3>
                  </article>
                ))}
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
