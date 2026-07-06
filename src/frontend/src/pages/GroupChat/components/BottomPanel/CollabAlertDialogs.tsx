/**
 * CollabAlertDialogs - 加入/退出协作确认弹窗
 *
 * 从 BottomPanel 中提取的弹窗组件，减少主组件行数
 */

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { AlertCircle, Info } from 'lucide-react';
import React from 'react';

interface CollabAlertDialogsProps {
  showJoinDialog: boolean;
  setShowJoinDialog: (v: boolean) => void;
  showLeaveDialog: boolean;
  setShowLeaveDialog: (v: boolean) => void;
  onJoin: () => Promise<void>;
  onLeave: () => Promise<void>;
  /** 是否为任务协作群（主从模式） */
  isManagerWorker?: boolean;
}

const CollabAlertDialogs: React.FC<CollabAlertDialogsProps> = ({
  showJoinDialog,
  setShowJoinDialog,
  showLeaveDialog,
  setShowLeaveDialog,
  onJoin,
  onLeave,
  isManagerWorker,
}) => {
  return (
    <>
      {/* 加入协作确认弹窗 */}
      <AlertDialog open={showJoinDialog} onOpenChange={setShowJoinDialog}>
        <AlertDialogContent className="max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-center text-base font-medium">
              用户加入协作群确认
            </AlertDialogTitle>
          </AlertDialogHeader>
          <div className="space-y-4">
            <div>
              <h4 className="text-sm font-medium text-slate-800 mb-2 flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-lavender-500" />
                确认后：
              </h4>
              <ul className="space-y-2 text-[13px] text-slate-600 pl-3.5">
                <li className="relative pl-2 before:content-['·'] before:absolute before:left-0 before:text-slate-400">
                  您的所有发言将以用户身份发送；
                </li>
              </ul>
            </div>
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
              <h4 className="text-sm font-medium text-amber-800 mb-2 flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                其他须知：
              </h4>
              {isManagerWorker ? (
                <ul className="space-y-2 text-[13px] text-amber-700 pl-3.5">
                  <li className="relative pl-2 before:content-['·'] before:absolute before:left-0 before:text-amber-500">
                    您可与主节点Bot进行对话，由主节点Bot负责任务分发、状态推进、质量核验
                  </li>
                  <li className="relative pl-2 before:content-['·'] before:absolute before:left-0 before:text-amber-500">
                    顶栏切换至用户视角时，您可见的任务协作群的会话上下文，将与主节点Bot可见的上下文保持一致
                  </li>
                </ul>
              ) : (
                <ul className="space-y-2 text-[13px] text-amber-700 pl-3.5">
                  <li className="relative pl-2 before:content-['·'] before:absolute before:left-0 before:text-amber-500">
                    选择加入该协作群，即代表您认可被其他Bot主动@或间接询问等情况；
                  </li>
                  <li className="relative pl-2 before:content-['·'] before:absolute before:left-0 before:text-amber-500">
                    如您无法及时回复或参与协同，请谨慎使用用户身份；
                  </li>
                  <li className="relative pl-2 before:content-['·'] before:absolute before:left-0 before:text-amber-500">
                    加入后可选择适当时机退出协作；
                  </li>
                </ul>
              )}
            </div>
          </div>
          <AlertDialogFooter className="mt-6">
            <AlertDialogCancel
              onClick={() => setShowJoinDialog(false)}
              data-aspm-click="ca114903.da194169"
              data-aspm-desc="GroupChat-取消加入协作"
              data-aspm-param={``}
              data-aspm-expo
            >
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={onJoin}
              className="bg-blue-600 hover:bg-blue-700"
              data-aspm-click="ca114903.da194170"
              data-aspm-desc="GroupChat-确认加入协作"
              data-aspm-param={``}
              data-aspm-expo
            >
              确认加入
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 退出协作确认弹窗 */}
      <AlertDialog open={showLeaveDialog} onOpenChange={setShowLeaveDialog}>
        <AlertDialogContent className="max-w-lg">
          <AlertDialogHeader>
            <div className="flex justify-center mb-2">
              <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
                <AlertCircle className="w-6 h-6 text-red-500" />
              </div>
            </div>
            <AlertDialogTitle className="text-center text-base font-medium">
              确认退出协作？
            </AlertDialogTitle>
          </AlertDialogHeader>
          <div className="text-center text-[13px] text-slate-600 mb-4">
            退出后，您将失去
            <span className="text-red-500 font-medium">用户身份发言</span>
            权限，您的 Bot 将恢复为独立协作者状态。
          </div>
          <div className="space-y-3">
            <div className="bg-red-50 border border-red-100 rounded-lg p-4">
              <h4 className="text-sm font-medium text-red-800 mb-2 flex items-center gap-2">
                <AlertCircle className="w-4 h-4" />
                退出后影响：
              </h4>
              <ul className="space-y-2 text-[13px] text-red-700 pl-5">
                <li className="relative before:content-['·'] before:absolute before:-left-3 before:text-red-500">
                  您将无法以用户身份在群内发言
                </li>
                <li className="relative before:content-['·'] before:absolute before:-left-3 before:text-red-500">
                  群成员将无法再@您或直接与您协作
                </li>
              </ul>
            </div>
            <div className="bg-blue-50 border border-blue-100 rounded-lg p-4 flex gap-2">
              <Info className="w-4 h-4 text-blue-500 flex-shrink-0 mt-0.5" />
              <p className="text-[13px] text-blue-700">
                退出协作是一个慎重操作。如只是暂时离开，建议保持当前状态。
              </p>
            </div>
          </div>
          <AlertDialogFooter className="mt-6">
            <AlertDialogCancel
              onClick={() => setShowLeaveDialog(false)}
              data-aspm-click="ca114903.da194171"
              data-aspm-desc="GroupChat-取消退出协作"
              data-aspm-param={``}
              data-aspm-expo
            >
              取消
            </AlertDialogCancel>
            <button
              type="button"
              onClick={onLeave}
              className="px-4 py-1.5 rounded-lg text-[13px] font-medium bg-red-600 text-white hover:bg-red-700 transition-colors"
              data-aspm-click="ca114903.da194172"
              data-aspm-desc="GroupChat-确认退出协作"
              data-aspm-param={``}
              data-aspm-expo
            >
              确认退出
            </button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
};

export default CollabAlertDialogs;
