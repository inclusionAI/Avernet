import { SquarePageShell } from '@/components/CollaborationSquare/SquarePageShell';
import { useLocation } from '@umijs/max';

export default function CollaborationSquareBotsPage() {
  const location = useLocation();
  // 审批入口深链（?panel=friend-requests）直达展开的好友申请面板；仅决定初始展开态。
  const friendRequestsDefaultOpen =
    new URLSearchParams(location.search).get('panel') === 'friend-requests';
  return <SquarePageShell resource="bot" friendRequestsDefaultOpen={friendRequestsDefaultOpen} />;
}
