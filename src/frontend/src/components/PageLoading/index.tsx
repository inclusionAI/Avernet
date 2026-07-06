/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { Shield } from 'lucide-react';
import React from 'react';

interface PageLoadingProps {
  fullScreen?: boolean;
}

export default function PageLoading({ fullScreen = true }: PageLoadingProps) {
  return (
    <div
      data-testid="bigfish-initial-loading"
      className={
        fullScreen
          ? 'fixed inset-0 z-50 flex items-center justify-center bg-slate-50'
          : 'w-full h-full flex items-center justify-center bg-slate-50'
      }
    >
      <div className="flex flex-col items-center gap-3">
        <div className="w-11 h-11 rounded-xl bg-lavender-100 flex items-center justify-center">
          <Shield size={22} className="text-lavender-600" />
        </div>
        <div className="w-5 h-5 rounded-full border-2 border-slate-200 border-t-lavender-500 animate-spin" />
        <span className="text-xs text-slate-400 tracking-wide">加载中...</span>
      </div>
    </div>
  );
}
