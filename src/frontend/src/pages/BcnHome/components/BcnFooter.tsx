/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * BcnFooter - 产品首页页脚（logo + GitHub 链接）
 *
 * 注：本落地页像素级还原设计稿，使用设计稿精确色（本页局部例外）。
 */

import { Bot } from 'lucide-react';
import React from 'react';

const GITHUB_URL = 'https://github.com/inclusionAI/Avernet';

const BcnFooter: React.FC = () => {
  return (
    <footer className="mt-4 border-t border-[#e5e9f2] py-8">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#1d4ed8] text-white shadow-sm">
            <Bot className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-semibold text-[#1a2332]">Avernet</p>
            <p className="text-xs tracking-[0.08em] text-[#8b95a5]">
              多智能体协作平台
            </p>
          </div>
        </div>
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noreferrer"
          className="text-sm font-medium text-[#1d4ed8] transition-colors hover:text-[#1e40af]"
        >
          GitHub
        </a>
      </div>
    </footer>
  );
};

export default BcnFooter;
