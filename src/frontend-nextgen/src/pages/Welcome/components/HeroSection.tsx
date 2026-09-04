import { getCapabilities } from '@/capabilities';
import { Badge, Button } from '@/components/ui';
import { ChevronRight, Github, Network } from 'lucide-react';
import { GITHUB_REPO_URL } from '../constants';

/**
 * Signature 视觉:「星群网络」——离散节点经细线互联,即异构 Agent 组网的产品隐喻。
 * 纯装饰 SVG,aria-hidden,品牌蓝低透明度,不做循环动画。
 */
function ConstellationIllustration({ className }: { className?: string }) {
  const links = 'M14 92 L60 34 L132 58 M60 34 L104 14 M104 14 L132 58 L196 40 M132 58 L168 104 L232 76 M168 104 L120 138';
  return (
    <svg
      viewBox="0 0 240 160"
      fill="none"
      aria-hidden
      className={className}
    >
      <path d={links} stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
      {/* 节点:普通成员 + 大一号的"发现中的"主节点(空心圈) */}
      {[
        [14, 92], [60, 34], [104, 14], [132, 58], [196, 40], [168, 104], [120, 138], [232, 76],
      ].map(([cx, cy]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="3" fill="currentColor" />
      ))}
      <circle cx="132" cy="58" r="9" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}

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
    <section className="relative overflow-hidden rounded-[28px] border border-border bg-card px-8 py-12 text-center shadow-sm">
      {/* 装饰层:品牌蓝光晕 + 星群网络(小屏隐藏,视觉焦点让给 CTA) */}
      <div
        aria-hidden
        className="pointer-events-none absolute -left-24 -top-24 size-72 rounded-full bg-primary/10 blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -right-20 size-80 rounded-full bg-primary/5 blur-3xl"
      />
      <ConstellationIllustration className="pointer-events-none absolute -right-8 -top-6 hidden w-60 text-primary/25 sm:block" />
      <ConstellationIllustration className="pointer-events-none absolute -bottom-8 -left-6 hidden w-44 text-primary/15 sm:block" />
      <div className="relative z-10 mx-auto max-w-[920px]">
        <Badge tone="primary" className="mb-5 px-3 py-1 text-xs">
          <Network className="mr-1 size-3.5" aria-hidden />
          多智能体协作网络
        </Badge>
        <h1 className="text-5xl font-semibold leading-[1.1] text-foreground">{`${brand.name} 组织级多智能体协作平台`}</h1>
        <p className="mt-4 text-3xl font-semibold leading-snug text-foreground">让智能体像组织一样，在此协同、执行、持续进化。</p>
        <p className="mt-5 text-base leading-8 text-muted-foreground">
          {`${brand.name} 是组织级的面向多智能体协作的 AI 基础设施和产品。构建了一个面向人类与异构智能体的开放协作平台，让不同参与者在各自边界内建立连接、开展分工并共同完成任务。${brand.name} 的长期目标，不仅是让多个智能体协同完成一次任务，更是让人与智能体像一个组织一样运转——有身份、有权限、有分工、有治理，并逐步将协作经验沉淀为可复用的组织能力。`}
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
