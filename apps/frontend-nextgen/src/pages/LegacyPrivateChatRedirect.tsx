// 老版分享链接路径兜底：/private-chat/chat?id=bot_id:owner_id → /workspace/chat?id=...
// 老域名重定向在运维层完成域名/路径转换；本路由兜底直接命中新版站老路径的流量
// （含运维规则未覆盖的场景），query 原样透传给对话页解析链路（route.ts id 别名 + Guard）。
import { history, useLocation } from '@umijs/max';
import { useEffect } from 'react';

export default function LegacyPrivateChatRedirect(): null {
  const location = useLocation();
  useEffect(() => {
    history.replace(`/workspace/chat${location.search}`);
  }, [location.search]);
  return null;
}
