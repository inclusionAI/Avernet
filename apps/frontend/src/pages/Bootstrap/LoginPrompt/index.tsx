import Button from '@/components/Button';
import { ShieldCheck } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import {
  getLoginPromptState,
  hideLoginPrompt,
  LoginPromptState,
  subscribeLoginPrompt,
} from './loginPromptState';

export default function LoginPrompt() {
  const [state, setState] = useState<LoginPromptState>(getLoginPromptState());

  useEffect(() => {
    return subscribeLoginPrompt(() => setState({ ...getLoginPromptState() }));
  }, []);

  if (!state.visible) return null;

  const handleLogin = () => {
    if (state.loginUrl) {
      window.location.href = state.loginUrl;
    } else {
      window.location.reload();
    }
  };

  return (
    <div className="fixed inset-0 z-[100000] flex items-center justify-center bg-black/25 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-xl border border-slate-200/80 w-[420px] max-w-[90vw] overflow-hidden">
        {/* 顶部色条 */}
        <div className="h-1 bg-lavender-500 rounded-t-2xl" />

        {/* Header */}
        <div className="flex items-center gap-3 px-6 pt-5 pb-4">
          <div className="w-9 h-9 rounded-xl bg-lavender-50 flex items-center justify-center flex-shrink-0">
            <ShieldCheck size={18} className="text-lavender-500" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-slate-800">身份验证</h2>
            <p className="text-xs text-slate-400 mt-0.5">需要登录后才能继续访问</p>
          </div>
        </div>

        {/* Body */}
        <div className="px-6 pb-4">
          <p className="text-sm text-slate-500 leading-relaxed">{state.help}</p>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-slate-100">
          <Button variant="default" ghost onClick={hideLoginPrompt}>
            取消
          </Button>
          <Button variant="primary" onClick={handleLogin}>
            前往登录
          </Button>
        </div>
      </div>
    </div>
  );
}
