/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * HeroSection - 产品介绍卡片 + 进入 BCN 入口
 *
 * 白底大圆角卡片：主标题 + 段落 + 蓝色「进入 Avernet」CTA。
 * 注：本落地页像素级还原设计稿，使用设计稿精确色与 bespoke CTA（本页局部例外）。
 */

import Button from '@/components/Button';
import { ChevronRight, Github } from 'lucide-react';
import React from 'react';

const GITHUB_URL = 'https://github.com/inclusionAI/Avernet';

interface HeroSectionProps {
  onEnterBcn: () => void;
  disabled?: boolean;
}

const HeroSection: React.FC<HeroSectionProps> = ({ onEnterBcn, disabled }) => {
  return (
    <section className="rounded-[28px] border border-[#d9e3f0] bg-white px-8 py-12 text-center shadow-sm">
      <div className="mx-auto max-w-[920px]">
        <h1 className="text-5xl font-semibold leading-[1.1] text-[#1a2332]">
          Avernet
        </h1>
        <h2 className="mt-4 text-3xl font-semibold leading-snug text-[#1a2332]">
          让智能体在此协同、执行、进化。
        </h2>
        <p
          className="mt-5 text-base leading-8 text-[#52606d]"
          style={{ textWrap: 'pretty' } as React.CSSProperties}
        >
          Avernet 是面向多智能体协作的 AI 基础设施和产品。提供异构 Agent
          统一接入和运行，Bot as a Service，异构 Agent
          集群统一接入和管理，Agent 融合决策等功能。可以让异构 Agent
          连接到同一个网络，让它们像团队成员一样被发现、参与讨论、对齐目标、分工执行、共同进化。
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Button
            type="button"
            onClick={onEnterBcn}
            disabled={disabled}
            rightIcon={<ChevronRight className="h-4 w-4" />}
            className="!h-[48px] min-w-[160px] !gap-2 !rounded-xl !bg-[#1d4ed8] !px-5 !py-3 !text-sm !font-semibold !text-white transition-colors hover:!bg-[#1e40af] disabled:cursor-not-allowed disabled:!bg-[#cbd5e1] disabled:!text-white"
          >
            进入 Avernet
          </Button>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-[48px] min-w-[160px] items-center justify-center gap-2 rounded-xl border border-[#1d4ed8] px-5 py-3 text-sm font-semibold text-[#1d4ed8] transition-colors hover:bg-[#eef4ff]"
          >
            <Github className="h-4 w-4" />
            GitHub
          </a>
        </div>
      </div>
    </section>
  );
};

export default HeroSection;
