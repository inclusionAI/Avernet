import type {
  CommunityAuthor,
  CommunityPostLocation,
  CommunityReply,
  CommunityTopic,
  ForumTargetRef,
  InteractionSummary,
} from '@/domain/community/types';
import type { ReplyComposerPayload } from './ReplyComposer';

export interface CommunityTopicDetailProps {
  topic: CommunityTopic | null;
  replies: CommunityReply[];
  replyTotal?: number;
  loading: boolean;
  error: string | null;
  closing: boolean;
  deleting: boolean;
  helpfulPostId: string | null;
  onBack: () => void;
  currentUserId?: string;
  viewer?: { displayName: string; avatarUrl?: string };
  targetSummary?: InteractionSummary;
  /** Actor-target summary used only to render the current viewer's liked/favorited state. */
  viewerTopicSummary?: InteractionSummary;
  getAuthorSummary?: (author: CommunityAuthor) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityAuthor) => void;
  followAuthor?: (author: CommunityAuthor) => Promise<boolean>;
  authorFollowLoading?: (author: CommunityAuthor) => boolean;
  authorFollowed?: (author: CommunityAuthor) => boolean;
  likePending?: boolean;
  favoritePending?: boolean;
  onLike?: (target: ForumTargetRef) => void;
  onFavorite?: (target: ForumTargetRef) => void;
  getReplySummary?: (postId: string) => InteractionSummary | undefined;
  getReplyViewerSummary?: (postId: string) => InteractionSummary | undefined;
  replyLikePending?: (postId: string) => boolean;
  onLikeReply?: (postId: string) => void;
  onCloseTopic: () => Promise<boolean>;
  onEditTopic: () => void;
  onDeleteTopic: () => Promise<boolean>;
  onSetPostHelpful: (postId: string, helpful: boolean) => Promise<boolean>;
  // 连续回帖窗口；页码只用于后端请求和楼层偏移
  replyLoading: boolean;
  replyError: string | null;
  replySaving?: boolean;
  onSubmitReply?: (payload: ReplyComposerPayload) => Promise<boolean>;
  firstReplyPage: number;
  lastReplyPage: number;
  failedReplyPage?: number | null;
  replyPageCount: number;
  replyPageSize: number;
  onGoToReplyPage: (page: number) => Promise<void>;
  /** Initial navigation target resolved by the backend for personal-center links. */
  pendingPostLocation?: CommunityPostLocation | null;
  onClearPendingPostLocation?: () => void;
}
