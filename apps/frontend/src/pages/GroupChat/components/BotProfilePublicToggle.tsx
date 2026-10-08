/**
 * BotProfilePublicToggle - Bot 画像公开开关（内部专属）
 *
 * 从 BotInfoCard 抽出的画像公开开关行 + 开启确认弹窗，自持 confirmOpen 状态、
 * 自调 useFuse(null, botUuid) 取 fusionEnable / isUpdatingConfig / toggleFusionEnable。
 * 核心 BotInfoCard 仅通过 AppExt.slots.botProfilePublic 注入消费，组件代码不进开源闭包。
 */

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import type { BotProfilePublicSlotProps } from '@/shell/types';
import { cn } from '@/utils/utils';
import { AlertTriangle, Eye, Info, Loader2 } from 'lucide-react';
import React, { useState } from 'react';
import { useFuse } from '@/pages/GroupChat/hooks/useFuse';

const BotProfilePublicToggle: React.FC<BotProfilePublicSlotProps> = ({
  botUuid,
}) => {
  const {
    fusionEnable,
    isUpdatingConfig: isUpdatingFusionConfig,
    toggleFusionEnable: onToggleFusionEnable,
  } = useFuse(null, null, botUuid);
  const [fusionConfirmOpen, setFusionConfirmOpen] = useState(false);

  return (
    <>
      {/* Bot画像公开 - 开关（内部专属，botProfilePublic 门控） */}
      <div className="px-4 py-3 border-t border-slate-100">
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-700">Bot画像公开</span>
          <div className="flex items-center gap-1.5">
            {isUpdatingFusionConfig && (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-400" />
            )}
            <button
              type="button"
              onClick={() => {
                if (fusionEnable) {
                  // 关闭时直接执行
                  onToggleFusionEnable?.(false);
                } else {
                  // 开启时弹出确认弹窗
                  setFusionConfirmOpen(true);
                }
              }}
              disabled={
                isUpdatingFusionConfig ||
                fusionEnable === null ||
                fusionEnable === undefined
              }
              className={cn(
                'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
                fusionEnable ? 'bg-lavender-500' : 'bg-slate-200',
                (isUpdatingFusionConfig ||
                  fusionEnable === null ||
                  fusionEnable === undefined) &&
                  'opacity-50 cursor-not-allowed',
              )}
              data-aspm-click="ca114903.da194154"
              data-aspm-desc="GroupChat-切换画像公开"
              data-aspm-param={``}
              data-aspm-expo
            >
              <span
                className={cn(
                  'inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform',
                  fusionEnable ? 'translate-x-5' : 'translate-x-0.5',
                )}
              />
            </button>
          </div>
        </div>
      </div>

      {/* Bot画像公开确认弹窗 */}
      <AlertDialog open={fusionConfirmOpen} onOpenChange={setFusionConfirmOpen}>
        <AlertDialogContent className="max-w-lg">
          <AlertDialogHeader>
            <div className="flex justify-center mb-2">
              <div className="w-12 h-12 rounded-full bg-lavender-50 flex items-center justify-center">
                <Eye className="w-6 h-6 text-lavender-600" />
              </div>
            </div>
            <AlertDialogTitle className="text-center text-base font-medium">
              开启 Bot 画像公开？
            </AlertDialogTitle>
            <AlertDialogDescription className="text-center text-[13px] text-slate-500">
              开启后，其他协作群成员可感知并使用当前 Bot 的能力
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3">
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
              <h4 className="text-sm font-medium text-blue-800 mb-2 flex items-center gap-2">
                <Info className="w-4 h-4" />
                说明
              </h4>
              <ul className="space-y-2 text-[13px] text-blue-700 pl-5">
                <li className="relative before:content-['·'] before:absolute before:-left-3 before:text-blue-500">
                  <span className="font-medium text-blue-800">适用场景：</span>
                  当前Bot
                  定位为服务团队内部协作，能力被协作群内其他用户快速感知与使用时无风险（如定位为案件分析，掌握必备案件分析能力与知识点）
                </li>
                <li className="relative before:content-['·'] before:absolute before:-left-3 before:text-blue-500">
                  <span className="font-medium text-blue-800">调用通知：</span>
                  用户使用智能问答时，如引用当前 Bot
                  画像内容，您可收到事后调用通知提示，方便追溯
                </li>
              </ul>
            </div>
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
              <h4 className="text-sm font-medium text-amber-800 mb-2 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" />
                风险告知
              </h4>
              <ul className="space-y-2 text-[13px] text-amber-700 pl-5">
                <li className="relative before:content-['·'] before:absolute before:-left-3 before:text-amber-500">
                  个人提效场景不建议打开，个人知识/隐私可能被其他协作群成员获取
                </li>
              </ul>
            </div>
          </div>
          <AlertDialogFooter className="mt-6">
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                onToggleFusionEnable?.(true);
                setFusionConfirmOpen(false);
              }}
              data-aspm-click="ca114903.da194155"
              data-aspm-desc="GroupChat-确认开启画像公开"
              data-aspm-param={``}
              data-aspm-expo
            >
              我已了解风险，确认开启
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
};

export default BotProfilePublicToggle;
