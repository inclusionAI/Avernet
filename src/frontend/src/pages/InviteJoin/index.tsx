/**
 * InviteJoin - 邀请链接加入页面
 *
 * 通过邀请链接加入 Group 或 Session，以弹窗形式展示
 * 路由：/invite/:type/:token
 * type: "groups" | "sessions"
 */

import Button from '@/components/Button';
import * as BcnController from '@/services/backend-api/BcnController';
import { history, useParams } from '@umijs/max';
import { AlertTriangle, Check, Loader2 } from 'lucide-react';
import React, { useCallback, useEffect, useState } from 'react';

type JoinState = 'loading' | 'confirm' | 'joining' | 'success' | 'error';

const InviteJoin: React.FC = () => {
  const params = useParams<{ type: string; token: string }>();
  const { type, token } = params;

  const [state, setState] = useState<JoinState>('loading');
  const [errorMsg, setErrorMsg] = useState('');
  const [result, setResult] = useState<BcnController.JoinResponse | null>(null);

  const isValidType = type === 'groups' || type === 'sessions';
  const targetLabel = type === 'groups' ? '协作群' : '会话';

  useEffect(() => {
    if (!type || !token || !isValidType) {
      setState('error');
      setErrorMsg('无效的邀请链接');
      return;
    }
    setState('confirm');
  }, [type, token, isValidType]);

  const redirectToChat = useCallback(
    async (res: BcnController.JoinResponse) => {
      try {
        const me = await BcnController.getMe({ skipErrorHandler: true });
        const staffNo = me?.staff_no;
        const params = new URLSearchParams();
        if (staffNo) params.set('bot_uuid', `human_${staffNo}`);

        if (res.target_type === 'group') {
          params.set('id', res.target_id);
        } else if (res.target_type === 'session') {
          const groupId = res.target_id.split(':')[0] || '';
          if (groupId) params.set('id', groupId);
          params.set('session', res.target_id);
        }

        const query = params.toString() ? `?${params.toString()}` : '';
        setTimeout(() => {
          history.push(`/bcn/chat/session${query}`);
        }, 1500);
      } catch {
        setTimeout(() => {
          history.push('/bcn/chat/session');
        }, 1500);
      }
    },
    [],
  );

  const handleJoin = useCallback(async () => {
    setState('joining');
    try {
      const res = await BcnController.joinByInviteToken({
        type: type as 'groups' | 'sessions',
        invite_token: token,
      });

      if (res.error) {
        setState('error');
        setErrorMsg(res.error);
        return;
      }

      setResult(res);
      setState('success');
      redirectToChat(res);
    } catch (err: any) {
      setState('error');
      setErrorMsg(
        err?.data?.error ||
          err?.response?.data?.error ||
          '加入失败，请检查网络后重试',
      );
    }
  }, [type, token, redirectToChat]);

  const handleCancel = useCallback(() => {
    history.push('/home');
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* 遮罩 */}
      <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" />

      {/* 弹窗 */}
      <div className="relative w-80 bg-white rounded-xl shadow-xl p-8">
        {/* 加载中 */}
        {state === 'loading' && (
          <div className="flex flex-col items-center gap-4 py-8">
            <Loader2 className="w-8 h-8 text-lavender-500 animate-spin" />
            <p className="text-sm text-slate-500">正在验证邀请链接...</p>
          </div>
        )}

        {/* 确认 */}
        {state === 'confirm' && (
          <div className="flex flex-col items-center gap-4">
            <div className="w-12 h-12 rounded-full bg-lavender-50 flex items-center justify-center">
              <svg
                className="w-6 h-6 text-lavender-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197m13.5-9a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0z"
                />
              </svg>
            </div>
            <div className="text-center">
              <h2 className="text-lg font-semibold text-slate-800">
                加入{targetLabel}
              </h2>
              <p className="text-sm text-slate-500 mt-2">
                你被邀请加入一个{targetLabel}
              </p>
            </div>
            <div className="flex gap-3 w-full mt-2">
              <Button variant="primary" onClick={handleJoin} className="flex-1">
                加入
              </Button>
              <Button
                variant="default"
                ghost
                onClick={handleCancel}
                className="flex-1"
              >
                取消
              </Button>
            </div>
          </div>
        )}

        {/* 加入中 */}
        {state === 'joining' && (
          <div className="flex flex-col items-center gap-4 py-8">
            <Loader2 className="w-8 h-8 text-lavender-500 animate-spin" />
            <p className="text-sm text-slate-500">正在加入{targetLabel}...</p>
          </div>
        )}

        {/* 成功 */}
        {state === 'success' && (
          <div className="flex flex-col items-center gap-4 py-4">
            <div className="w-12 h-12 rounded-full bg-green-50 flex items-center justify-center">
              <Check className="w-6 h-6 text-green-500" />
            </div>
            <div className="text-center">
              <h2 className="text-lg font-semibold text-slate-800">
                {result?.already_member ? '你已在群中' : '加入成功'}
              </h2>
              <p className="text-sm text-slate-500 mt-2">
                即将跳转到{targetLabel}...
              </p>
            </div>
          </div>
        )}

        {/* 失败 */}
        {state === 'error' && (
          <div className="flex flex-col items-center gap-4 py-4">
            <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
              <AlertTriangle className="w-6 h-6 text-red-500" />
            </div>
            <div className="text-center">
              <h2 className="text-lg font-semibold text-slate-800">加入失败</h2>
              <p className="text-sm text-slate-500 mt-2">{errorMsg}</p>
            </div>
            <Button
              variant="default"
              ghost
              onClick={handleCancel}
              className="mt-2"
            >
              关闭
            </Button>
          </div>
        )}
      </div>
    </div>
  );
};

export default InviteJoin;
