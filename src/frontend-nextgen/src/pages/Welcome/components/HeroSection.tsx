import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui';
import { ChevronRight, Github } from 'lucide-react';
import { GITHUB_REPO_URL } from '../constants';

interface HeroSectionProps {
  /** CTA「进入产品」回调(页面层 push /workspace,登录态交给既有登录链路)。 */
  onEnter: () => void;
}

/**
 * 欢迎页 Hero:大标题(产品名)+ tagline + 介绍段落 + CTA / GitHub 外链。
 * 产品名与品牌视觉一律经 getProductBrand 插值(design 决策 5);视觉走设计系统 token,
 * CTA `bg-primary` 为品牌蓝(#165dff),不照搬参考稿靛蓝裸色(见 add-avernet-open-core-differentiation)。
 */
export function HeroSection({ onEnter }: HeroSectionProps) {
  const brand = getCapabilities().getProductBrand().value;
  return (
    <section className="rounded-[28px] border border-border bg-card px-8 py-12 text-center shadow-sm">
      <div className="mx-auto max-w-[920px]">
        <h1 className="text-5xl font-semibold leading-[1.1] text-foreground">{brand.name}</h1>
        <p className="mt-4 text-3xl font-semibold leading-snug text-foreground">让智能体在此协同、执行、进化。</p>
        <p className="mt-5 text-base leading-8 text-muted-foreground">
          {`${brand.name} 是面向多智能体协作的 AI 基础设施和产品:提供异构 Agent 统一接入和运行、Bot as a Service、异构 Agent 集群统一接入和管理、Agent 融合决策等能力。可以让异构 Agent 连接到同一个网络,像团队成员一样被发现、参与讨论、对齐目标、分工执行、共同进化。`}
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Button size="lg" onClick={onEnter} rightIcon={<ChevronRight className="h-4 w-4" aria-hidden />}>
            进入 {brand.name}
          </Button>
          <Button size="lg" variant="outline" asChild>
            <a href={GITHUB_REPO_URL} target="_blank" rel="noreferrer">
              <Github className="h-4 w-4" aria-hidden />
              GitHub
            </a>
          </Button>
        </div>
      </div>
    </section>
  );
}
