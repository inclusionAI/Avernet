import { CommunityPage } from '@/components/Community/CommunityPage';

/**
 * 实验室 → 社区页。
 * 「实验室」是一级导航，实验室落地页 (/lab) 为功能卡网格，点社区的「开始体验」进入此页。
 * 社区右上角的齿轮按钮（Bot 访问社区配置）在 CommunityPage 内承载。
 */
export default function LabCommunityPage() {
  return <CommunityPage />;
}
