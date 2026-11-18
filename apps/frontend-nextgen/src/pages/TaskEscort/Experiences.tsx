import { useDetailFocusRestore } from '@/hooks/taskEscortWorkspace/useDetailFocus';
import { useExperiencePresentation } from '@/hooks/taskEscortWorkspace/useExperiencePresentation';
import { ArrowRight, CheckCircle2, Filter, Lightbulb, SearchX, ShieldCheck } from 'lucide-react';
import { ExperienceCard } from './Experiences.cards';
import type { ViewProps } from './types';
import {
  Badge,
  Button,
  Empty,
  Modal,
  ModalContent,
  ModalDescription,
  ModalHeader,
  ModalTitle,
  Panel,
  SearchField,
  SelectField,
} from './ui';

export function Experiences({ demo }: ViewProps) {
  const focusRestore = useDetailFocusRestore();
  const { category, setCategory, selected, setSelectedId, categories, experiences, summary, clear, reference } =
    useExperiencePresentation(demo);
  return (
    <div className="space-y-6" data-testid="experiences-view" data-detail-focus-fallback tabIndex={-1}>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">经验库</h2>
          <p className="mt-2 text-sm text-muted-foreground">沉淀已验证的解决路径，供下次人工参考，而非自动套用。</p>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <ShieldCheck className="size-3.5 text-success" />
          仅展示已人工整理的经验
        </div>
      </header>
      <div className="grid gap-4 sm:grid-cols-3">
        <section className="panel">
          <p className="text-xs text-muted-foreground">已验证经验</p>
          <p className="mt-3 stat-value">
            {summary.verifiedCount}
            <span className="ml-2 text-xs font-normal text-muted-foreground">条</span>
          </p>
        </section>
        <section className="panel">
          <p className="text-xs text-muted-foreground">经验复用次数</p>
          <p className="mt-3 stat-value">
            {summary.reuseCount}
            <span className="ml-2 text-xs font-normal text-muted-foreground">次</span>
          </p>
        </section>
        <section className="panel">
          <p className="text-xs text-muted-foreground">覆盖分类</p>
          <p className="mt-3 stat-value">
            {categories.length}
            <span className="ml-2 text-xs font-normal text-muted-foreground">类</span>
          </p>
        </section>
      </div>
      <Panel
        title="可复用经验"
        description="浏览经过人工确认的解决方案，打开详情查看原始异常与适用范围"
        action={
          <Badge tone="outline" className="gap-1 font-normal">
            <Filter className="size-3" />
            人工精选
          </Badge>
        }
      >
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <div className="min-w-48 flex-1">
            <SearchField value={demo.query} onChange={demo.setQuery} placeholder="搜索经验标题、分类或工作流…" />
          </div>
          <SelectField
            label="经验分类筛选"
            value={category}
            onChange={setCategory}
            options={[{ value: 'all', label: '全部分类' }, ...categories.map((item) => ({ value: item, label: item }))]}
          />
          {(demo.query || category !== 'all') && (
            <Button variant="ghost" size="sm" onClick={clear}>
              清除筛选
            </Button>
          )}
        </div>
        <p className="mb-4 text-xs text-muted-foreground" role="status" aria-live="polite">
          共 {experiences.length} 条经验
          {demo.query && `，关键词「${demo.query}」`}
        </p>
        {experiences.length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {experiences.map((experience) => (
              <ExperienceCard key={experience.id} experience={experience} onOpen={() => setSelectedId(experience.id)} />
            ))}
          </div>
        ) : (
          <div role="status">
            <Empty
              title="没有匹配的经验"
              description="调整关键词或分类后重试。"
              icon={<SearchX className="size-5" />}
              action={
                <Button variant="outline" size="sm" onClick={clear}>
                  清除筛选
                </Button>
              }
            />
          </div>
        )}
        <div className="mt-5 flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-3 text-xs leading-6 text-muted-foreground">
          <Lightbulb className="mt-1 size-3.5 shrink-0 text-primary" />
          <p>经验只会在异常完成“人工验证”后，由你显式保存；从这里点击“参考这条经验”不会自动应用到任何工作流。</p>
        </div>
      </Panel>
      <Modal
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
      >
        <ModalContent {...focusRestore} data-detail-focus-fallback className="w-[calc(100%-2rem)] max-w-xl">
          <ModalHeader className="text-left">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Badge tone="outline" className="font-normal">
                {selected?.category}
              </Badge>
              {selected?.verified && (
                <Badge tone="success" className="gap-1 font-normal">
                  <CheckCircle2 className="size-3" />
                  已验证
                </Badge>
              )}
            </div>
            <ModalTitle className="text-xl">{selected?.title}</ModalTitle>
            <ModalDescription>{selected?.description}</ModalDescription>
          </ModalHeader>
          {selected && (
            <div className="space-y-4">
              <dl className="grid grid-cols-2 gap-4 rounded-lg border border-border bg-muted/20 p-4">
                <div>
                  <dt className="text-[11px] text-muted-foreground">来源工作流</dt>
                  <dd className="mt-1 text-xs font-medium">{selected.workflowName}</dd>
                </div>
                <div>
                  <dt className="text-[11px] text-muted-foreground">历史复用</dt>
                  <dd className="mt-1 text-xs font-medium">{selected.reuseCount} 次</dd>
                </div>
                <div>
                  <dt className="text-[11px] text-muted-foreground">来源异常</dt>
                  <dd className="mono mt-1 text-xs font-medium">{selected.sourceIssueId}</dd>
                </div>
                <div>
                  <dt className="text-[11px] text-muted-foreground">状态</dt>
                  <dd className="mt-1 text-xs font-medium">仅供参考 · 需人工审阅</dd>
                </div>
              </dl>
              <div className="flex items-start gap-2 rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs leading-6 text-muted-foreground">
                <ShieldCheck className="mt-1 size-3.5 shrink-0 text-primary" />
                这条经验已被人工验证，但每次使用前仍需结合目标工作流重新审阅。演示不会自动调用或应用。
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setSelectedId(null)}>
                  关闭
                </Button>
                <Button onClick={reference}>
                  参考这条经验
                  <ArrowRight className="ml-2 size-3.5" />
                </Button>
              </div>
            </div>
          )}
        </ModalContent>
      </Modal>
    </div>
  );
}
