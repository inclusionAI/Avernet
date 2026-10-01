/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * BcnFooter - 产品首页页脚（logo + GitHub 链接）
 */

import React from 'react';

const GITHUB_URL = 'https://github.com/inclusionAI/Avernet';

const BcnFooter: React.FC = () => {
  return (
    <footer className="border-t border-[#e5e9f2] py-5">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-3">
          <img
            src="/Avernet-logotitle.png"
            alt="Avernet"
            className="h-9 w-auto object-contain"
          />
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
