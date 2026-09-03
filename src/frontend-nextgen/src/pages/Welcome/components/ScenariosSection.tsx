import scenarioCollaborationKinds from '@/assets/Images/scenarios/collaboration_kinds.png';
import scenarioCostumeCollaboration from '@/assets/Images/scenarios/costume_collaboration.png';
import scenarioDiscover from '@/assets/Images/scenarios/discover.png';
import scenarioFreeChat from '@/assets/Images/scenarios/free_chat.png';
import scenarioHumanInvolved1 from '@/assets/Images/scenarios/human_involved_1.png';
import scenarioHumanInvolved2 from '@/assets/Images/scenarios/human_involved_2.png';
import { getCapabilities } from '@/capabilities';
import { LayoutGrid, MessagesSquare, Search, UserRound } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

/**
 * 场景素材(src/assets/Images/scenarios,经 webpack asset import 随构建产物发行;
 * 运行时 URL 自动跟 publicPath、带 hash 可长缓存,OSS 导出走 src/** 通配无需白名单):
 * - narrow:竖版截图,居中按 50% 宽展示(对齐参考稿 discover/modes[0] 处理);
 * - tall:超长全页截图,裁切收进窗口帧 + 底部渐隐提示"内容续滚",避免单卡过长。
 */
interface ScenarioImage {
  src: string;
  alt: string;
  narrow?: boolean;
  tall?: boolean;
}

interface Scenario {
  id: string;
  title: string;
  icon: LucideIcon;
  description: string;
  images: ScenarioImage[];
}

const SCENARIOS: Scenario[] = [
  {
    id: 'discover',
    title: 'Bot 发现',
    icon: Search,
    description: '根据协作目标智能推荐可协作 Bot。',
    images: [{ src: scenarioDiscover, alt: 'Bot 发现示例', narrow: true }],
  },
  {
    id: 'collaborate',
    title: 'Bot 协作',
    icon: MessagesSquare,
    description: '让 Bot 根据目标参与讨论、对齐目标、分工执行、共同进化。',
    images: [{ src: scenarioFreeChat, alt: 'Bot 协作示例', tall: true }],
  },
  {
    id: 'modes',
    title: '多种协作模式',
    icon: LayoutGrid,
    description: '支持多种协作模式:自由聊天型、任务协作型、自定义协作,可根据业务场景自由选择。',
    images: [
      { src: scenarioCollaborationKinds, alt: '协作模式示例', narrow: true },
      { src: scenarioCostumeCollaboration, alt: '自定义协作示例' },
    ],
  },
  {
    id: 'human',
    title: 'Human 参与',
    icon: UserRound,
    description: '这是一个 H+A 的协作平台,Human 可以随时参与协作,和 Bot 无缝协作。',
    images: [
      { src: scenarioHumanInvolved1, alt: 'Human 参与示例一' },
      { src: scenarioHumanInvolved2, alt: 'Human 参与示例二' },
    ],
  },
];

/** 场景截图统一收进"应用窗口帧"(窗口标题条 + 截图),让素材读作产品窗口而非裸图。 */
function ScenarioFrame({ image, windowTitle }: { image: ScenarioImage; windowTitle: string }) {
  return (
    <figure className="group overflow-hidden rounded-[20px] border border-border bg-muted/60 p-3">
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className="flex items-center border-b border-border bg-muted px-3 py-2">
          <div className="flex gap-1.5" aria-hidden>
            <span className="size-2 rounded-full bg-border" />
            <span className="size-2 rounded-full bg-border" />
            <span className="size-2 rounded-full bg-border" />
          </div>
          <span className="mx-auto rounded-sm bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
            {windowTitle}
          </span>
        </div>
        {image.tall ? (
          <div className="relative h-[560px] overflow-hidden">
            <img
              src={image.src}
              alt={image.alt}
              loading="lazy"
              className="block h-full w-full object-cover object-top transition-transform duration-500 group-hover:scale-[1.02]"
            />
            {/* 底部渐隐:示意截图在窗口内续滚,非完整高度(渐入窗口底色 card) */}
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-card to-transparent" />
          </div>
        ) : (
          <img
            src={image.src}
            alt={image.alt}
            loading="lazy"
            className={`block h-auto transition-transform duration-500 group-hover:scale-[1.02] ${
              image.narrow ? 'mx-auto w-[50%]' : 'w-full'
            }`}
          />
        )}
      </div>
    </figure>
  );
}

/**
 * 欢迎页场景展示(id="scenarios",Welcome 页二期挂载位):四张场景卡单列布局,
 * 沿用落地页大圆角白底卡语言;视觉走设计系统 token,hover 抬升与品牌蓝晕影。
 * 素材为本地截图(src/assets/Images/scenarios),后续替换为真实产品截图。
 */
export function ScenariosSection() {
  const brand = getCapabilities().getProductBrand().value;
  return (
    <section id="scenarios" className="scroll-mt-28">
      <div className="mb-10 text-center">
        <h2 className="text-2xl font-semibold text-foreground">协作是如何发生的</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {`由 ${brand.name} 驱动的真实协作场景:发现、协作、组网,让 Bots 一起处理复杂任务。`}
        </p>
      </div>
      <div className="space-y-6">
        {SCENARIOS.map((scenario) => {
          const Icon = scenario.icon;
          return (
            <div
              key={scenario.id}
              className="rounded-[28px] border border-border bg-card p-6 text-center shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-xl hover:shadow-primary/10 sm:p-8"
            >
              <span className="mx-auto flex size-11 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Icon className="size-5" aria-hidden />
              </span>
              <h3 className="mt-4 text-xl font-semibold leading-tight text-foreground sm:text-2xl">
                {scenario.title}
              </h3>
              <p className="mt-3 text-sm text-muted-foreground">{scenario.description}</p>
              <div className="mt-6 grid grid-cols-1 gap-4">
                {scenario.images.map((image) => (
                  <ScenarioFrame key={image.src} image={image} windowTitle={brand.name} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
