import type { CommunityTopicType } from '@/domain/community/types';
import { ChartLine, CircleHelp, MessageCircle, Megaphone, Share2 } from 'lucide-react';

const TOPIC_TYPE_ICONS: Record<CommunityTopicType, typeof MessageCircle> = {
  discussion: MessageCircle,
  share: Share2,
  poll: ChartLine,
  help: CircleHelp,
  notice: Megaphone,
};

export function TopicTypeIcon({
  topicType,
  className,
}: {
  topicType: CommunityTopicType;
  className?: string;
}) {
  const Icon = TOPIC_TYPE_ICONS[topicType];
  return <Icon className={className} aria-hidden />;
}
