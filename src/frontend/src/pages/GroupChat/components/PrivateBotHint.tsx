/**
 * PrivateBotHint - 隐身模式提示组件
 *
 * 当 Bot 处于隐身模式时显示提示信息
 */

import { EyeOff, WifiOff } from 'lucide-react';
import React from 'react';
import { BotVisibility } from '../types';

interface PrivateBotHintProps {
  className?: string;
  visibility?: BotVisibility;
  dynamicStatus?: { status?: string };
}

const PrivateBotHint: React.FC<PrivateBotHintProps> = ({
  className = '',
  visibility,
  dynamicStatus,
}) => {
  const isOffline = visibility === 'offline';
  const isBotUnreachable = !isOffline && dynamicStatus?.status === 'offline';

  return (
    <div
      className={`flex flex-col items-center justify-center py-12 px-4 ${className}`}
    >
      <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center mb-3">
        {isOffline || isBotUnreachable ? (
          <WifiOff className="w-5 h-5 text-slate-400" />
        ) : (
          <EyeOff className="w-5 h-5 text-slate-400" />
        )}
      </div>
      <p className="text-sm text-slate-500 text-center leading-relaxed">
        {isOffline
          ? '该Bot处于离线，请先加入协作网络。'
          : isBotUnreachable
          ? 'Bot 与协作网络连接异常，请稍后重试或检查 Bot 状态。'
          : '该Bot为隐身模式，切换模式后可与好友Bot或公开Bot一起协作！'}
      </p>
    </div>
  );
};

export default PrivateBotHint;
